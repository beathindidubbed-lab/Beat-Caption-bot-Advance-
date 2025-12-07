import json
import os
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import asyncio
from aiohttp import web
import psycopg
from psycopg_pool import AsyncConnectionPool

# Bot credentials and config
API_ID = int(os.getenv("API_ID", "28318819"))
API_HASH = os.getenv("API_HASH", "2996bb8e28a5bb09b56c16f6ca764c10")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8476862156:AAEMRJaLJ9PiN-8thOBr3hqGK2-PjzmWG_c")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "10000"))

# Authorized users (comma-separated IDs in environment variable)
auth_users_str = os.getenv("AUTHORIZED_USERS", "").strip()
if auth_users_str:
    try:
        AUTHORIZED_USERS = [int(uid.strip()) for uid in auth_users_str.split(",") if uid.strip()]
    except ValueError:
        print(f"⚠️ Warning: Invalid AUTHORIZED_USERS format: {auth_users_str}")
        AUTHORIZED_USERS = []
else:
    AUTHORIZED_USERS = []

print(f"🔐 Authorization: {AUTHORIZED_USERS if AUTHORIZED_USERS else 'Open to all users'}", flush=True)

ALL_QUALITIES = ["480p", "720p", "1080p", "4K", "2160p"]

# Database pool
db_pool = None

# In-memory cache for progress
progress = {
    "target_chat_id": None,
    "season": 1,
    "episode": 1,
    "total_episode": 1,
    "video_count": 0,
    "selected_qualities": ["480p", "720p", "1080p"],
    "base_caption": "<b>Anime</b> - <i>@Beat_Anime_Hindi</i>\n"
                    "Season {season} - Episode {episode} ({total_episode}) - {quality}\n"
                    "<blockquote>Don't miss this episode!</blockquote>"
}

# Pyrogram app
app = Client("auto_caption_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Track users waiting for input and last messages
waiting_for_input = {}
last_bot_messages = {}

# Lock to avoid concurrent uploads
upload_lock = asyncio.Lock()


async def init_db():
    """Initialize database connection and create table if not exists"""
    global db_pool
    
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable not set!")
        raise ValueError("DATABASE_URL is required")
    
    print(f"Connecting to database...")
    db_pool = AsyncConnectionPool(
        DATABASE_URL, 
        min_size=1, 
        max_size=5,  # Reduced for serverless
        open=False,
        kwargs={
            "autocommit": True,  # Auto-commit for better connection handling
            "prepare_threshold": None,  # Disable prepared statements for serverless
        }
    )
    await db_pool.open()
    print("Database connection pool opened")
    
    async with db_pool.connection() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_progress (
                id INTEGER PRIMARY KEY DEFAULT 1,
                target_chat_id BIGINT,
                season INTEGER DEFAULT 1,
                episode INTEGER DEFAULT 1,
                total_episode INTEGER DEFAULT 1,
                video_count INTEGER DEFAULT 0,
                selected_qualities TEXT DEFAULT '480p,720p,1080p',
                base_caption TEXT,
                CONSTRAINT single_row CHECK (id = 1)
            )
        """)
        
        # Insert default row if not exists
        await conn.execute("""
            INSERT INTO bot_progress (id, base_caption)
            VALUES (1, %s)
            ON CONFLICT (id) DO NOTHING
        """, (progress["base_caption"],))
    
    print("Database table created/verified")
    
    # Load progress from database
    await load_progress()


async def load_progress():
    """Load progress from database with retry logic"""
    global progress
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with db_pool.connection() as conn:
                row = await conn.execute("""
                    SELECT target_chat_id, season, episode, total_episode, 
                           video_count, selected_qualities, base_caption
                    FROM bot_progress WHERE id = 1
                """)
                data = await row.fetchone()
                
                if data:
                    progress["target_chat_id"] = data[0]
                    progress["season"] = data[1]
                    progress["episode"] = data[2]
                    progress["total_episode"] = data[3]
                    progress["video_count"] = data[4]
                    progress["selected_qualities"] = data[5].split(",") if data[5] else []
                    progress["base_caption"] = data[6] if data[6] else progress["base_caption"]
                    print(f"Progress loaded from database: Season {progress['season']}, Episode {progress['episode']}")
                return  # Success
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"⚠️ Database load failed (attempt {attempt + 1}/{max_retries}): {e}", flush=True)
                await asyncio.sleep(1)
            else:
                print(f"❌ Database load failed after {max_retries} attempts: {e}", flush=True)
                raise


async def save_progress():
    """Save progress to database with retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with db_pool.connection() as conn:
                await conn.execute("""
                    UPDATE bot_progress SET
                        target_chat_id = %s,
                        season = %s,
                        episode = %s,
                        total_episode = %s,
                        video_count = %s,
                        selected_qualities = %s,
                        base_caption = %s
                    WHERE id = 1
                """, (
                    progress["target_chat_id"],
                    progress["season"],
                    progress["episode"],
                    progress["total_episode"],
                    progress["video_count"],
                    ",".join(progress["selected_qualities"]),
                    progress["base_caption"]
                ))
                return  # Success, exit function
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"⚠️ Database save failed (attempt {attempt + 1}/{max_retries}): {e}", flush=True)
                await asyncio.sleep(1)  # Wait before retry
            else:
                print(f"❌ Database save failed after {max_retries} attempts: {e}", flush=True)
                raise


def is_authorized(user_id):
    """Check if user is authorized to use the bot"""
    if not AUTHORIZED_USERS:
        return True
    return user_id in AUTHORIZED_USERS


async def delete_last_message(client, chat_id):
    """Delete the last bot message if it exists"""
    if chat_id in last_bot_messages:
        try:
            await client.delete_messages(chat_id, last_bot_messages[chat_id])
        except Exception:
            pass
        del last_bot_messages[chat_id]


def get_menu_markup():
    buttons = [
        [InlineKeyboardButton("Preview Caption", callback_data="preview")],
        [InlineKeyboardButton("Set Caption", callback_data="set_caption")],
        [
            InlineKeyboardButton("Set Season", callback_data="set_season"),
            InlineKeyboardButton("Set Episode", callback_data="set_episode")
        ],
        [InlineKeyboardButton("Set Total Episode", callback_data="set_total_episode")],
        [InlineKeyboardButton("Quality Settings", callback_data="quality_menu")],
        [InlineKeyboardButton("Set Target Channel", callback_data="set_target_channel")],
        [InlineKeyboardButton("Reset Episode", callback_data="reset")],
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(buttons)


def get_quality_markup():
    buttons = []
    for quality in ALL_QUALITIES:
        is_selected = quality in progress["selected_qualities"]
        checkmark = "✅ " if is_selected else ""
        buttons.append([InlineKeyboardButton(
            f"{checkmark}{quality}",
            callback_data=f"toggle_quality_{quality}"
        )])
    buttons.append([InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")])
    return InlineKeyboardMarkup(buttons)


@app.on_message(filters.private & filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id
    
    if not is_authorized(user_id):
        await message.reply("❌ You are not authorized to use this bot.")
        return
    
    # Delete the command message
    try:
        await message.delete()
    except Exception:
        pass
    
    # Delete previous bot message if exists
    await delete_last_message(client, message.chat.id)
    
    target_status = f"✅ Set: {progress['target_chat_id']}" if progress['target_chat_id'] else "❌ Not Set"
    
    welcome_text = (
        "👋 Welcome to the Anime Caption Bot!\n\n"
        f"<b>Target Channel:</b> {target_status}\n\n"
        "Use the buttons below to manage captions and episodes."
    )
    
    # Only send if no previous message or it was deleted
    sent = await client.send_message(
        message.chat.id, 
        welcome_text, 
        parse_mode=ParseMode.HTML,
        reply_markup=get_menu_markup()
    )
    last_bot_messages[message.chat.id] = sent.id


@app.on_callback_query()
async def handle_buttons(client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    if not is_authorized(user_id):
        try:
            await callback_query.answer("❌ You are not authorized to use this bot.", show_alert=True)
        except Exception:
            pass
        return
    
    try:
        await callback_query.answer()
    except Exception:
        pass

    chat_id = callback_query.message.chat.id
    data = callback_query.data

    await delete_last_message(client, chat_id)

    if data == "preview":
        quality = progress["selected_qualities"][progress["video_count"] % len(progress["selected_qualities"])] if progress["selected_qualities"] else "N/A"
        preview_text = progress["base_caption"] \
            .replace("{season}", f"{progress['season']:02}") \
            .replace("{episode}", f"{progress['episode']:02}") \
            .replace("{total_episode}", f"{progress['total_episode']:02}") \
            .replace("{quality}", quality)

        target_status = f"✅ {progress['target_chat_id']}" if progress['target_chat_id'] else "❌ Not Set"

        sent = await callback_query.message.reply(
            f"📝 <b>Preview Caption:</b>\n\n{preview_text}\n\n<b>Current Settings:</b>\n"
            f"Target Channel: {target_status}\n"
            f"Season: {progress['season']}\n"
            f"Episode: {progress['episode']}\n"
            f"Total Episode: {progress['total_episode']}\n"
            f"Selected Qualities: {', '.join(progress['selected_qualities']) if progress['selected_qualities'] else 'None'}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_caption":
        waiting_for_input[user_id] = "caption"
        sent = await callback_query.message.reply(
            "✏️ Please send the new base caption now (HTML supported).\n"
            "You can use <code>{season}</code>, <code>{episode}</code>, <code>{total_episode}</code>, and <code>{quality}</code> as placeholders.",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_season":
        waiting_for_input[user_id] = "season"
        sent = await callback_query.message.reply(
            f"✏️ Current season: <b>{progress['season']}</b>\n\n"
            "Please send the new season number (e.g., 1, 2, 3, etc.).",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_episode":
        waiting_for_input[user_id] = "episode"
        sent = await callback_query.message.reply(
            f"✏️ Current episode: <b>{progress['episode']}</b>\n\n"
            "Please send the new episode number for this season (e.g., 1, 2, 3, etc.).",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_total_episode":
        waiting_for_input[user_id] = "total_episode"
        sent = await callback_query.message.reply(
            f"✏️ Current total episode: <b>{progress['total_episode']}</b>\n\n"
            "Please send the new total episode number (e.g., 1, 2, 3, etc.).",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_target_channel":
        waiting_for_input[user_id] = "set_channel"
        sent = await callback_query.message.reply(
            "📢 <b>Set Target Channel</b>\n\n"
            "<b>Option 1:</b> Forward any message from the target channel\n"
            "<b>Option 2:</b> Send the channel ID (e.g., <code>-1001234567890</code>)\n"
            "<b>Option 3:</b> Send the channel username (e.g., <code>@yourchannel</code>)\n\n"
            "Make sure the bot is an admin in that channel!",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "quality_menu":
        sent = await callback_query.message.reply(
            "🎬 <b>Quality Settings</b>\n\n"
            "Select which qualities should be uploaded for each episode.\n"
            "Click on a quality to toggle it on/off.\n\n"
            f"<b>Currently selected:</b> {', '.join(progress['selected_qualities']) if progress['selected_qualities'] else 'None'}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_quality_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data.startswith("toggle_quality_"):
        quality = data.replace("toggle_quality_", "")
        if quality in progress["selected_qualities"]:
            progress["selected_qualities"].remove(quality)
        else:
            progress["selected_qualities"].append(quality)
        
        progress["selected_qualities"] = [q for q in ALL_QUALITIES if q in progress["selected_qualities"]]
        await save_progress()
        
        try:
            await callback_query.message.edit_text(
                "🎬 <b>Quality Settings</b>\n\n"
                "Select which qualities should be uploaded for each episode.\n"
                "Click on a quality to toggle it on/off.\n\n"
                f"<b>Currently selected:</b> {', '.join(progress['selected_qualities']) if progress['selected_qualities'] else 'None'}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_quality_markup()
            )
        except Exception:
            pass

    elif data == "back_to_main":
        try:
            await callback_query.message.delete()
        except Exception:
            pass
        
        target_status = f"✅ Set: {progress['target_chat_id']}" if progress['target_chat_id'] else "❌ Not Set"
        
        sent = await client.send_message(
            chat_id,
            "👋 Welcome to the Anime Caption Bot!\n\n"
            f"<b>Target Channel:</b> {target_status}\n\n"
            "Use the buttons below to manage captions and episodes.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "reset":
        progress["episode"] = 1
        progress["video_count"] = 0
        await save_progress()
        sent = await callback_query.message.reply(
            f"🔄 Episode counter reset. Starting from Episode {progress['episode']} (Season {progress['season']}).\n"
            f"Total episode counter: {progress['total_episode']}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "cancel":
        if user_id in waiting_for_input:
            del waiting_for_input[user_id]
            sent = await callback_query.message.reply("❌ Process cancelled.", reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await callback_query.message.reply("No ongoing process to cancel.", reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id


@app.on_message(filters.private & filters.forwarded)
async def handle_forwarded(client, message):
    user_id = message.from_user.id
    
    if not is_authorized(user_id):
        return
    
    chat_id = message.chat.id

    if user_id in waiting_for_input and waiting_for_input[user_id] == "set_channel":
        if message.forward_from_chat:
            channel_id = message.forward_from_chat.id
            channel_title = message.forward_from_chat.title
            
            # Try to verify bot can access the channel
            try:
                # Test if bot can access the channel
                channel_info = await client.get_chat(channel_id)
                
                progress["target_chat_id"] = channel_id
                await save_progress()
                del waiting_for_input[user_id]
                
                try:
                    await message.delete()
                except Exception:
                    pass
                
                await delete_last_message(client, chat_id)
                
                sent = await client.send_message(
                    chat_id,
                    f"✅ <b>Target channel set successfully!</b>\n\n"
                    f"<b>Channel:</b> {channel_title}\n"
                    f"<b>ID:</b> <code>{channel_id}</code>\n\n"
                    f"You can now start sending videos!",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_main")],
                        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
                    ])
                )
                last_bot_messages[chat_id] = sent.id
            except Exception as e:
                await message.reply(
                    f"❌ Cannot access this channel!\n\n"
                    f"Error: {str(e)}\n\n"
                    f"Please:\n"
                    f"1. Add bot as admin in the channel\n"
                    f"2. Give it 'Post Messages' permission\n"
                    f"3. Try again after a few seconds",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Try Again", callback_data="set_target_channel")]])
                )
        else:
            await message.reply("❌ Please forward a message from a channel, not from a user.")


@app.on_message(filters.private & filters.text & ~filters.command("start") & ~filters.forwarded)
async def receive_input(client, message):
    user_id = message.from_user.id
    
    if not is_authorized(user_id):
        return
    
    chat_id = message.chat.id

    if user_id not in waiting_for_input:
        return

    try:
        await message.delete()
    except Exception:
        pass

    await delete_last_message(client, chat_id)

    input_type = waiting_for_input[user_id]

    if input_type == "set_channel":
        # Handle channel ID or username
        channel_input = message.text.strip()
        
        try:
            # Try to get channel info
            if channel_input.startswith('@'):
                # Username format
                channel_info = await client.get_chat(channel_input)
            elif channel_input.lstrip('-').isdigit():
                # ID format
                channel_id = int(channel_input)
                channel_info = await client.get_chat(channel_id)
            else:
                await client.send_message(
                    chat_id,
                    "❌ Invalid format!\n\n"
                    "Please send:\n"
                    "• Channel ID (e.g., <code>-1001234567890</code>)\n"
                    "• Channel username (e.g., <code>@yourchannel</code>)\n"
                    "• Or forward a message from the channel",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
                )
                return
            
            progress["target_chat_id"] = channel_info.id
            await save_progress()
            del waiting_for_input[user_id]
            
            sent = await client.send_message(
                chat_id,
                f"✅ <b>Target channel set successfully!</b>\n\n"
                f"<b>Channel:</b> {channel_info.title}\n"
                f"<b>ID:</b> <code>{channel_info.id}</code>\n\n"
                f"You can now start sending videos!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_main")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
                ])
            )
            last_bot_messages[chat_id] = sent.id
            
        except Exception as e:
            await client.send_message(
                chat_id,
                f"❌ Cannot access this channel!\n\n"
                f"Error: {str(e)}\n\n"
                f"Please make sure:\n"
                f"1. The channel ID/username is correct\n"
                f"2. Bot is added as admin\n"
                f"3. Bot has 'Post Messages' permission",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data="set_target_channel")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
                ])
            )

    elif input_type == "caption":
        progress["base_caption"] = message.text
        await save_progress()
        del waiting_for_input[user_id]
        sent = await client.send_message(
            chat_id,
            "✅ Caption updated!",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif input_type == "season":
        if message.text.isdigit():
            new_season = int(message.text)
            progress["season"] = new_season
            await save_progress()
            del waiting_for_input[user_id]
            sent = await client.send_message(
                chat_id,
                f"✅ Season updated to {new_season}!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(
                chat_id,
                "❌ Please enter a valid season number (e.g., 1, 2, 3, etc.).",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[chat_id] = sent.id

    elif input_type == "episode":
        if message.text.isdigit():
            new_episode = int(message.text)
            progress["episode"] = new_episode
            await save_progress()
            del waiting_for_input[user_id]
            sent = await client.send_message(
                chat_id,
                f"✅ Episode updated to {new_episode}!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(
                chat_id,
                "❌ Please enter a valid episode number (e.g., 1, 2, 3, etc.).",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[chat_id] = sent.id

    elif input_type == "total_episode":
        if message.text.isdigit():
            new_total_episode = int(message.text)
            progress["total_episode"] = new_total_episode
            await save_progress()
            del waiting_for_input[user_id]
            sent = await client.send_message(
                chat_id,
                f"✅ Total episode updated to {new_total_episode}!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(
                chat_id,
                "❌ Please enter a valid total episode number (e.g., 1, 2, 3, etc.).",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[chat_id] = sent.id


@app.on_message(filters.private & filters.video)
async def auto_forward(client, message):
    user_id = message.from_user.id
    
    print(f"📹 Video received from user {user_id}", flush=True)
    
    if not is_authorized(user_id):
        print(f"❌ Unauthorized user: {user_id}", flush=True)
        await message.reply("❌ You are not authorized to use this bot.")
        return
    
    async with upload_lock:
        print(f"🔒 Lock acquired for video processing", flush=True)
        
        if not progress["target_chat_id"]:
            print(f"❌ Target channel not set", flush=True)
            await message.reply("❌ Target channel not set! Please set the target channel first using 'Set Target Channel' button.")
            return
        
        if not progress["selected_qualities"]:
            print(f"❌ No qualities selected", flush=True)
            await message.reply("❌ No qualities selected! Please select at least one quality from the Quality Settings menu.")
            return

        file_id = message.video.file_id
        quality = progress["selected_qualities"][progress["video_count"] % len(progress["selected_qualities"])]

        caption = progress["base_caption"] \
            .replace("{season}", f"{progress['season']:02}") \
            .replace("{episode}", f"{progress['episode']:02}") \
            .replace("{total_episode}", f"{progress['total_episode']:02}") \
            .replace("{quality}", quality)

        print(f"📤 Forwarding video to channel {progress['target_chat_id']}", flush=True)
        print(f"📝 Caption: {caption[:50]}...", flush=True)
        
        try:
            # First verify we can access the channel and initialize connection
            try:
                channel_info = await client.get_chat(progress["target_chat_id"])
                print(f"✅ Channel verified: {channel_info.title}", flush=True)
            except Exception as e:
                print(f"❌ Cannot access channel: {e}", flush=True)
                
                # Try to resolve the peer by sending a request
                try:
                    await client.send_chat_action(progress["target_chat_id"], "typing")
                    print(f"✅ Peer resolved, retrying...", flush=True)
                except:
                    await message.reply(
                        f"❌ Cannot access target channel!\n\n"
                        f"Channel ID: <code>{progress['target_chat_id']}</code>\n\n"
                        f"This usually happens when:\n"
                        f"• Bot was just added to the channel\n"
                        f"• Bot doesn't have proper permissions\n"
                        f"• Channel ID is incorrect\n\n"
                        f"<b>Solution:</b>\n"
                        f"1. Remove bot from channel\n"
                        f"2. Add bot back as admin with 'Post Messages' permission\n"
                        f"3. Set the channel again using 'Set Target Channel'\n"
                        f"4. Wait 30 seconds before sending videos",
                        parse_mode=ParseMode.HTML
                    )
                    return
            
            sent_msg = await client.send_video(
                chat_id=progress["target_chat_id"],
                video=file_id,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
            
            print(f"✅ Video forwarded successfully! Message ID: {sent_msg.id}", flush=True)

            await message.reply(
                f"✅ Video forwarded with caption:\n{caption}\n\n"
                f"Progress: {progress['video_count'] + 1}/{len(progress['selected_qualities'])} videos for this episode",
                parse_mode=ParseMode.HTML
            )

            progress["video_count"] += 1

            if progress["video_count"] >= len(progress["selected_qualities"]):
                progress["episode"] += 1
                progress["total_episode"] += 1
                progress["video_count"] = 0
                print(f"📊 Episode complete! Moving to Episode {progress['episode']}", flush=True)

            await save_progress()
            print(f"💾 Progress saved to database", flush=True)
            
        except Exception as e:
            print(f"❌ Error forwarding video: {e}", flush=True)
            import traceback
            traceback.print_exc()
            
            error_msg = str(e)
            if "CHAT_ADMIN_REQUIRED" in error_msg or "not enough rights" in error_msg.lower():
                await message.reply(f"❌ Bot is not an admin in the target channel!\n\nPlease add the bot as admin with 'Post Messages' permission.")
            elif "CHAT_WRITE_FORBIDDEN" in error_msg:
                await message.reply(f"❌ Bot cannot post in the target channel!\n\nMake sure the bot has permission to post messages.")
            elif "chat not found" in error_msg.lower():
                await message.reply(f"❌ Target channel not found!\n\nChannel ID: {progress['target_chat_id']}\n\nPlease set the channel again.")
            else:
                await message.reply(f"❌ Error forwarding video:\n\n{error_msg}\n\nCheck the logs for details.")


# Health check endpoint for Render
async def health_check(request):
    return web.Response(text="Bot is running!")


async def start_web_server():
    """Start web server for Render health checks"""
    app_web = web.Application()
    app_web.router.add_get('/', health_check)
    app_web.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"Web server started on port {PORT}")


if __name__ == "__main__":
    import sys
    import signal
    
    def signal_handler(sig, frame):
        print("\n🛑 Received shutdown signal", flush=True)
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 50, flush=True)
    print("STARTING BOT...", flush=True)
    print("=" * 50, flush=True)
    
    try:
        # Initialize database and web server in a separate thread
        import threading
        
        def init_services():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            print("Initializing database...", flush=True)
            loop.run_until_complete(init_db())
            print("✅ Database initialized successfully!", flush=True)
            
            print("Starting web server...", flush=True)
            loop.run_until_complete(start_web_server())
            print(f"✅ Web server started on port {PORT}", flush=True)
            
            # Keep the loop running for the web server
            loop.run_forever()
        
        # Start services in background thread
        services_thread = threading.Thread(target=init_services, daemon=True)
        services_thread.start()
        
        # Wait a bit for services to initialize
        import time
        time.sleep(3)
        
        print(f"Authorized users: {AUTHORIZED_USERS if AUTHORIZED_USERS else 'All users (no restriction)'}", flush=True)
        print("=" * 50, flush=True)
        print("STARTING PYROGRAM BOT...", flush=True)
        print("=" * 50, flush=True)
        
        sys.stdout.flush()
        
        # Start the bot in the main thread
        app.run()
        
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user", flush=True)
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
