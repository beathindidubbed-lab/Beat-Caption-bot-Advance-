import sys

import json
from pyrogram import Client, filters, idle
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

# Bot credentials and config
API_ID = int(os.getenv('API_ID', ''))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
PORT = int(os.getenv('PORT', '10000'))
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL', '')
DATABASE_URL = os.getenv('DATABASE_URL', '')

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
    workers=4
)

print(f"🔧 Pyrogram Client initialized with API_ID: {API_ID}", flush=True)
print(f"🔧 BOT_TOKEN present: {'Yes' if BOT_TOKEN else 'No'}", flush=True)

# Track users waiting for input and last messages
waiting_for_input = {}  # user_id -> input_type
last_bot_messages = {}  # user_id -> message_id

# Per-user upload locks
user_locks = {}  # user_id -> asyncio.Lock

# Web server for health checks
web_app = web.Application()


def get_user_lock(user_id):
    """Get or create a lock for a specific user"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


async def init_db():
    """Initialize PostgreSQL database"""
    global db_pool
    if DATABASE_URL:
        try:
            db_pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False)
            await db_pool.open()
            
            # Create tables
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    # User settings table
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
                    
                    # Welcome message settings table
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
                    
                    # Upload history table
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
                    
                    # Channel info table
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
                    
                    # Create indexes for better performance
                    await cur.execute('''
                        CREATE INDEX IF NOT EXISTS idx_upload_history_user_id 
                        ON upload_history(user_id)
                    ''')
                    
                    await cur.execute('''
                        CREATE INDEX IF NOT EXISTS idx_upload_history_uploaded_at 
                        ON upload_history(uploaded_at)
                    ''')
                
                await conn.commit()
            
            print("✅ PostgreSQL database initialized successfully")
        except Exception as e:
            print(f"❌ Database initialization failed: {e}")
            print("⚠️ Falling back to JSON file storage")
            db_pool = None
    else:
        print("⚠️ No DATABASE_URL found, using JSON file storage")


async def get_user_settings(user_id, username=None, first_name=None):
    """Load settings for a specific user from database or create default"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        'SELECT * FROM user_settings WHERE user_id = %s', 
                        (user_id,)
                    )
                    row = await cur.fetchone()
                    
                    if row:
                        # Get column names
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
                        # Create default settings for new user
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
            print(f"Error loading user settings from database: {e}")
    
    # Fallback to JSON file per user
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
    """Save settings for a specific user to database"""
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
            print(f"Error saving user settings to database: {e}")
    
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
            print(f"Error logging upload: {e}")


async def save_channel_info(user_id, chat_id, username, title, chat_type):
    """Save channel info to database"""
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
            print(f"Error saving channel info: {e}")


async def get_user_upload_stats(user_id):
    """Get upload statistics for a specific user"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        'SELECT COUNT(*) FROM upload_history WHERE user_id = %s', 
                        (user_id,)
                    )
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
            print(f"Error getting stats: {e}")
    return 0, 0


async def get_all_users_count():
    """Get total number of users"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT COUNT(*) FROM user_settings')
                    count = await cur.fetchone()
                    return count[0] if count else 0
        except Exception as e:
            print(f"Error getting user count: {e}")
    return 0


async def get_welcome_message():
    """Get welcome message from database"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        'SELECT * FROM welcome_settings ORDER BY id DESC LIMIT 1'
                    )
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
            print(f"Error getting welcome message: {e}")
    return None


async def save_welcome_message(message_type, file_id, caption):
    """Save welcome message to database"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    # Delete old welcome messages
                    await cur.execute('DELETE FROM welcome_settings')
                    
                    # Insert new welcome message
                    await cur.execute('''
                        INSERT INTO welcome_settings (message_type, file_id, caption)
                        VALUES (%s, %s, %s)
                    ''', (message_type, file_id, caption))
                await conn.commit()
                return True
        except Exception as e:
            print(f"Error saving welcome message: {e}")
    return False


async def delete_last_message(client, chat_id):
    """Delete the last bot message if it exists"""
    if chat_id in last_bot_messages:
        try:
            await client.delete_messages(chat_id, last_bot_messages[chat_id])
        except Exception:
            pass
        del last_bot_messages[chat_id]


def get_menu_markup():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Preview Caption", callback_data="preview")],
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
        ]
    )


def get_admin_menu_markup():
    """Admin menu with additional options"""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Set Welcome Message", callback_data="admin_set_welcome")],
            [InlineKeyboardButton("👁️ Preview Welcome", callback_data="admin_preview_welcome")],
            [InlineKeyboardButton("📊 Global Stats", callback_data="admin_global_stats")],
            [InlineKeyboardButton("⬅️ Back to User Menu", callback_data="back_to_main")]
        ]
    )


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


@app.on_message(filters.private & filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Load or create user settings
    settings = await get_user_settings(user_id, username, first_name)
    
    try:
        await message.delete()
    except Exception:
        pass
    
    await delete_last_message(client, message.chat.id)
    
    # Get custom welcome message if exists
    welcome_data = await get_welcome_message()
    
    if welcome_data and welcome_data['file_id']:
        # Send custom welcome message with media
        try:
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
                # Fallback to text
                sent = await client.send_message(
                    message.chat.id,
                    welcome_data['caption'].format(first_name=first_name, user_id=user_id),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_menu_markup()
                )
            last_bot_messages[message.chat.id] = sent.id
            return
        except Exception as e:
            print(f"Error sending custom welcome: {e}")
    
    # Default welcome message
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
    """Show help message with all commands"""
    help_text = (
        "📚 <b>Bot Commands & Features</b>\n\n"
        
        "🤖 <b>Basic Commands:</b>\n"
        "/start - Initialize bot and show main menu\n"
        "/help - Show this help message\n"
        "/stats - View your upload statistics\n"
        "/admin - Admin panel (admin only)\n\n"
        
        "📋 <b>Menu Features:</b>\n\n"
        
        "📝 <b>Preview Caption</b>\n"
        "   • See how your caption will look\n"
        "   • Shows current settings preview\n\n"
        
        "✏️ <b>Set Caption</b>\n"
        "   • Customize your caption template\n"
        "   • Use placeholders: {season}, {episode}, {total_episode}, {quality}\n"
        "   • Supports HTML formatting\n\n"
        
        "📺 <b>Set Season</b>\n"
        "   • Change current season number\n"
        "   • Used in caption placeholders\n\n"
        
        "🎬 <b>Set Episode</b>\n"
        "   • Change current episode number\n"
        "   • Auto-increments after all qualities uploaded\n\n"
        
        "🔢 <b>Set Total Episode</b>\n"
        "   • Set overall episode counter\n"
        "   • Tracks total episodes across all seasons\n\n"
        
        "🎥 <b>Quality Settings</b>\n"
        "   • Select video qualities (480p, 720p, 1080p, 4K, 2160p)\n"
        "   • Bot cycles through selected qualities\n"
        "   • Multiple qualities per episode supported\n\n"
        
        "🎯 <b>Set Target Channel</b>\n"
        "   • Configure YOUR channel where videos will be forwarded\n"
        "   • Two methods: Forward message OR Send username/ID\n"
        "   • Bot must be admin in your channel\n\n"
        
        "📊 <b>My Statistics</b>\n"
        "   • View your total uploads\n"
        "   • See today's upload count\n"
        "   • Check current progress\n"
        "   • Monitor channel status\n\n"
        
        "🔄 <b>Reset Episode</b>\n"
        "   • Reset episode counter to 1\n"
        "   • Useful when starting new season\n\n"
        
        "📤 <b>Video Upload Process:</b>\n"
        "1. Send video file to bot\n"
        "2. Bot applies your caption\n"
        "3. Forwards to your channel\n"
        "4. Cycles to next quality\n"
        "5. Auto-increments episode when all qualities done\n\n"
        
        "💡 <b>Tips:</b>\n"
        "• Make bot admin in your channel first\n"
        "• Use forward method to easily get channel ID\n"
        "• Preview caption before uploading\n"
        "• Each user has independent settings\n"
        "• Your uploads don't affect other users\n\n"
        
        "🔒 <b>Privacy:</b>\n"
        "• Your settings are private\n"
        "• Your channel is separate\n"
        "• Your statistics are personal\n"
        "• Complete data isolation\n\n"
        
        "❓ <b>Need Help?</b>\n"
        "Contact the bot admin or check documentation."
    )
    
    await message.reply(help_text, parse_mode=ParseMode.HTML)


@app.on_message(filters.private & filters.command("stats"))
async def stats_command(client, message):
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
    """Admin command to see global stats and manage bot"""
    user_id = message.from_user.id
    
    # You can add specific admin user IDs here
    ADMIN_IDS = [user_id]  # Add your admin user IDs
    
    if user_id not in ADMIN_IDS:
        await message.reply("❌ You don't have permission to use this command.")
        return
    
    total_users = await get_all_users_count()
    
    admin_text = (
        f"👑 <b>Admin Panel</b>\n\n"
        f"📊 <b>Global Statistics:</b>\n"
        f"• Total Users: <code>{total_users}</code>\n"
        f"• Active Users: <code>{total_users}</code>\n\n"
        f"🤖 Bot Status: ✅ Running\n\n"
        f"⚙️ <b>Admin Options:</b>\n"
        f"Use the buttons below to manage bot settings."
    )
    
    await message.reply(admin_text, parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_markup())


@app.on_callback_query()
async def handle_buttons(client, callback_query: CallbackQuery):
    try:
        await callback_query.answer()
    except Exception:
        pass

    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    
    # Load user settings
    settings = await get_user_settings(user_id)

    await delete_last_message(client, chat_id)

    if data == "preview":
        if not settings['target_chat_id']:
            sent = await callback_query.message.reply(
                "⚠️ <b>No target channel set!</b>\n\n"
                "Please set your target channel first using the '🎯 Set Target Channel' button.",
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
            f"📝 <b>Preview Caption:</b>\n\n{preview_text}\n\n"
            f"<b>Your Current Settings:</b>\n"
            f"Season: {settings['season']}\n"
            f"Episode: {settings['episode']}\n"
            f"Total Episode: {settings['total_episode']}\n"
            f"Target Channel ID: <code>{settings['target_chat_id']}</code>\n"
            f"Selected Qualities: {', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_caption":
        waiting_for_input[user_id] = "caption"
        sent = await callback_query.message.reply(
            "✏️ <b>Set Your Caption Template</b>\n\n"
            "Please send the new base caption now (HTML supported).\n\n"
            "<b>Available placeholders:</b>\n"
            "<code>{season}</code> - Season number\n"
            "<code>{episode}</code> - Episode number\n"
            "<code>{total_episode}</code> - Total episode count\n"
            "<code>{quality}</code> - Video quality\n\n"
            "<b>Example:</b>\n"
            "<code>&lt;b&gt;My Anime&lt;/b&gt; - Season {season} Episode {episode}</code>",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_season":
        waiting_for_input[user_id] = "season"
        sent = await callback_query.message.reply(
            f"📺 Current season: <b>{settings['season']}</b>\n\n"
            "Please send the new season number (e.g., 1, 2, 3).",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_episode":
        waiting_for_input[user_id] = "episode"
        sent = await callback_query.message.reply(
            f"🎬 Current episode: <b>{settings['episode']}</b>\n\n"
            "Please send the new episode number.",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_total_episode":
        waiting_for_input[user_id] = "total_episode"
        sent = await callback_query.message.reply(
            f"🔢 Current total episode: <b>{settings['total_episode']}</b>\n\n"
            "Please send the new total episode number.",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "quality_menu":
        sent = await callback_query.message.reply(
            "🎥 <b>Your Quality Settings</b>\n\n"
            "Select which qualities should be uploaded for each episode.\n"
            "Click on a quality to toggle it on/off.\n\n"
            f"<b>Currently selected:</b> {', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}",
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
                "🎥 <b>Your Quality Settings</b>\n\n"
                "Select which qualities should be uploaded for each episode.\n"
                "Click on a quality to toggle it on/off.\n\n"
                f"<b>Currently selected:</b> {', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_quality_markup(settings['selected_qualities'])
            )
        except Exception:
            pass

    elif data == "set_channel":
        sent = await callback_query.message.reply(
            "🎯 <b>Set Your Target Channel</b>\n\n"
            "This is YOUR personal channel where videos will be forwarded.\n"
            "Other users have their own separate channels.\n\n"
            "Choose how you want to set it:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_channel_set_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "forward_channel":
        waiting_for_input[user_id] = "forward_channel"
        sent = await callback_query.message.reply(
            "📤 <b>Forward a message from your target channel</b>\n\n"
            "Please forward any message from the channel you want to set as target.\n"
            "I will automatically detect the channel ID.\n\n"
            "⚠️ <b>Important:</b> Make sure I'm an admin in that channel!",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "send_channel_id":
        waiting_for_input[user_id] = "channel_id"
        sent = await callback_query.message.reply(
            "🔗 <b>Send Channel Username or ID</b>\n\n"
            "Please send the channel username (e.g., @mychannel) or ID (e.g., -1001234567890).\n\n"
            "⚠️ <b>Important:</b> Make sure I'm an admin in that channel!",
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
            f"📺 <b>Current Progress:</b>\n"
            f"• Season: <code>{settings['season']}</code>\n"
            f"• Episode: <code>{settings['episode']}</code>\n"
            f"• Total Episodes: <code>{settings['total_episode']}</code>\n\n"
            f"🎯 <b>Channel:</b> {channel_status}\n"
            f"🎥 <b>Qualities:</b> <code>{', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "back_to_main":
        try:
            await callback_query.message.delete()
        except Exception:
            pass
        
        sent = await client.send_message(
            chat_id,
            "👋 <b>Welcome Back!</b>\n\n"
            "Use the buttons below to manage your bot.",
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
            f"Starting from Episode {settings['episode']} (Season {settings['season']}).\n"
            f"Total episode counter: {settings['total_episode']}",
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

    # Admin callbacks
    elif data == "admin_set_welcome":
        # Check if user is admin
        ADMIN_IDS = [user_id]  # You should load this from config
        if user_id not in ADMIN_IDS:
            await callback_query.answer("❌ Admin only!", show_alert=True)
            return
        
        waiting_for_input[user_id] = "admin_welcome"
        sent = await callback_query.message.reply(
            "📝 <b>Set Welcome Message</b>\n\n"
            "Send me a photo, video, or GIF with a caption.\n"
            "This will be shown to all users when they use /start\n\n"
            "<b>Available placeholders:</b>\n"
            "<code>{first_name}</code> - User's first name\n"
            "<code>{user_id}</code> - User's ID\n\n"
            "<b>Example caption:</b>\n"
            "<code>Welcome {first_name}! Your ID: {user_id}</code>",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "admin_preview_welcome":
        welcome_data = await get_welcome_message()
        if welcome_data and welcome_data['file_id']:
            try:
                preview_caption = (
                    "👁️ <b>Current Welcome Message Preview:</b>\n\n"
                    "─────────────────────\n\n"
                    f"{welcome_data['caption']}\n\n"
                    "─────────────────────\n\n"
                    f"<b>Type:</b> {welcome_data['message_type']}\n"
                    f"<b>Has Media:</b> ✅ Yes"
                )
                
                if welcome_data['message_type'] == 'photo':
                    await client.send_photo(
                        chat_id,
                        photo=welcome_data['file_id'],
                        caption=preview_caption,
                        parse_mode=ParseMode.HTML
                    )
                elif welcome_data['message_type'] == 'video':
                    await client.send_video(
                        chat_id,
                        video=welcome_data['file_id'],
                        caption=preview_caption,
                        parse_mode=ParseMode.HTML
                    )
                elif welcome_data['message_type'] == 'animation':
                    await client.send_animation(
                        chat_id,
                        animation=welcome_data['file_id'],
                        caption=preview_caption,
                        parse_mode=ParseMode.HTML
                    )
                
                sent = await callback_query.message.reply(
                    "Above is the current welcome message preview.",
                    reply_markup=get_admin_menu_markup()
                )
                last_bot_messages[chat_id] = sent.id
            except Exception as e:
                sent = await callback_query.message.reply(
                    f"❌ Error loading preview: {e}",
                    reply_markup=get_admin_menu_markup()
                )
                last_bot_messages[chat_id] = sent.id
        else:
            sent = await callback_query.message.reply(
                "📝 No custom welcome message set yet.\n\n"
                "Using default welcome message.",
                reply_markup=get_admin_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id

    elif data == "admin_global_stats":
        total_users = await get_all_users_count()
        sent = await callback_query.message.reply(
            f"📊 <b>Global Statistics</b>\n\n"
            f"👥 Total Users: <code>{total_users}</code>\n"
            f"🤖 Bot Status: ✅ Running\n"
            f"🗄️ Database: {'✅ Connected' if db_pool else '⚠️ Using JSON'}",
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
        except Exception:
            pass
        
        await delete_last_message(client, message.chat.id)
        
        if message.forward_from_chat:
            chat = message.forward_from_chat
            settings = await get_user_settings(user_id)
            settings["target_chat_id"] = chat.id
            await save_user_settings(settings)
            
            # Save channel info
            await save_channel_info(
                user_id,
                chat.id,
                chat.username if chat.username else None,
                chat.title,
                str(chat.type)
            )
            
            del waiting_for_input[user_id]
            
            sent = await client.send_message(
                message.chat.id,
                f"✅ <b>Your target channel updated!</b>\n\n"
                f"📝 Title: <b>{chat.title}</b>\n"
                f"🆔 ID: <code>{chat.id}</code>\n"
                f"👤 Username: @{chat.username if chat.username else 'N/A'}\n"
                f"📊 Type: {chat.type}\n\n"
                f"⚠️ <b>Important:</b> Make sure I'm an admin in this channel!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[message.chat.id] = sent.id
        else:
            sent = await client.send_message(
                message.chat.id,
                "❌ Please forward a message from a channel, not from a user.",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & (filters.photo | filters.video | filters.animation))
async def handle_media_for_welcome(client, message: Message):
    """Handle media messages for welcome message setup"""
    user_id = message.from_user.id
    
    # Check if admin is setting welcome message
    if user_id in waiting_for_input and waiting_for_input[user_id] == "admin_welcome":
        try:
            await message.delete()
        except Exception:
            pass
        
        await delete_last_message(client, message.chat.id)
        
        # Determine message type and get file_id
        message_type = None
        file_id = None
        caption = message.caption or "Welcome to the bot!"
        
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
            # Save to database
            success = await save_welcome_message(message_type, file_id, caption)
            
            if success:
                del waiting_for_input[user_id]
                sent = await client.send_message(
                    message.chat.id,
                    f"✅ <b>Welcome message updated!</b>\n\n"
                    f"📝 Type: {message_type}\n"
                    f"📄 Caption: {caption[:100]}{'...' if len(caption) > 100 else ''}\n\n"
                    f"Users will see this when they use /start",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_admin_menu_markup()
                )
                last_bot_messages[message.chat.id] = sent.id
            else:
                sent = await client.send_message(
                    message.chat.id,
                    "❌ Failed to save welcome message. Please try again.",
                    parse_mode=ParseMode.HTML
                )
                last_bot_messages[message.chat.id] = sent.id
        return
    
    # If not setting welcome, ignore (let video handler take care of it)


@app.on_message(filters.private & filters.text & ~filters.command("start") & ~filters.command("stats") & ~filters.command("admin") & ~filters.command("help") & ~filters.forwarded)
async def receive_input(client, message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id not in waiting_for_input:
        return

    try:
        await message.delete()
    except Exception:
        pass

    await delete_last_message(client, chat_id)
    
    settings = await get_user_settings(user_id)
    input_type = waiting_for_input[user_id]

    if input_type == "caption":
        settings["base_caption"] = message.text
        await save_user_settings(settings)
        del waiting_for_input[user_id]
        sent = await client.send_message(
            chat_id,
            "✅ <b>Your caption updated successfully!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif input_type == "season":
        if message.text.isdigit():
            new_season = int(message.text)
            settings["season"] = new_season
            await save_user_settings(settings)
            del waiting_for_input[user_id]
            sent = await client.send_message(
                chat_id,
                f"✅ Your season updated to {new_season}!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(
                chat_id,
                "❌ Please enter a valid number.",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[chat_id] = sent.id

    elif input_type == "episode":
        if message.text.isdigit():
            new_episode = int(message.text)
            settings["episode"] = new_episode
            await save_user_settings(settings)
            del waiting_for_input[user_id]
            sent = await client.send_message(
                chat_id,
                f"✅ Your episode updated to {new_episode}!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(
                chat_id,
                "❌ Please enter a valid number.",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[chat_id] = sent.id

    elif input_type == "total_episode":
        if message.text.isdigit():
            new_total_episode = int(message.text)
            settings["total_episode"] = new_total_episode
            await save_user_settings(settings)
            del waiting_for_input[user_id]
            sent = await client.send_message(
                chat_id,
                f"✅ Your total episode updated to {new_total_episode}!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(
                chat_id,
                "❌ Please enter a valid number.",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[chat_id] = sent.id

    elif input_type == "channel_id":
        text = message.text.strip()
        
        try:
            # Try to get chat info
            if text.startswith('@'):
                chat = await client.get_chat(text)
            elif text.lstrip('-').isdigit():
                chat = await client.get_chat(int(text))
            else:
                raise ValueError("Invalid format")
            
            settings["target_chat_id"] = chat.id
            await save_user_settings(settings)
            
            # Save channel info
            await save_channel_info(
                user_id,
                chat.id,
                chat.username if hasattr(chat, 'username') and chat.username else None,
                chat.title if hasattr(chat, 'title') else str(chat.id),
                str(chat.type)
            )
            
            del waiting_for_input[user_id]
            
            sent = await client.send_message(
                chat_id,
                f"✅ <b>Your target channel updated!</b>\n\n"
                f"📝 Title: <b>{chat.title if hasattr(chat, 'title') else 'N/A'}</b>\n"
                f"🆔 ID: <code>{chat.id}</code>\n"
                f"👤 Username: @{chat.username if hasattr(chat, 'username') and chat.username else 'N/A'}\n\n"
                f"⚠️ <b>Important:</b> Make sure I'm an admin in this channel!",
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
            
        except Exception as e:
            sent = await client.send_message(
                chat_id,
                f"❌ Error: Could not find channel.\n\n"
                f"Please make sure:\n"
                f"• The username is correct (e.g., @mychannel)\n"
                f"• Or the ID is correct (e.g., -1001234567890)\n"
                f"• The bot is admin in that channel\n\n"
                f"Error details: {str(e)}",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[chat_id] = sent.id


@app.on_message(filters.private & filters.video)
async def auto_forward(client, message):
    user_id = message.from_user.id
    
    # Get user-specific lock
    user_lock = get_user_lock(user_id)
    
    async with user_lock:
        # Load user settings
        settings = await get_user_settings(user_id)
        
        # Check if target channel is set
        if not settings["target_chat_id"]:
            await message.reply(
                "❌ <b>No target channel set!</b>\n\n"
                "Please set your target channel first using the '🎯 Set Target Channel' button.\n"
                "Use /start to open the menu.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Check if qualities are selected
        if not settings["selected_qualities"]:
            await message.reply(
                "❌ <b>No qualities selected!</b>\n\n"
                "Please select at least one quality from the Quality Settings menu.\n"
                "Use /start to open the menu.",
                parse_mode=ParseMode.HTML
            )
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
            
            # Log upload to database
            await log_upload(
                user_id,
                settings['season'],
                settings['episode'],
                settings['total_episode'],
                quality,
                file_id,
                caption,
                settings['target_chat_id']
            )

            await message.reply(
                f"✅ <b>Video forwarded successfully!</b>\n\n"
                f"📺 Season: {settings['season']}\n"
                f"🎬 Episode: {settings['episode']}\n"
                f"🔢 Total Episode: {settings['total_episode']}\n"
                f"🎥 Quality: {quality}\n"
                f"📊 Progress: {settings['video_count'] + 1}/{len(settings['selected_qualities'])} videos for this episode",
                parse_mode=ParseMode.HTML
            )

            settings["video_count"] += 1

            if settings["video_count"] >= len(settings["selected_qualities"]):
                settings["episode"] += 1
                settings["total_episode"] += 1
                settings["video_count"] = 0

            await save_user_settings(settings)

        except Exception as e:
            error_msg = str(e)
            await message.reply(
                f"❌ <b>Error forwarding video!</b>\n\n"
                f"Error: {error_msg}\n\n"
                f"<b>Common issues:</b>\n"
                f"• Bot is not admin in the target channel\n"
                f"• Channel ID is incorrect: <code>{settings['target_chat_id']}</code>\n"
                f"• Bot doesn't have permission to post videos\n\n"
                f"<b>Solution:</b>\n"
                f"1. Make sure I'm an admin in your channel\n"
                f"2. Give me permission to post messages\n"
                f"3. Try setting the channel again with /start",
                parse_mode=ParseMode.HTML
            )


# Web server handlers
async def health_check(request):
    """Health check endpoint"""
    total_users = await get_all_users_count()
    return web.Response(
        text=f"Bot is running! Total users: {total_users}",
        content_type='text/plain'
    )


async def stats_endpoint(request):
    """Global stats endpoint"""
    total_users = await get_all_users_count()
    
    stats = {
        'status': 'running',
        'total_users': total_users,
        'timestamp': datetime.utcnow().isoformat()
    }
    
    return web.json_response(stats)


async def self_ping():
    """Self-ping every 10 minutes to keep the service alive"""
    await asyncio.sleep(60)  # Wait 1 minute before first ping
    while True:
        await asyncio.sleep(600)  # 10 minutes
        if RENDER_EXTERNAL_URL:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{RENDER_EXTERNAL_URL}/health") as resp:
                        print(f"✅ Self-ping successful: {resp.status}")
            except Exception as e:
                print(f"❌ Self-ping failed: {e}")


async def start_web_server():
    """Start the web server"""
    web_app.router.add_get('/health', health_check)
    web_app.router.add_get('/', health_check)
    web_app.router.add_get('/stats', stats_endpoint)
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"✅ Web server started on 0.0.0.0:{PORT}")
    print(f"🌐 Health check: http://0.0.0.0:{PORT}/health")


async def main():
    """Main function to run bot and web server"""
    
    try:
        # Start web server FIRST
        print("🌐 Starting web server...", flush=True)
        await start_web_server()
        print(f"✅ Web server listening on port {PORT}", flush=True)
        
        # Initialize database
        print("🗄️ Initializing database...", flush=True)
        await init_db()
        
        # Start self-ping task
        asyncio.create_task(self_ping())
        
        # Start bot
        print("🚀 Starting Pyrogram bot...", flush=True)
        await app.start()
        print("✅ Bot started successfully!", flush=True)
        print(f"🤖 Bot username: @{app.me.username}", flush=True)
        print(f"🆔 Bot ID: {app.me.id}", flush=True)
        print("🤖 Multi-user mode enabled", flush=True)
        print("👥 Each user has their own settings and target channel", flush=True)
        print("📡 Bot is now listening for messages...", flush=True)
        sys.stdout.flush()
        
        # Keep running using idle
        await idle()
        
    except KeyboardInterrupt:
        print("⚠️ Bot stopped by user", flush=True)
    except Exception as e:
        print(f"❌ Error in main: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
    finally:
        if app.is_connected:
            print("🛑 Stopping bot...", flush=True)
            await app.stop()
        if db_pool:
            print("🗄️ Closing database pool...", flush=True)
            await db_pool.close()
        print("👋 Shutdown complete", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Goodbye!")

