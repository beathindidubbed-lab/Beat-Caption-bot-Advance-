import sys
import json
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from pathlib import Path
import asyncio
import os
from aiohttp import web
import aiohttp
import psycopg
from psycopg_pool import AsyncConnectionPool
from datetime import datetime
import logging
import inspect
from pyrogram.handlers import MessageHandler, CallbackQueryHandler 

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Bot credentials and config
API_ID = int(os.getenv('API_ID', ''))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
PORT = int(os.getenv('PORT', '10000'))
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL', '')
DATABASE_URL = os.getenv('DATABASE_URL', '')

# Webhook configuration
WEBHOOK_HOST = RENDER_EXTERNAL_URL.replace('https://', '').replace('http://', '') if RENDER_EXTERNAL_URL else ''
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else ''

# Admin IDs - Add your Telegram user IDs here
ADMIN_IDS = [
    int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()
]

# If no admin IDs in environment variable, add them manually here
if not ADMIN_IDS:
    ADMIN_IDS = [123456789]  # Replace with your actual Telegram user ID

logger.info(f"📧 Admin IDs configured: {ADMIN_IDS}")
logger.info(f"📧 Webhook URL: {WEBHOOK_URL if WEBHOOK_URL else 'Not configured (using polling)'}")

# Default settings
ALL_QUALITIES = ["480p", "720p", "1080p", "4K", "2160p"]
DEFAULT_CAPTION = ("<b>Anime</b> - <i>@Your_Channel</i>\n"
                  "Season {season} - Episode {episode} ({total_episode}) - {quality}\n"
                  "<blockquote>Don't miss this episode!</blockquote>")

# Database pool
db_pool = None

# Pyrogram app
app = Client(
    "auto_caption_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=4,
    in_memory=True
)

logger.info(f"📧 Pyrogram Client initialized")

# Track users waiting for input and last messages
waiting_for_input = {}
last_bot_messages = {}
user_locks = {}

# Web server
web_app = web.Application()


def get_user_lock(user_id):
    """Get or create a lock for a specific user"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


# --- HANDLER REGISTRATION LOGIC REMOVED (Replaced by decorators below) ---


async def init_db():
    """Initialize PostgreSQL database"""
    global db_pool
    if DATABASE_URL:
        try:
            db_pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False)
            await db_pool.open()
            
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('''
                        CREATE TABLE IF NOT EXISTS user_settings (
                            user_id BIGINT PRIMARY KEY,
                            username TEXT,
                            first_name TEXT,
                            season INTEGER NOT NULL DEFAULT 1,
                            episode INTEGER NOT NULL DEFAULT 1,
                            total_episode INTEGER NOT NULL DEFAULT 1,
                            video_count INTEGER NOT NULL DEFAULT 0,
                            selected_qualities TEXT NOT NULL DEFAULT '480p,720p,1080p',
                            base_caption TEXT NOT NULL,
                            target_chat_id BIGINT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    ''')
                    
                    await cur.execute('''
                        CREATE TABLE IF NOT EXISTS welcome_settings (
                            id SERIAL PRIMARY KEY,
                            message_type TEXT NOT NULL,
                            file_id TEXT,
                            caption TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    ''')
                    
                    await cur.execute('''
                        CREATE TABLE IF NOT EXISTS upload_history (
                            id SERIAL PRIMARY KEY,
                            user_id BIGINT NOT NULL,
                            season INTEGER NOT NULL,
                            episode INTEGER NOT NULL,
                            total_episode INTEGER NOT NULL,
                            quality TEXT NOT NULL,
                            file_id TEXT NOT NULL,
                            caption TEXT,
                            target_chat_id BIGINT,
                            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    ''')
                    
                    await cur.execute('''
                        CREATE TABLE IF NOT EXISTS channel_info (
                            user_id BIGINT NOT NULL,
                            chat_id BIGINT NOT NULL,
                            username TEXT,
                            title TEXT,
                            type TEXT,
                            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (user_id, chat_id)
                        )
                    ''')
                    
                    await cur.execute('''
                        CREATE INDEX IF NOT EXISTS idx_upload_history_user_id 
                        ON upload_history(user_id)
                    ''')
                    
                    await cur.execute('''
                        CREATE INDEX IF NOT EXISTS idx_upload_history_uploaded_at 
                        ON upload_history(uploaded_at)
                    ''')
                
                await conn.commit()
            
            logger.info("✅ PostgreSQL database initialized successfully")
        except Exception as e:
            logger.error(f"❌ Database initialization failed: {e}")
            logger.info("⚠️ Falling back to JSON file storage")
            db_pool = None
    else:
        logger.info("⚠️ No DATABASE_URL found, using JSON file storage")


async def get_user_settings(user_id, username=None, first_name=None):
    """Load settings for a specific user"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT * FROM user_settings WHERE user_id = %s', (user_id,))
                    row = await cur.fetchone()
                    
                    if row:
                        colnames = [desc[0] for desc in cur.description]
                        row_dict = dict(zip(colnames, row))
                        return {
                            'user_id': row_dict['user_id'],
                            'season': row_dict['season'],
                            'episode': row_dict['episode'],
                            'total_episode': row_dict['total_episode'],
                            'video_count': row_dict['video_count'],
                            'selected_qualities': row_dict['selected_qualities'].split(',') if row_dict['selected_qualities'] else [],
                            'base_caption': row_dict['base_caption'],
                            'target_chat_id': row_dict['target_chat_id']
                        }
                    else:
                        default_settings = {
                            'user_id': user_id,
                            'season': 1,
                            'episode': 1,
                            'total_episode': 1,
                            'video_count': 0,
                            'selected_qualities': ["480p", "720p", "1080p"],
                            'base_caption': DEFAULT_CAPTION,
                            'target_chat_id': None
                        }
                        
                        await cur.execute('''
                            INSERT INTO user_settings 
                            (user_id, username, first_name, season, episode, total_episode, 
                             video_count, selected_qualities, base_caption, target_chat_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ''', (user_id, username, first_name, 1, 1, 1, 0, 
                            '480p,720p,1080p', DEFAULT_CAPTION, None))
                        
                        await conn.commit()
                        return default_settings
        except Exception as e:
            logger.error(f"Error loading user settings: {e}")
    
    # Fallback to JSON
    user_file = Path(f"user_{user_id}_progress.json")
    if user_file.exists():
        with open(user_file, "r") as f:
            return json.load(f)
    
    return {
        'user_id': user_id,
        'season': 1,
        'episode': 1,
        'total_episode': 1,
        'video_count': 0,
        'selected_qualities': ["480p", "720p", "1080p"],
        'base_caption': DEFAULT_CAPTION,
        'target_chat_id': None
    }


async def save_user_settings(settings):
    """Save user settings"""
    user_id = settings['user_id']
    
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('''
                        UPDATE user_settings SET 
                            season = %s, episode = %s, total_episode = %s, 
                            video_count = %s, selected_qualities = %s, 
                            base_caption = %s, target_chat_id = %s, 
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                    ''', (settings['season'], settings['episode'], 
                        settings['total_episode'], settings['video_count'], 
                        ','.join(settings['selected_qualities']),
                        settings['base_caption'], settings['target_chat_id'], user_id))
                await conn.commit()
            return
        except Exception as e:
            logger.error(f"Error saving user settings: {e}")
    
    # Fallback to JSON
    user_file = Path(f"user_{user_id}_progress.json")
    user_file.write_text(json.dumps(settings, indent=2))


async def log_upload(user_id, season, episode, total_episode, quality, file_id, caption, target_chat_id):
    """Log upload to database"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('''
                        INSERT INTO upload_history 
                        (user_id, season, episode, total_episode, quality, file_id, caption, target_chat_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (user_id, season, episode, total_episode, quality, file_id, caption, target_chat_id))
                await conn.commit()
        except Exception as e:
            logger.error(f"Error logging upload: {e}")


async def save_channel_info(user_id, chat_id, username, title, chat_type):
    """Save channel info"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('''
                        INSERT INTO channel_info (user_id, chat_id, username, title, type)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (user_id, chat_id) DO UPDATE SET
                            username = EXCLUDED.username,
                            title = EXCLUDED.title,
                            type = EXCLUDED.type,
                            added_at = CURRENT_TIMESTAMP
                    ''', (user_id, chat_id, username, title, chat_type))
                await conn.commit()
        except Exception as e:
            logger.error(f"Error saving channel info: {e}")


async def get_user_upload_stats(user_id):
    """Get upload statistics"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT COUNT(*) FROM upload_history WHERE user_id = %s', (user_id,))
                    total = await cur.fetchone()
                    total = total[0] if total else 0
                    
                    await cur.execute(
                        'SELECT COUNT(*) FROM upload_history WHERE user_id = %s AND DATE(uploaded_at) = CURRENT_DATE',
                        (user_id,)
                    )
                    today = await cur.fetchone()
                    today = today[0] if today else 0
                    
                    return total, today
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
    return 0, 0


async def get_all_users_count():
    """Get total users"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT COUNT(*) FROM user_settings')
                    count = await cur.fetchone()
                    return count[0] if count else 0
        except Exception as e:
            logger.error(f"Error getting user count: {e}")
            return 0
    return 0


async def get_welcome_message():
    """Get welcome message"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT * FROM welcome_settings ORDER BY id DESC LIMIT 1')
                    row = await cur.fetchone()
                    if row:
                        colnames = [desc[0] for desc in cur.description]
                        row_dict = dict(zip(colnames, row))
                        return {
                            'message_type': row_dict['message_type'],
                            'file_id': row_dict['file_id'],
                            'caption': row_dict['caption']
                        }
        except Exception as e:
            logger.error(f"Error getting welcome message: {e}")
    return None


async def save_welcome_message(message_type, file_id, caption):
    """Save welcome message"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('DELETE FROM welcome_settings')
                    await cur.execute('''
                        INSERT INTO welcome_settings (message_type, file_id, caption)
                        VALUES (%s, %s, %s)
                    ''', (message_type, file_id, caption))
                await conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error saving welcome message: {e}")
    return False


async def delete_last_message(client, chat_id):
    """Delete the last bot message"""
    if chat_id in last_bot_messages:
        try:
            await client.delete_messages(chat_id, last_bot_messages[chat_id])
        except Exception:
            pass
        del last_bot_messages[chat_id]


def get_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Preview Caption", callback_data="preview")],
        [InlineKeyboardButton("✏️ Set Caption", callback_data="set_caption")],
        [
            InlineKeyboardButton("📺 Set Season", callback_data="set_season"),
            InlineKeyboardButton("🎬 Set Episode", callback_data="set_episode")
        ],
        [InlineKeyboardButton("🔢 Set Total Episode", callback_data="set_total_episode")],
        [InlineKeyboardButton("🎥 Quality Settings", callback_data="quality_menu")],
        [InlineKeyboardButton("🎯 Set Target Channel", callback_data="set_channel")],
        [InlineKeyboardButton("📊 My Statistics", callback_data="stats")],
        [InlineKeyboardButton("🔄 Reset Episode", callback_data="reset")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ])


def get_admin_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Set Welcome Message", callback_data="admin_set_welcome")],
        [InlineKeyboardButton("👁️ Preview Welcome", callback_data="admin_preview_welcome")],
        [InlineKeyboardButton("📊 Global Stats", callback_data="admin_global_stats")],
        [InlineKeyboardButton("⬅️ Back to User Menu", callback_data="back_to_main")]
    ])


def get_quality_markup(selected_qualities):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            f"{'✅ ' if q in selected_qualities else ''}{q}",
            callback_data=f"toggle_quality_{q}"
        )] for q in ALL_QUALITIES] +
        [[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")]]
    )


def get_channel_set_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Forward Message", callback_data="forward_channel")],
        [InlineKeyboardButton("🔗 Send Username/ID", callback_data="send_channel_id")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")]
    ])


# Handler functions (NOW REGISTERED VIA DECORATORS)

@app.on_message(filters.private & filters.command("start"))
async def start(client, message):
    logger.info(f"📨 /start from user {message.from_user.id} (@{message.from_user.username})")
    
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    try:
        settings = await get_user_settings(user_id, username, first_name)
        await message.delete()
    except Exception as e:
        logger.error(f"Error in start: {e}")
    
    await delete_last_message(client, message.chat.id)
    
    welcome_data = await get_welcome_message()
    
    if welcome_data and welcome_data['file_id']:
        try:
            # Code to send media welcome message...
            if welcome_data['message_type'] == 'photo':
                sent = await client.send_photo(
                    message.chat.id,
                    photo=welcome_data['file_id'],
                    caption=welcome_data['caption'].format(first_name=first_name, user_id=user_id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_menu_markup()
                )
            elif welcome_data['message_type'] == 'video':
                sent = await client.send_video(
                    message.chat.id,
                    video=welcome_data['file_id'],
                    caption=welcome_data['caption'].format(first_name=first_name, user_id=user_id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_menu_markup()
                )
            elif welcome_data['message_type'] == 'animation':
                sent = await client.send_animation(
                    message.chat.id,
                    animation=welcome_data['file_id'],
                    caption=welcome_data['caption'].format(first_name=first_name, user_id=user_id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_menu_markup()
                )
            else:
                sent = await client.send_message(
                    message.chat.id,
                    welcome_data['caption'].format(first_name=first_name, user_id=user_id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_menu_markup()
                )
            last_bot_messages[message.chat.id] = sent.id
            return
        except Exception as e:
            logger.error(f"Error sending custom welcome: {e}")
    
    welcome_text = (
        f"👋 <b>Welcome {first_name}!</b>\n\n"
        "🤖 <b>Your Personal Anime Caption Bot</b>\n\n"
        "✨ <b>Features:</b>\n"
        "• Auto-caption and forward videos\n"
        "• Multi-quality support\n"
        "• Episode tracking (personal)\n"
        "• Your own channel settings\n"
        "• Upload statistics\n\n"
        "🎯 <b>Get Started:</b>\n"
        "1. Set your target channel\n"
        "2. Configure caption template\n"
        "3. Select video qualities\n"
        "4. Send videos to forward!\n\n"
        "💡 Type /help to see all commands"
    )
    
    sent = await client.send_message(
        message.chat.id,
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_menu_markup()
    )
    last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & filters.command("help"))
async def help_command(client, message):
    try:
        await message.delete()
    except:
        pass
    
    help_text = (
        "📚 <b>Bot Commands & Features</b>\n\n"
        "🤖 <b>Basic Commands:</b>\n"
        "/start - Initialize bot and show main menu\n"
        "/help - Show this help message\n"
        "/stats - View your upload statistics\n"
        "/admin - Admin panel (admin only)\n\n"
        "💡 <b>Tips:</b>\n"
        "• Make bot admin in your channel first\n"
        "• Use forward method to easily get channel ID\n"
        "• Preview caption before uploading\n"
        "• Each user has independent settings\n\n"
        "❓ <b>Need Help?</b>\n"
        "Contact the bot admin."
    )
    
    await message.reply(help_text, parse_mode=ParseMode.HTML)


@app.on_message(filters.private & filters.command("stats"))
async def stats_command(client, message):
    try:
        await message.delete()
    except:
        pass
    
    user_id = message.from_user.id
    settings = await get_user_settings(user_id)
    total, today = await get_user_upload_stats(user_id)
    
    channel_status = "✅ Set" if settings['target_chat_id'] else "❌ Not Set"
    
    stats_text = (
        f"📊 <b>Your Statistics</b>\n\n"
        f"👤 User ID: <code>{user_id}</code>\n\n"
        f"📤 <b>Uploads:</b>\n"
        f"• Total: <code>{total}</code>\n"
        f"• Today: <code>{today}</code>\n\n"
        f"📺 <b>Current Progress:</b>\n"
        f"• Season: <code>{settings['season']}</code>\n"
        f"• Episode: <code>{settings['episode']}</code>\n"
        f"• Total Episodes: <code>{settings['total_episode']}</code>\n\n"
        f"🎯 <b>Channel:</b> {channel_status}\n"
        f"🎥 <b>Qualities:</b> <code>{', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}</code>"
    )
    await message.reply(stats_text, parse_mode=ParseMode.HTML)


@app.on_message(filters.private & filters.command("admin"))
async def admin_command(client, message):
    try:
        await message.delete()
    except:
        pass
    
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.reply(
            "❌ <b>Access Denied!</b>\n\n"
            "You don't have permission to use this command.",
            parse_mode=ParseMode.HTML
        )
        return
    
    total_users = await get_all_users_count()
    
    admin_text = (
        f"👑 <b>Admin Panel</b>\n\n"
        f"📊 <b>Global Statistics:</b>\n"
        f"• Total Users: <code>{total_users}</code>\n\n"
        f"🤖 Bot Status: ✅ Running\n"
        f"👤 Your Admin ID: <code>{user_id}</code>"
    )
    
    await message.reply(admin_text, parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_markup())


@app.on_callback_query()
async def handle_buttons(client, callback_query: CallbackQuery):
    try:
        await callback_query.answer()
    except:
        pass

    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    
    settings = await get_user_settings(user_id)
    await delete_last_message(client, chat_id)

    if data == "preview":
        if not settings['target_chat_id']:
            sent = await callback_query.message.reply(
                "⚠️ <b>No target channel set!</b>\n\n"
                "Please set your target channel first.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
            return
            
        quality = settings["selected_qualities"][settings["video_count"] % len(settings["selected_qualities"])] if settings["selected_qualities"] else "N/A"
        preview_text = settings["base_caption"] \
            .replace("{season}", f"{settings['season']:02}") \
            .replace("{episode}", f"{settings['episode']:02}") \
            .replace("{total_episode}", f"{settings['total_episode']:02}") \
            .replace("{quality}", quality)

        sent = await callback_query.message.reply(
            f"📄 <b>Preview Caption:</b>\n\n{preview_text}\n\n"
            f"<b>Current Settings:</b>\n"
            f"Season: {settings['season']}\n"
            f"Episode: {settings['episode']}\n"
            f"Total Episode: {settings['total_episode']}\n"
            f"Channel ID: <code>{settings['target_chat_id']}</code>\n"
            f"Qualities: {', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_caption":
        waiting_for_input[user_id] = "caption"
        sent = await callback_query.message.reply(
            "✏️ <b>Set Your Caption Template</b>\n\n"
            "Send the new caption (HTML supported).\n\n"
            "<b>Placeholders:</b>\n"
            "{season}, {episode}, {total_episode}, {quality}",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_season":
        waiting_for_input[user_id] = "season"
        sent = await callback_query.message.reply(
            f"📺 Current season: <b>{settings['season']}</b>\n\n"
            "Send the new season number.",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_episode":
        waiting_for_input[user_id] = "episode"
        sent = await callback_query.message.reply(
            f"🎬 Current episode: <b>{settings['episode']}</b>\n\n"
            "Send the new episode number.",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_total_episode":
        waiting_for_input[user_id] = "total_episode"
        sent = await callback_query.message.reply(
            f"🔢 Current total episode: <b>{settings['total_episode']}</b>\n\n"
            "Send the new total episode number.",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "quality_menu":
        sent = await callback_query.message.reply(
            "🎥 <b>Quality Settings</b>\n\n"
            "Select which qualities to upload.\n"
            "Click to toggle on/off.\n\n"
            f"<b>Selected:</b> {', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_quality_markup(settings['selected_qualities'])
        )
        last_bot_messages[chat_id] = sent.id

    elif data.startswith("toggle_quality_"):
        quality = data.replace("toggle_quality_", "")
        if quality in settings["selected_qualities"]:
            settings["selected_qualities"].remove(quality)
        else:
            settings["selected_qualities"].append(quality)
        
        settings["selected_qualities"] = [q for q in ALL_QUALITIES if q in settings["selected_qualities"]]
        await save_user_settings(settings)
        
        try:
            await callback_query.message.edit_text(
                "🎥 <b>Quality Settings</b>\n\n"
                "Select which qualities to upload.\n"
                "Click to toggle on/off.\n\n"
                f"<b>Selected:</b> {', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_quality_markup(settings['selected_qualities'])
            )
        except:
            pass

    elif data == "set_channel":
        sent = await callback_query.message.reply(
            "🎯 <b>Set Your Target Channel</b>\n\n"
            "Choose how to set it:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_channel_set_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "forward_channel":
        waiting_for_input[user_id] = "forward_channel"
        sent = await callback_query.message.reply(
            "📤 <b>Forward a message from your channel</b>\n\n"
            "Forward any message from your target channel.\n\n"
            "⚠️ Make sure I'm an admin!",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "send_channel_id":
        waiting_for_input[user_id] = "channel_id"
        sent = await callback_query.message.reply(
            "🔗 <b>Send Channel Username or ID</b>\n\n"
            "Send the channel username (e.g., @mychannel) or ID (e.g., -1001234567890).\n\n"
            "⚠️ Make sure I'm an admin!",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "stats":
        total, today = await get_user_upload_stats(user_id)
        channel_status = "✅ Set" if settings['target_chat_id'] else "❌ Not Set"
        
        sent = await callback_query.message.reply(
            f"📊 <b>Your Statistics</b>\n\n"
            f"👤 User ID: <code>{user_id}</code>\n\n"
            f"📤 <b>Uploads:</b>\n"
            f"• Total: <code>{total}</code>\n"
            f"• Today: <code>{today}</code>\n\n"
            f"📺 <b>Progress:</b>\n"
            f"• Season: <code>{settings['season']}</code>\n"
            f"• Episode: <code>{settings['episode']}</code>\n"
            f"• Total Episodes: <code>{settings['total_episode']}</code>\n\n"
            f"🎯 <b>Channel:</b> {channel_status}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "back_to_main":
        try:
            await callback_query.message.delete()
        except:
            pass
        
        sent = await client.send_message(
            chat_id,
            "👋 <b>Welcome Back!</b>\n\nUse the buttons below.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "reset":
        settings["episode"] = 1
        settings["video_count"] = 0
        await save_user_settings(settings)
        sent = await callback_query.message.reply(
            f"🔄 <b>Episode counter reset!</b>\n\n"
            f"Starting from Episode {settings['episode']} (Season {settings['season']}).",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "cancel":
        if user_id in waiting_for_input:
            del waiting_for_input[user_id]
            sent = await callback_query.message.reply(
                "❌ Process cancelled.",
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await callback_query.message.reply(
                "No ongoing process to cancel.",
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id

    elif data == "admin_set_welcome":
        if user_id not in ADMIN_IDS:
            await callback_query.answer("❌ Admin only!", show_alert=True)
            return
        
        waiting_for_input[user_id] = "admin_welcome"
        sent = await callback_query.message.reply(
            "📝 <b>Set Welcome Message</b>\n\n"
            "Send a photo/video/GIF with caption.\n\n"
            "<b>Placeholders:</b>\n"
            "{first_name}, {user_id}",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "admin_preview_welcome":
        welcome_data = await get_welcome_message()
        if welcome_data and welcome_data['file_id']:
            try:
                preview_caption = f"👁️ <b>Welcome Preview:</b>\n\n{welcome_data['caption']}\n\n<b>Type:</b> {welcome_data['message_type']}"
                
                if welcome_data['message_type'] == 'photo':
                    await client.send_photo(chat_id, photo=welcome_data['file_id'], caption=preview_caption, parse_mode=ParseMode.HTML)
                elif welcome_data['message_type'] == 'video':
                    await client.send_video(chat_id, video=welcome_data['file_id'], caption=preview_caption, parse_mode=ParseMode.HTML)
                elif welcome_data['message_type'] == 'animation':
                    await client.send_animation(chat_id, animation=welcome_data['file_id'], caption=preview_caption, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.error(f"Error preview: {e}")
        else:
            sent = await callback_query.message.reply(
                "📝 No custom welcome message set.",
                reply_markup=get_admin_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id

    elif data == "admin_global_stats":
        total_users = await get_all_users_count()
        sent = await callback_query.message.reply(
            f"📊 <b>Global Statistics</b>\n\n"
            f"👥 Total Users: <code>{total_users}</code>\n"
            f"🤖 Bot Status: ✅ Running\n"
            f"🗄️ Database: {'✅ Connected' if db_pool else '⚠️ JSON'}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_admin_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id


@app.on_message(filters.private & filters.forwarded)
async def handle_forwarded(client, message: Message):
    user_id = message.from_user.id
    
    if user_id in waiting_for_input and waiting_for_input[user_id] == "forward_channel":
        try:
            await message.delete()
        except:
            pass
        
        await delete_last_message(client, message.chat.id)
        
        if message.forward_from_chat:
            chat = message.forward_from_chat
            settings = await get_user_settings(user_id)
            settings["target_chat_id"] = chat.id
            await save_user_settings(settings)
            
            await save_channel_info(user_id, chat.id, chat.username if chat.username else None, chat.title, str(chat.type))
            
            del waiting_for_input[user_id]
            
            sent = await client.send_message(
                message.chat.id,
                f"✅ <b>Channel updated!</b>\n\n"
                f"📝 Title: <b>{chat.title}</b>\n"
                f"🆔 ID: <code>{chat.id}</code>\n"
                f"👤 Username: @{chat.username if chat.username else 'N/A'}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[message.chat.id] = sent.id
        else:
            sent = await client.send_message(
                message.chat.id,
                "❌ Please forward from a channel, not a user.",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & (filters.photo | filters.video | filters.animation))
async def handle_media_for_welcome(client, message: Message):
    user_id = message.from_user.id
    
    # Only process if admin is setting welcome message
    if user_id not in waiting_for_input or waiting_for_input[user_id] != "admin_welcome":
        return
    
    try:
        await message.delete()
    except:
        pass
    
    await delete_last_message(client, message.chat.id)
    
    message_type = None
    file_id = None
    caption = message.caption or "Welcome!"
    
    if message.photo:
        message_type = "photo"
        file_id = message.photo.file_id
    elif message.video:
        message_type = "video"
        file_id = message.video.file_id
    elif message.animation:
        message_type = "animation"
        file_id = message.animation.file_id
    
    if message_type and file_id:
        success = await save_welcome_message(message_type, file_id, caption)
        
        if success:
            del waiting_for_input[user_id]
            sent = await client.send_message(
                message.chat.id,
                f"✅ <b>Welcome message updated!</b>\n\n"
                f"📝 Type: {message_type}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_menu_markup()
            )
            last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & filters.text & ~filters.forwarded)
async def receive_input(client, message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Ignore if not waiting for input or if it's a command
    if user_id not in waiting_for_input or message.text.startswith('/'):
        return

    try:
        await message.delete()
    except:
        pass

    await delete_last_message(client, chat_id)
    
    settings = await get_user_settings(user_id)
    input_type = waiting_for_input[user_id]

    if input_type == "caption":
        settings["base_caption"] = message.text
        await save_user_settings(settings)
        del waiting_for_input[user_id]
        sent = await client.send_message(chat_id, "✅ Caption updated!", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
        last_bot_messages[chat_id] = sent.id

    elif input_type == "season":
        if message.text.isdigit():
            settings["season"] = int(message.text)
            await save_user_settings(settings)
            del waiting_for_input[user_id]
            sent = await client.send_message(chat_id, f"✅ Season updated to {settings['season']}!", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(chat_id, "❌ Please enter a valid number.", parse_mode=ParseMode.HTML)
            last_bot_messages[chat_id] = sent.id

    elif input_type == "episode":
        if message.text.isdigit():
            settings["episode"] = int(message.text)
            await save_user_settings(settings)
            del waiting_for_input[user_id]
            sent = await client.send_message(chat_id, f"✅ Episode updated to {settings['episode']}!", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(chat_id, "❌ Please enter a valid number.", parse_mode=ParseMode.HTML)
            last_bot_messages[chat_id] = sent.id

    elif input_type == "total_episode":
        if message.text.isdigit():
            settings["total_episode"] = int(message.text)
            await save_user_settings(settings)
            del waiting_for_input[user_id]
            sent = await client.send_message(chat_id, f"✅ Total episode updated to {settings['total_episode']}!", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(chat_id, "❌ Please enter a valid number.", parse_mode=ParseMode.HTML)
            last_bot_messages[chat_id] = sent.id

    elif input_type == "channel_id":
        text = message.text.strip()
        
        try:
            if text.startswith('@'):
                chat = await client.get_chat(text)
            elif text.lstrip('-').isdigit():
                chat = await client.get_chat(int(text))
            else:
                raise ValueError("Invalid format")
            
            settings["target_chat_id"] = chat.id
            await save_user_settings(settings)
            
            await save_channel_info(user_id, chat.id, chat.username if hasattr(chat, 'username') and chat.username else None, chat.title if hasattr(chat, 'title') else str(chat.id), str(chat.type))
            
            del waiting_for_input[user_id]
            
            sent = await client.send_message(
                chat_id,
                f"✅ <b>Channel updated!</b>\n\n"
                f"📝 Title: <b>{chat.title if hasattr(chat, 'title') else 'N/A'}</b>\n"
                f"🆔 ID: <code>{chat.id}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
            
        except Exception as e:
            sent = await client.send_message(
                chat_id,
                f"❌ Error: Could not find channel.\n\n{str(e)}",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[chat_id] = sent.id


@app.on_message(filters.private & filters.video & ~filters.forwarded & ~filters.media_group)
async def auto_forward(client, message):
    user_id = message.from_user.id
    
    # Ignore if waiting for input
    if user_id in waiting_for_input:
        return
    
    user_lock = get_user_lock(user_id)
    
    async with user_lock:
        settings = await get_user_settings(user_id)
        
        if not settings["target_chat_id"]:
            await message.reply("❌ No target channel set!\n\nUse /start to configure.", parse_mode=ParseMode.HTML)
            return
        
        if not settings["selected_qualities"]:
            await message.reply("❌ No qualities selected!\n\nUse /start to configure.", parse_mode=ParseMode.HTML)
            return

        file_id = message.video.file_id
        quality = settings["selected_qualities"][settings["video_count"] % len(settings["selected_qualities"])]

        caption = settings["base_caption"] \
            .replace("{season}", f"{settings['season']:02}") \
            .replace("{episode}", f"{settings['episode']:02}") \
            .replace("{total_episode}", f"{settings['total_episode']:02}") \
            .replace("{quality}", quality)

        try:
            await client.send_video(
                chat_id=settings["target_chat_id"],
                video=file_id,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
            
            await log_upload(user_id, settings['season'], settings['episode'], settings['total_episode'], quality, file_id, caption, settings['target_chat_id'])

            reply_msg = await message.reply(
                f"✅ <b>Video forwarded!</b>\n\n"
                f"📺 Season: {settings['season']}\n"
                f"🎬 Episode: {settings['episode']}\n"
                f"🎥 Quality: {quality}\n"
                f"📊 Progress: {settings['video_count'] + 1}/{len(settings['selected_qualities'])}",
                parse_mode=ParseMode.HTML
            )
            
            await asyncio.sleep(5)
            try:
                await reply_msg.delete()
                await message.delete()
            except:
                pass

            settings["video_count"] += 1

            if settings["video_count"] >= len(settings["selected_qualities"]):
                settings["episode"] += 1
                settings["total_episode"] += 1
                settings["video_count"] = 0

            await save_user_settings(settings)

        except Exception as e:
            logger.error(f"Error forwarding video: {e}")
            await message.reply(
                f"❌ <b>Error forwarding!</b>\n\n"
                f"Make sure:\n"
                f"• Bot is admin in channel\n"
                f"• Channel ID is correct: <code>{settings['target_chat_id']}</code>",
                parse_mode=ParseMode.HTML
            )


async def telegram_webhook(request):
    """Handle incoming webhook updates from Telegram"""
    try:
        update_dict = await request.json()
        update_id = update_dict.get('update_id', 'unknown')
        
        logger.info(f"📨 Webhook received update ID: {update_id}")
        
        # Process update asynchronously
        asyncio.create_task(process_update_manually(update_dict))
        
        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        return web.Response(status=200, text="OK")


async def process_update_manually(update_dict):
    """Process updates from webhook using Pyrogram's internal methods"""
    try:
        # Import Telegram raw types for conversion
        from pyrogram import types
        from pyrogram.enums import ChatType
        
        logger.info(f"🔄 Processing update: {list(update_dict.keys())}")
        
        # Handle messages
        if 'message' in update_dict:
            msg = update_dict['message']
            logger.info(f"📩 Message from user {msg.get('from', {}).get('id')}: {msg.get('text', 'N/A')}")
            
            try:
                from_user_data = msg.get('from', {})
                chat_data = msg.get('chat', {})
                
                # Build User object
                user_obj = types.User(
                    id=from_user_data.get('id'),
                    is_bot=from_user_data.get('is_bot', False),
                    first_name=from_user_data.get('first_name', ''),
                    last_name=from_user_data.get('last_name'),
                    username=from_user_data.get('username'),
                    language_code=from_user_data.get('language_code'),
                    client=app
                )
                
                # Build Chat object (for private chats, it's the same as user)
                chat_obj = types.Chat(
                    id=chat_data.get('id'),
                    type=ChatType.PRIVATE if chat_data.get('type') == 'private' else ChatType.GROUP,
                    first_name=chat_data.get('first_name'),
                    last_name=chat_data.get('last_name'),
                    username=chat_data.get('username'),
                    client=app
                )
                
                # Build Message object
                message_obj = types.Message(
                    id=msg.get('message_id'),
                    from_user=user_obj,
                    date=msg.get('date'),
                    chat=chat_obj,
                    text=msg.get('text'),
                    client=app
                )
                
                # Set additional attributes that filters might check
                message_obj.outgoing = False
                message_obj.mentioned = False
                message_obj.scheduled = False
                message_obj.from_scheduled = False
                message_obj.has_protected_content = False
                
                # Add entities if present (for command detection)
                if msg.get('entities'):
                    message_obj.entities = []
                    for ent in msg.get('entities', []):
                        if ent.get('type') == 'bot_command':
                            # Create a simple entity object
                            entity = types.MessageEntity(
                                type='bot_command',
                                offset=ent.get('offset'),
                                length=ent.get('length'),
                                client=app
                            )
                            message_obj.entities.append(entity)
                
                # Add command info if it's a command
                text = msg.get('text', '')
                if text.startswith('/'):
                    # Extract command without the leading '/' and without bot username
                    command_text = text.split()[0][1:].split('@')[0] if text else None
                    message_obj.command = [command_text] if command_text else None
                
                # Handle media fields for filters
                if 'video' in msg:
                    video_data = msg['video']
                    message_obj.video = types.Video(
                        file_id=video_data.get('file_id'),
                        file_unique_id=video_data.get('file_unique_id'),
                        width=video_data.get('width'),
                        height=video_data.get('height'),
                        duration=video_data.get('duration'),
                        mime_type=video_data.get('mime_type'),
                        file_size=video_data.get('file_size'),
                        client=app
                    )
                    message_obj.media = types.enums.MessageMediaType.VIDEO

                if 'photo' in msg:
                    # Get largest photo size
                    photo_sizes = msg['photo']['sizes']
                    largest_photo = max(photo_sizes, key=lambda p: p['file_size'])
                    message_obj.photo = types.Photo(
                        file_id=largest_photo.get('file_id'),
                        file_unique_id=largest_photo.get('file_unique_id'),
                        width=largest_photo.get('width'),
                        height=largest_photo.get('height'),
                        file_size=largest_photo.get('file_size'),
                        client=app
                    )
                    message_obj.media = types.enums.MessageMediaType.PHOTO
                
                if 'animation' in msg:
                    animation_data = msg['animation']
                    message_obj.animation = types.Animation(
                        file_id=animation_data.get('file_id'),
                        file_unique_id=animation_data.get('file_unique_id'),
                        width=animation_data.get('width'),
                        height=animation_data.get('height'),
                        duration=animation_data.get('duration'),
                        mime_type=animation_data.get('mime_type'),
                        file_size=animation_data.get('file_size'),
                        client=app
                    )
                    message_obj.media = types.enums.MessageMediaType.ANIMATION
                
                if 'caption' in msg:
                    message_obj.caption = msg['caption']
                    
                if 'forward_from_chat' in msg:
                    # Populate forward_from_chat for filters.forwarded
                    chat_data = msg['forward_from_chat']
                    message_obj.forward_from_chat = types.Chat(
                        id=chat_data.get('id'),
                        type=ChatType(chat_data.get('type').upper()), # Convert string type to Enum
                        title=chat_data.get('title'),
                        username=chat_data.get('username'),
                        client=app
                    )

                logger.info(f"✅ Created message object: {message_obj.text or 'Media'}")
                logger.info(f"📋 Message attributes: chat.id={message_obj.chat.id}, from_user.id={message_obj.from_user.id}, command={getattr(message_obj, 'command', None)}")
                
                # Now dispatch through handlers
                handlers_found = False
                
                # Recalculate total handlers here to confirm registration
                total_handlers_registered = sum(len(handlers) for handlers in app.dispatcher.groups.values())
                logger.info(f"🔍 Checking {total_handlers_registered} handlers in dispatcher groups")
                
                for group in sorted(app.dispatcher.groups.keys()):
                    for handler in app.dispatcher.groups[group]:
                        if isinstance(handler, MessageHandler):
                            handler_name = handler.callback.__name__
                            try:
                                filter_result = True
                                if handler.filters:
                                    filter_result = await handler.filters(app, message_obj)
                                
                                if filter_result:
                                    handlers_found = True
                                    logger.info(f"✅ Executing message handler: {handler_name}")
                                    await handler.callback(app, message_obj)
                                    break # Stop after finding the first matching handler in a group
                            except Exception as e:
                                logger.error(f"❌ Handler error in {handler_name}: {e}", exc_info=True)
                    
                    if handlers_found:
                        break
                
                if not handlers_found:
                    logger.warning(f"⚠️ No handlers matched for message: {message_obj.text or 'Media'}")
                    
            except Exception as e:
                logger.error(f"❌ Error processing message: {e}", exc_info=True)
        
        # Handle callback queries
        elif 'callback_query' in update_dict:
            cb = update_dict['callback_query']
            logger.info(f"🔘 Callback query from user {cb.get('from', {}).get('id')}: {cb.get('data')}")
            
            try:
                from_user_data = cb.get('from', {})
                message_data = cb.get('message', {})
                
                # Build User object
                user_obj = types.User(
                    id=from_user_data.get('id'),
                    is_bot=from_user_data.get('is_bot', False),
                    first_name=from_user_data.get('first_name', ''),
                    last_name=from_user_data.get('last_name'),
                    username=from_user_data.get('username'),
                    language_code=from_user_data.get('language_code'),
                    client=app
                )
                
                # Build Chat object
                chat_data = message_data.get('chat', {})
                chat_obj = types.Chat(
                    id=chat_data.get('id'),
                    type=ChatType.PRIVATE if chat_data.get('type') == 'private' else ChatType.GROUP,
                    first_name=chat_data.get('first_name'),
                    last_name=chat_data.get('last_name'),
                    username=chat_data.get('username'),
                    client=app
                )
                
                # Build Message object (for context)
                message_obj = types.Message(
                    id=message_data.get('message_id'),
                    from_user=user_obj,
                    date=message_data.get('date'),
                    chat=chat_obj,
                    text=message_data.get('text'),
                    client=app
                )
                
                # Build CallbackQuery object
                callback_obj = types.CallbackQuery(
                    id=cb.get('id'),
                    from_user=user_obj,
                    message=message_obj,
                    data=cb.get('data'),
                    chat_instance=cb.get('chat_instance'),
                    client=app
                )
                
                logger.info(f"✅ Created callback object: {callback_obj.data}")
                
                # Dispatch through handlers
                from pyrogram.handlers import CallbackQueryHandler
                for group in sorted(app.dispatcher.groups.keys()):
                    for handler in app.dispatcher.groups[group]:
                        if isinstance(handler, CallbackQueryHandler):
                            try:
                                if handler.filters is None or await handler.filters(app, callback_obj):
                                    logger.info(f"✅ Executing callback handler: {handler.callback.__name__}")
                                    await handler.callback(app, callback_obj)
                                    break
                            except Exception as e:
                                logger.error(f"❌ Callback handler error: {e}", exc_info=True)
                                
            except Exception as e:
                logger.error(f"❌ Error processing callback: {e}", exc_info=True)
    
    except Exception as e:
        logger.error(f"❌ Fatal error in process_update_manually: {e}", exc_info=True)


async def health_check(request):
    total_users = await get_all_users_count()
    return web.Response(text=f"Bot running! Users: {total_users}", content_type='text/plain')


async def stats_endpoint(request):
    total_users = await get_all_users_count()
    return web.json_response({
        'status': 'running',
        'total_users': total_users,
        'timestamp': datetime.utcnow().isoformat(),
        'webhook': WEBHOOK_URL if WEBHOOK_URL else 'polling'
    })


async def setup_webhook():
    """Set up Telegram webhook"""
    if not WEBHOOK_URL:
        logger.warning("⚠️ WEBHOOK_URL not set, using polling mode")
        return False
    
    try:
        # Use raw API call to set webhook
        import httpx
        
        telegram_api_url = f"https://api.telegram.org/bot{BOT_TOKEN}"
        
        # Delete existing webhook
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{telegram_api_url}/deleteWebhook",
                json={"drop_pending_updates": True}
            )
            result = response.json()
            if result.get('ok'):
                logger.info("🗑️ Deleted old webhook")
            
            # Set new webhook
            response = await client.post(
                f"{telegram_api_url}/setWebhook",
                json={
                    "url": WEBHOOK_URL,
                    "drop_pending_updates": True,
                    "allowed_updates": ["message", "callback_query"]
                }
            )
            result = response.json()
            
            if result.get('ok'):
                logger.info(f"✅ Webhook set successfully: {WEBHOOK_URL}")
                
                # Verify webhook
                response = await client.get(f"{telegram_api_url}/getWebhookInfo")
                webhook_info = response.json()
                
                if webhook_info.get('ok'):
                    info = webhook_info['result']
                    logger.info(f"📡 Webhook URL: {info.get('url', 'N/A')}")
                    logger.info(f"📊 Pending updates: {info.get('pending_update_count', 0)}")
                    if info.get('last_error_message'):
                        logger.warning(f"⚠️ Last error: {info.get('last_error_message')}")
                
                return True
            else:
                logger.error(f"❌ Failed to set webhook: {result.get('description', 'Unknown error')}")
                return False
            
    except Exception as e:
        logger.error(f"❌ Webhook setup error: {e}")
        return False


async def self_ping():
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(600)
        if RENDER_EXTERNAL_URL:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{RENDER_EXTERNAL_URL}/health") as resp:
                        logger.info(f"✅ Self-ping: {resp.status}")
            except Exception as e:
                logger.error(f"❌ Self-ping failed: {e}")


async def start_web_server():
    global web_app
    # Add webhook endpoint
    if WEBHOOK_URL:
        web_app.router.add_post(WEBHOOK_PATH, telegram_webhook)
        logger.info(f"🔗 Webhook endpoint: {WEBHOOK_PATH}")
    
    web_app.router.add_get('/health', health_check)
    web_app.router.add_get('/', health_check)
    web_app.router.add_get('/stats', stats_endpoint)
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"✅ Web server started on port {PORT}")


async def main():
    # Start web server
    logger.info("🌐 Starting web server...")
    await start_web_server()
    
    # Initialize database
    logger.info("🗄️ Initializing database...")
    await init_db()
    
    # Start bot
    logger.info("🚀 Starting bot...")
    
    try:
        # Handlers are registered globally via decorators. We only need to start the client.
        await app.start()
        
        me = await app.get_me()
        logger.info(f"✅ Bot started: @{me.username} (ID: {me.id})")
        
        # New check for handlers to ensure they are available in dispatcher groups
        total_handlers = sum(len(handlers) for handlers in app.dispatcher.groups.values())
        logger.info(f"📝 Total handlers found in dispatcher: {total_handlers}")
        
        if total_handlers == 0:
            logger.error("❌ Handler registration FAILED even with decorators. The Pyrogram Client Dispatcher is not loading handlers.")
        
        # Setup webhook if URL is provided
        if WEBHOOK_URL:
            webhook_success = await setup_webhook()
            if webhook_success:
                logger.info("🔗 Running in WEBHOOK mode")
            else:
                logger.warning("⚠️ Webhook setup failed, falling back to POLLING mode")
        else:
            logger.info("📡 Running in POLLING mode")
        
        logger.info("=" * 50)
        logger.info("✅ ALL SYSTEMS OPERATIONAL")
        logger.info("=" * 50)
        
        # Start self-ping
        asyncio.create_task(self_ping())
        
        # Keep alive
        while True:
            await asyncio.sleep(3600)
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise
    finally:
        logger.info("🛑 Shutting down...")
        try:
            if WEBHOOK_URL:
                # Delete webhook on shutdown
                import httpx
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
                        json={"drop_pending_updates": False}
                    )
                logger.info("🗑️ Webhook deleted")
            await app.stop()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        if db_pool:
            try:
                await db_pool.close()
            except:
                pass


if __name__ == "__main__":
    asyncio.run(main())
