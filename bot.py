"""
Telegram Multi-User Anime Caption Bot
Optimized for Render deployment with PostgreSQL
"""

import sys
import json
from pyrogram import Client, filters, idle
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from pathlib import Path
import asyncio
import os
from aiohttp import web
import httpx
import psycopg
from psycopg_pool import AsyncConnectionPool
from datetime import datetime
import logging

# ===== LOGGING SETUP =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== ENVIRONMENT VARIABLES =====
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
PORT = int(os.getenv('PORT', '10000'))
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL', '')
DATABASE_URL = os.getenv('DATABASE_URL', '')

# Validate critical environment variables
if not API_ID or API_ID == 0:
    logger.error("❌ API_ID is not set or invalid!")
if not API_HASH:
    logger.error("❌ API_HASH is not set!")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN is not set!")
else:
    logger.info(f"✅ Bot Token configured")

# Admin IDs configuration
ADMIN_IDS = []
admin_ids_env = os.getenv('ADMIN_IDS', '').strip()
if admin_ids_env:
    try:
        ADMIN_IDS = [int(id.strip()) for id in admin_ids_env.split(',') if id.strip()]
        logger.info(f"🔧 Admin IDs configured: {len(ADMIN_IDS)} admin(s)")
    except ValueError:
        logger.error("❌ Invalid ADMIN_IDS format")
        ADMIN_IDS = []

if not ADMIN_IDS:
    logger.warning("⚠️ No admin IDs configured")

# ===== DEFAULT SETTINGS =====
ALL_QUALITIES = ["480p", "720p", "1080p", "4K", "2160p"]
DEFAULT_CAPTION = (
    "<b>Anime</b> - <i>@Your_Channel</i>\n"
    "Season {season} - Episode {episode} ({total_episode}) - {quality}\n"
    "<blockquote>Don't miss this episode!</blockquote>"
)

# ===== GLOBAL VARIABLES =====
db_pool = None
waiting_for_input = {}
last_bot_messages = {}
user_locks = {}
web_app = web.Application()

# ===== PYROGRAM CLIENT =====
app = Client(
    "auto_caption_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=8
)

logger.info("🔧 Pyrogram Client initialized")

# ===== HELPER FUNCTIONS =====

def get_user_lock(user_id):
    """Get or create a lock for a specific user"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


# ===== DATABASE FUNCTIONS =====

async def init_db():
    """Initialize PostgreSQL database with all required tables"""
    global db_pool
    if DATABASE_URL:
        try:
            db_pool = AsyncConnectionPool(
                DATABASE_URL, 
                min_size=1, 
                max_size=10, 
                open=False
            )
            await db_pool.open()
            
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
                    
                    # Welcome settings table
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
                    
                    # Create indexes for performance
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
                    await cur.execute(
                        'SELECT * FROM user_settings WHERE user_id = %s', 
                        (user_id,)
                    )
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
                        # Create new user with defaults
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
                        ''', (
                            user_id, username, first_name, 1, 1, 1, 0, 
                            '480p,720p,1080p', DEFAULT_CAPTION, None
                        ))
                        
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
    """Save user settings to database or JSON"""
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
                    ''', (
                        settings['season'], settings['episode'], 
                        settings['total_episode'], settings['video_count'], 
                        ','.join(settings['selected_qualities']),
                        settings['base_caption'], settings['target_chat_id'], 
                        user_id
                    ))
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
                    ''', (
                        user_id, season, episode, total_episode, 
                        quality, file_id, caption, target_chat_id
                    ))
                await conn.commit()
        except Exception as e:
            logger.error(f"Error logging upload: {e}")


async def save_channel_info(user_id, chat_id, username, title, chat_type):
    """Save channel information"""
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
    """Get upload statistics for a user"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    # Total uploads
                    await cur.execute(
                        'SELECT COUNT(*) FROM upload_history WHERE user_id = %s', 
                        (user_id,)
                    )
                    total = await cur.fetchone()
                    total = total[0] if total else 0
                    
                    # Today's uploads
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
    """Get total number of users"""
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


async def get_welcome_message():
    """Get custom welcome message"""
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
            logger.error(f"Error getting welcome message: {e}")
    return None


async def save_welcome_message(message_type, file_id, caption):
    """Save custom welcome message"""
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

# ===== UI HELPER FUNCTIONS =====

async def delete_last_message(client, chat_id):
    """Delete the last bot message"""
    if chat_id in last_bot_messages:
        try:
            await client.delete_messages(chat_id, last_bot_messages[chat_id])
        except Exception:
            pass
        del last_bot_messages[chat_id]


def get_menu_markup():
    """Main menu keyboard markup"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Preview Caption", callback_data="preview")],
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
    """Admin panel keyboard markup"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Set Welcome Message", callback_data="admin_set_welcome")],
        [InlineKeyboardButton("👁️ Preview Welcome", callback_data="admin_preview_welcome")],
        [InlineKeyboardButton("📊 Global Stats", callback_data="admin_global_stats")],
        [InlineKeyboardButton("⬅️ Back to User Menu", callback_data="back_to_main")]
    ])


def get_quality_markup(selected_qualities):
    """Quality selection keyboard markup"""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            f"{'✅ ' if q in selected_qualities else ''}{q}",
            callback_data=f"toggle_quality_{q}"
        )] for q in ALL_QUALITIES] +
        [[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")]]
    )


def get_channel_set_markup():
    """Channel setup keyboard markup"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Forward Message", callback_data="forward_channel")],
        [InlineKeyboardButton("🔗 Send Username/ID", callback_data="send_channel_id")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")]
    ])


# ===== MESSAGE HANDLERS =====

async def start_handler(client, message):
    """Handle /start command"""
    logger.critical("🔴 START HANDLER CALLED!")  # Add this line
    logger.info(f"📨 /start from user {message.from_user.id}")

@app.on_message(filters.private & filters.command("start"))
async def start_handler(client, message):
    """Handle /start command"""
    logger.info(f"📨 /start from user {message.from_user.id}")
    
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    try:
        settings = await get_user_settings(user_id, username, first_name)
        await message.delete()
    except Exception as e:
        logger.error(f"Error in start: {e}")
    
    await delete_last_message(client, message.chat.id)
    
    # Check for custom welcome message
    welcome_data = await get_welcome_message()
    
    if welcome_data and welcome_data['file_id']:
        try:
            caption_text = welcome_data['caption'].format(
                first_name=first_name, 
                user_id=user_id
            )
            
            if welcome_data['message_type'] == 'photo':
                sent = await client.send_photo(
                    message.chat.id,
                    photo=welcome_data['file_id'],
                    caption=caption_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_menu_markup()
                )
            elif welcome_data['message_type'] == 'video':
                sent = await client.send_video(
                    message.chat.id,
                    video=welcome_data['file_id'],
                    caption=caption_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_menu_markup()
                )
            elif welcome_data['message_type'] == 'animation':
                sent = await client.send_animation(
                    message.chat.id,
                    animation=welcome_data['file_id'],
                    caption=caption_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_menu_markup()
                )
            else:
                sent = await client.send_message(
                    message.chat.id,
                    caption_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_menu_markup()
                )
            last_bot_messages[message.chat.id] = sent.id
            return
        except Exception as e:
            logger.error(f"Error sending custom welcome: {e}")
    
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
        "💡 Type /help for more info"
    )
    
    sent = await client.send_message(
        message.chat.id,
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_menu_markup()
    )
    last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & filters.command("help"))
async def help_handler(client, message):
    """Handle /help command"""
    try:
        await message.delete()
    except:
        pass
    
    await delete_last_message(client, message.chat.id)
    
    help_text = (
        "📚 <b>Bot Commands & Features</b>\n\n"
        "🤖 <b>Basic Commands:</b>\n"
        "/start - Initialize bot and show main menu\n"
        "/help - Show this help message\n"
        "/stats - View your upload statistics\n"
        "/admin - Admin panel (admin only)\n\n"
        "🎯 <b>How to Use:</b>\n"
        "1. Set your target channel (bot must be admin)\n"
        "2. Configure your caption template\n"
        "3. Select video qualities\n"
        "4. Send videos to auto-forward\n\n"
        "💡 <b>Tips:</b>\n"
        "• Make bot admin in your channel first\n"
        "• Use forward method to easily set channel\n"
        "• Preview caption before uploading\n"
        "• Each user has independent settings\n\n"
        "❓ <b>Need Help?</b>\n"
        "Check the documentation or contact support."
    )
    
    sent = await client.send_message(
        message.chat.id,
        help_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_menu_markup()
    )
    last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & filters.command("stats"))
async def stats_handler(client, message):
    """Handle /stats command"""
    user_id = message.from_user.id
    settings = await get_user_settings(user_id)
    total, today = await get_user_upload_stats(user_id)
    
    try:
        await message.delete()
    except:
        pass
        
    await delete_last_message(client, message.chat.id)
    
    channel_status = "✅ Set" if settings['target_chat_id'] else "❌ Not Set"
    
    sent = await client.send_message(
        message.chat.id,
        f"📊 <b>Your Statistics</b>\n\n"
        f"👤 User ID: <code>{user_id}</code>\n\n"
        f"📤 <b>Uploads:</b>\n"
        f"• Total: <code>{total}</code>\n"
        f"• Today: <code>{today}</code>\n\n"
        f"📺 <b>Progress:</b>\n"
        f"• Season: <code>{settings['season']}</code>\n"
        f"• Episode: <code>{settings['episode']}</code>\n"
        f"• Total Episodes: <code>{settings['total_episode']}</code>\n"
        f"• Videos Done: <code>{settings['video_count']}/{len(settings['selected_qualities'])}</code>\n\n"
        f"🎯 <b>Channel:</b> {channel_status}\n"
        f"🎥 <b>Qualities:</b> {', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_menu_markup()
    )
    last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & filters.command("admin"))
async def admin_handler(client, message):
    """Handle /admin command"""
    if not ADMIN_IDS or message.from_user.id not in ADMIN_IDS:
        await message.reply(
            "❌ <b>Access Denied</b>\n\n"
            "You are not authorized to use admin features.\n"
            "Contact the bot owner to enable admin access.",
            parse_mode=ParseMode.HTML
        )
        return
        
    try:
        await message.delete()
    except:
        pass
        
    await delete_last_message(client, message.chat.id)
    
    sent = await client.send_message(
        message.chat.id,
        "👑 <b>Admin Panel</b>\n\n"
        "Welcome, Admin. Use the buttons below to manage bot settings.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_menu_markup()
    )
    last_bot_messages[message.chat.id] = sent.id

# ===== TEXT INPUT HANDLER =====

@app.on_message(
    filters.private & 
    (filters.text | filters.sticker) & 
    filters.incoming & 
    ~filters.command(["start", "help", "stats", "admin"])
)
async def text_input_handler(client, message):
    """Handle text input from users"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    input_type = waiting_for_input.get(user_id)

    if not input_type:
        return
        
    try:
        await message.delete()
    except:
        pass
        
    await delete_last_message(client, message.chat.id)
    
    settings = await get_user_settings(user_id)

    async with get_user_lock(user_id):
        if input_type == "caption":
            if message.text:
                settings["base_caption"] = message.text
                await save_user_settings(settings)
                del waiting_for_input[user_id]
                sent = await client.send_message(
                    chat_id, 
                    "✅ Caption template updated successfully!",
                    parse_mode=ParseMode.HTML, 
                    reply_markup=get_menu_markup()
                )
                last_bot_messages[chat_id] = sent.id
            else:
                sent = await client.send_message(
                    chat_id, 
                    "❌ Please send a valid text caption.",
                    parse_mode=ParseMode.HTML
                )
                last_bot_messages[chat_id] = sent.id
                
        elif input_type == "season":
            if message.text and message.text.isdigit() and int(message.text) > 0:
                settings["season"] = int(message.text)
                await save_user_settings(settings)
                del waiting_for_input[user_id]
                sent = await client.send_message(
                    chat_id, 
                    f"✅ Season updated to <code>{settings['season']}</code>!",
                    parse_mode=ParseMode.HTML, 
                    reply_markup=get_menu_markup()
                )
                last_bot_messages[chat_id] = sent.id
            else:
                sent = await client.send_message(
                    chat_id, 
                    "❌ Please enter a valid number greater than 0.",
                    parse_mode=ParseMode.HTML
                )
                last_bot_messages[chat_id] = sent.id

        elif input_type == "episode":
            if message.text and message.text.isdigit() and int(message.text) > 0:
                settings["episode"] = int(message.text)
                settings["video_count"] = 0
                await save_user_settings(settings)
                del waiting_for_input[user_id]
                sent = await client.send_message(
                    chat_id, 
                    f"✅ Episode updated to <code>{settings['episode']}</code>!\n"
                    f"Video count has been reset to 0.",
                    parse_mode=ParseMode.HTML, 
                    reply_markup=get_menu_markup()
                )
                last_bot_messages[chat_id] = sent.id
            else:
                sent = await client.send_message(
                    chat_id, 
                    "❌ Please enter a valid number greater than 0.",
                    parse_mode=ParseMode.HTML
                )
                last_bot_messages[chat_id] = sent.id

        elif input_type == "total_episode":
            if message.text and message.text.isdigit() and int(message.text) > 0:
                settings["total_episode"] = int(message.text)
                await save_user_settings(settings)
                del waiting_for_input[user_id]
                sent = await client.send_message(
                    chat_id, 
                    f"✅ Total episode count updated to <code>{settings['total_episode']}</code>!",
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
                if text.startswith('@'):
                    chat = await client.get_chat(text)
                elif text.lstrip('-').isdigit():
                    chat = await client.get_chat(int(text))
                else:
                    raise ValueError("Invalid format")
                
                settings["target_chat_id"] = chat.id
                await save_user_settings(settings)
                
                username = getattr(chat, 'username', None)
                title = getattr(chat, 'title', str(chat.id))
                
                await save_channel_info(
                    user_id, chat.id, username, title, str(chat.type)
                )
                
                del waiting_for_input[user_id]
                
                sent = await client.send_message(
                    chat_id, 
                    f"✅ <b>Channel updated successfully!</b>\n\n"
                    f"📝 Title: <b>{title}</b>\n"
                    f"🆔 ID: <code>{chat.id}</code>\n"
                    f"👤 Username: @{username if username else 'N/A'}",
                    parse_mode=ParseMode.HTML, 
                    reply_markup=get_menu_markup()
                )
                last_bot_messages[chat_id] = sent.id
            except Exception as e:
                sent = await client.send_message(
                    chat_id, 
                    f"❌ <b>Error:</b> Could not find channel or bot is not admin.\n\n"
                    f"<code>{str(e)}</code>",
                    parse_mode=ParseMode.HTML 
                )
                last_bot_messages[chat_id] = sent.id

        elif input_type == "admin_welcome_caption":
            if message.text:
                welcome_data = waiting_for_input.get(f"{user_id}_welcome_data")
                if welcome_data:
                    success = await save_welcome_message(
                        welcome_data['message_type'], 
                        welcome_data['file_id'], 
                        message.text
                    )
                    if success:
                        del waiting_for_input[user_id]
                        if f"{user_id}_welcome_data" in waiting_for_input:
                            del waiting_for_input[f"{user_id}_welcome_data"]
                        
                        sent = await client.send_message(
                            chat_id, 
                            "✅ Welcome message updated successfully!",
                            parse_mode=ParseMode.HTML, 
                            reply_markup=get_admin_menu_markup()
                        )
                        last_bot_messages[chat_id] = sent.id
                    else:
                        sent = await client.send_message(
                            chat_id, 
                            "❌ Failed to save welcome message to database.",
                            parse_mode=ParseMode.HTML, 
                            reply_markup=get_admin_menu_markup()
                        )
                        last_bot_messages[chat_id] = sent.id
                else:
                    del waiting_for_input[user_id]
                    sent = await client.send_message(
                        chat_id, 
                        "⚠️ <b>Error:</b> Media data lost. Please start over from /admin.",
                        parse_mode=ParseMode.HTML, 
                        reply_markup=get_admin_menu_markup()
                    )
                    last_bot_messages[chat_id] = sent.id
            else:
                sent = await client.send_message(
                    chat_id, 
                    "❌ Please send a valid text caption.",
                    parse_mode=ParseMode.HTML
                )
                last_bot_messages[chat_id] = sent.id


# ===== FORWARDED MESSAGE HANDLER =====

@app.on_message(filters.private & filters.forwarded)
async def forward_handler(client, message):
    """Handle forwarded messages for channel setup"""
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
                f"✅ <b>Channel updated successfully!</b>\n\n"
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
                "❌ Please forward a message from a <b>channel</b> or <b>group</b>, not from a user.",
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[message.chat.id] = sent.id


# ===== MEDIA HANDLER (for admin welcome message) =====

@app.on_message(filters.private & (filters.photo | filters.video | filters.animation))
async def media_handler(client, message: Message):
    """Handle media uploads for welcome message setup"""
    user_id = message.from_user.id
    
    # Only process if user is waiting for admin welcome media
    if user_id not in waiting_for_input or waiting_for_input[user_id] != "admin_welcome":
        return
    
    # Verify admin access
    if user_id not in ADMIN_IDS:
        return

    try:
        await message.delete()
    except:
        pass
    
    await delete_last_message(client, message.chat.id)
    
    message_type = None
    file_id = None
    caption = message.caption or ""

    if message.photo:
        message_type = "photo"
        file_id = message.photo.file_id
    elif message.video:
        message_type = "video"
        file_id = message.video.file_id
    elif message.animation:
        message_type = "animation"
        file_id = message.animation.file_id
        
    if file_id:
        # Store media data temporarily
        waiting_for_input[f"{user_id}_welcome_data"] = {
            'message_type': message_type,
            'file_id': file_id
        }
        
        # Now wait for caption
        waiting_for_input[user_id] = "admin_welcome_caption"
        
        caption_prompt = (
            f"🖼️ <b>Media received!</b> Type: <code>{message_type}</code>\n\n"
            "Now, send the <b>final caption</b> (HTML supported).\n\n"
            "<b>Available Placeholders:</b>\n"
            "• <code>{first_name}</code> - User's first name\n"
            "• <code>{user_id}</code> - User's Telegram ID\n\n"
            f"<b>Current Draft:</b>\n<i>{caption if caption else 'No caption'}</i>"
        )
        
        sent = await client.send_message(
            message.chat.id,
            caption_prompt,
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[message.chat.id] = sent.id
    else:
        sent = await client.send_message(
            message.chat.id,
            "❌ Only <b>Photo</b>, <b>Video</b>, or <b>GIF/Animation</b> are supported.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_admin_menu_markup()
        )
        last_bot_messages[message.chat.id] = sent.id

# ===== VIDEO UPLOAD HANDLER =====

@app.on_message(filters.private & filters.video & ~filters.forwarded & ~filters.media_group)
async def video_handler(client, message):
    """Handle video uploads for auto-forwarding"""
    user_id = message.from_user.id
    
    # Skip if user is in input mode
    if user_id in waiting_for_input:
        return
    
    user_lock = get_user_lock(user_id)
    
    async with user_lock:
        try:
            settings = await get_user_settings(user_id)
            target_chat_id = settings.get('target_chat_id')
            
            # Validate target channel
            if not target_chat_id:
                await message.reply_text(
                    "⚠️ <b>Error: No target channel set</b>\n\n"
                    "Please set your target channel first using the menu or /start command.",
                    parse_mode=ParseMode.HTML
                )
                return

            # Validate selected qualities
            selected_qualities = settings.get('selected_qualities', [])
            if not selected_qualities:
                await message.reply_text(
                    "⚠️ <b>Error: No video qualities selected</b>\n\n"
                    "Please configure your quality settings using the menu.",
                    parse_mode=ParseMode.HTML
                )
                return

            # Calculate current quality
            quality_index = settings['video_count'] % len(selected_qualities)
            current_quality = selected_qualities[quality_index]
            
            # Generate caption with placeholders
            current_caption = settings["base_caption"] \
                .replace("{season}", f"{settings['season']:02}") \
                .replace("{episode}", f"{settings['episode']:02}") \
                .replace("{total_episode}", f"{settings['total_episode']:02}") \
                .replace("{quality}", current_quality)

            # Forward video to target channel
            await client.copy_message(
                chat_id=target_chat_id,
                from_chat_id=message.chat.id,
                message_id=message.id,
                caption=current_caption,
                parse_mode=ParseMode.HTML
            )
            
            # Log the upload
            await log_upload(
                user_id, 
                settings['season'], 
                settings['episode'], 
                settings['total_episode'], 
                current_quality, 
                message.video.file_id if message.video else "N/A", 
                current_caption, 
                target_chat_id
            )
            
            # Update video count
            settings['video_count'] += 1
            
            # Check if all qualities for this episode are done
            if settings['video_count'] >= len(selected_qualities):
                settings['episode'] += 1
                settings['video_count'] = 0
                
                await message.reply_text(
                    f"✅ <b>Episode Complete!</b>\n\n"
                    f"All qualities uploaded for Episode {settings['episode']-1}.\n\n"
                    f"📺 Next: <b>Season {settings['season']}, Episode {settings['episode']}</b>\n"
                    f"🎥 Quality: <b>{selected_qualities[0]}</b>",
                    parse_mode=ParseMode.HTML
                )
            else:
                next_quality_index = settings['video_count'] % len(selected_qualities)
                next_quality = selected_qualities[next_quality_index]
                
                await message.reply_text(
                    f"✅ <b>Upload Successful!</b>\n\n"
                    f"📤 Quality: <b>{current_quality}</b>\n"
                    f"📊 Progress: <b>{settings['video_count']}/{len(selected_qualities)}</b> videos\n\n"
                    f"🎥 Next quality: <b>{next_quality}</b>",
                    parse_mode=ParseMode.HTML
                )

            # Save updated settings
            await save_user_settings(settings)
            
        except Exception as e:
            logger.error(f"Error during video upload for user {user_id}: {e}", exc_info=True)
            await message.reply_text(
                f"❌ <b>Upload Failed</b>\n\n"
                f"<code>{str(e)}</code>\n\n"
                "<b>Possible reasons:</b>\n"
                "• Bot is not an admin in the target channel\n"
                "• Target channel ID is incorrect\n"
                "• Video file is corrupted or too large\n"
                "• Network connectivity issue",
                parse_mode=ParseMode.HTML
            )


# ===== CALLBACK QUERY HANDLER =====

@app.on_callback_query()
async def callback_handler(client, callback_query: CallbackQuery):
    """Handle all button clicks"""
    try:
        await callback_query.answer()
    except:
        pass
        
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    data = callback_query.data
    
    settings = await get_user_settings(user_id)
    await delete_last_message(client, chat_id)
    
    # ===== ADMIN ACTIONS =====
    
    if data.startswith("admin_") and user_id not in ADMIN_IDS:
        await callback_query.message.reply(
            "❌ <b>Access Denied</b>\n\nThis feature is for admins only.",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "admin_set_welcome":
        waiting_for_input[user_id] = "admin_welcome"
        sent = await callback_query.message.reply(
            "📝 <b>Set Welcome Message</b>\n\n"
            "<b>Step 1:</b> Send a photo, video, or GIF with your caption draft.\n"
            "<b>Step 2:</b> Bot will ask for the final caption.\n\n"
            "Use /admin to cancel.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_admin_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id
        return

    elif data == "admin_preview_welcome":
        welcome_data = await get_welcome_message()
        if not welcome_data:
            sent = await callback_query.message.reply(
                "⚠️ <b>No custom welcome message set</b>\n\n"
                "Use the button below to set one.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_menu_markup()
            )
        else:
            preview_caption = welcome_data['caption'].format(
                first_name='Test User', 
                user_id=123456789
            )
            
            try:
                if welcome_data['message_type'] == 'photo':
                    await client.send_photo(
                        chat_id, 
                        welcome_data['file_id'],
                        caption=f"👁️ <b>Preview:</b>\n\n{preview_caption}",
                        parse_mode=ParseMode.HTML
                    )
                elif welcome_data['message_type'] == 'video':
                    await client.send_video(
                        chat_id, 
                        welcome_data['file_id'],
                        caption=f"👁️ <b>Preview:</b>\n\n{preview_caption}",
                        parse_mode=ParseMode.HTML
                    )
                elif welcome_data['message_type'] == 'animation':
                    await client.send_animation(
                        chat_id, 
                        welcome_data['file_id'],
                        caption=f"👁️ <b>Preview:</b>\n\n{preview_caption}",
                        parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                await client.send_message(
                    chat_id, 
                    f"❌ <b>Preview Error:</b> <code>{str(e)}</code>",
                    parse_mode=ParseMode.HTML
                )
            
            sent = await client.send_message(
                chat_id, 
                "Use buttons below to manage settings.",
                parse_mode=ParseMode.HTML,
                reply_markup=get_admin_menu_markup()
            )
        
        last_bot_messages[chat_id] = sent.id
        return

    elif data == "admin_global_stats":
        total_users = await get_all_users_count()
        
        sent = await callback_query.message.reply(
            f"📊 <b>Global Statistics</b>\n\n"
            f"👥 <b>Total Users:</b> <code>{total_users}</code>\n"
            f"💾 <b>Database:</b> {'✅ PostgreSQL' if db_pool else '⚠️ JSON Fallback'}\n"
            f"🌐 <b>Server:</b> {RENDER_EXTERNAL_URL or 'Not Set'}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_admin_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id
        return

    # ===== USER ACTIONS =====
    
    elif data == "preview":
        if not settings['target_chat_id']:
            target_display = "❌ Not Set"
        else:
            target_display = f"<code>{settings['target_chat_id']}</code>"
        
        if settings["selected_qualities"]:
            quality = settings["selected_qualities"][
                settings["video_count"] % len(settings["selected_qualities"])
            ]
        else:
            quality = "N/A"
        
        preview_caption = settings["base_caption"] \
            .replace("{season}", f"{settings['season']:02}") \
            .replace("{episode}", f"{settings['episode']:02}") \
            .replace("{total_episode}", f"{settings['total_episode']:02}") \
            .replace("{quality}", quality)
            
        sent = await callback_query.message.reply(
            f"🔍 <b>Caption Preview:</b>\n\n"
            f"{preview_caption}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"<b>Current Settings:</b>\n"
            f"📺 Season: <code>{settings['season']}</code>\n"
            f"🎬 Episode: <code>{settings['episode']}</code>\n"
            f"🔢 Total Episodes: <code>{settings['total_episode']}</code>\n"
            f"📊 Progress: <code>{settings['video_count']}/{len(settings['selected_qualities'])}</code>\n"
            f"🎥 Next Quality: <b>{quality}</b>\n"
            f"🎯 Channel: {target_display}\n"
            f"✅ Qualities: {', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None'}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

# CONTINUATION OF CALLBACK HANDLER
    
    elif data == "set_caption":
        waiting_for_input[user_id] = "caption"
        sent = await callback_query.message.reply(
            "✏️ <b>Set Caption Template</b>\n\n"
            "Send your new caption template (HTML supported).\n\n"
            "<b>Available Placeholders:</b>\n"
            "• <code>{season}</code> - Season number (e.g., 01)\n"
            "• <code>{episode}</code> - Episode number (e.g., 10)\n"
            "• <code>{total_episode}</code> - Total episodes (e.g., 125)\n"
            "• <code>{quality}</code> - Video quality (e.g., 1080p)\n\n"
            f"<b>Current Caption:</b>\n<i>{settings['base_caption']}</i>",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_season":
        waiting_for_input[user_id] = "season"
        sent = await callback_query.message.reply(
            f"📺 <b>Set Season Number</b>\n\n"
            f"Current season: <code>{settings['season']}</code>\n\n"
            f"Send the new season number:",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_episode":
        waiting_for_input[user_id] = "episode"
        sent = await callback_query.message.reply(
            f"🎬 <b>Set Episode Number</b>\n\n"
            f"Current episode: <code>{settings['episode']}</code>\n\n"
            f"Send the new episode number.\n\n"
            f"⚠️ <b>Note:</b> This will reset your video count to 0.",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_total_episode":
        waiting_for_input[user_id] = "total_episode"
        sent = await callback_query.message.reply(
            f"🔢 <b>Set Total Episode Count</b>\n\n"
            f"Current total: <code>{settings['total_episode']}</code>\n\n"
            f"Send the new total episode count:",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "quality_menu":
        sent = await callback_query.message.reply(
            "🎥 <b>Quality Settings</b>\n\n"
            "Toggle the video qualities you plan to upload for each episode.\n"
            "The bot will cycle through these qualities in order.\n\n"
            "✅ = Selected | ❌ = Not Selected",
            parse_mode=ParseMode.HTML,
            reply_markup=get_quality_markup(settings['selected_qualities'])
        )
        last_bot_messages[chat_id] = sent.id

    elif data.startswith("toggle_quality_"):
        quality = data.split('_')[-1]
        
        async with get_user_lock(user_id):
            selected_qualities = settings['selected_qualities']
            
            if quality in selected_qualities:
                selected_qualities.remove(quality)
            else:
                selected_qualities.append(quality)
                
            # Sort qualities in the order they appear in ALL_QUALITIES
            selected_qualities.sort(key=lambda q: ALL_QUALITIES.index(q))
            
            settings['selected_qualities'] = selected_qualities
            await save_user_settings(settings)
            
            try:
                await callback_query.message.edit_text(
                    "🎥 <b>Quality Settings</b>\n\n"
                    "Toggle the video qualities you plan to upload for each episode.\n"
                    "The bot will cycle through these qualities in order.\n\n"
                    "✅ = Selected | ❌ = Not Selected",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_quality_markup(settings['selected_qualities'])
                )
            except:
                pass

    elif data == "set_channel":
        sent = await callback_query.message.reply(
            "🎯 <b>Set Target Channel</b>\n\n"
            "Choose how you want to set your target channel:\n\n"
            "⚠️ <b>Important:</b> The bot must be an <b>admin</b> in the target channel!",
            parse_mode=ParseMode.HTML,
            reply_markup=get_channel_set_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "forward_channel":
        waiting_for_input[user_id] = "forward_channel"
        sent = await callback_query.message.reply(
            "📤 <b>Forward a Message</b>\n\n"
            "Forward any message from your target channel/group.\n\n"
            "⚠️ Make sure the bot is an admin in that channel!",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id
        
    elif data == "send_channel_id":
        waiting_for_input[user_id] = "channel_id"
        sent = await callback_query.message.reply(
            "🔗 <b>Send Channel Username or ID</b>\n\n"
            "Send either:\n"
            "• Username: <code>@yourchannel</code>\n"
            "• ID: <code>-1001234567890</code>\n\n"
            "⚠️ Make sure the bot is an admin in that channel!",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "stats":
        total, today = await get_user_upload_stats(user_id)
        channel_status = "✅ Set" if settings['target_chat_id'] else "❌ Not Set"
        
        sent = await callback_query.message.reply(
            f"📊 <b>Your Statistics</b>\n\n"
            f"👤 <b>User ID:</b> <code>{user_id}</code>\n\n"
            f"📤 <b>Upload History:</b>\n"
            f"• Total Uploads: <code>{total}</code>\n"
            f"• Today's Uploads: <code>{today}</code>\n\n"
            f"📺 <b>Current Progress:</b>\n"
            f"• Season: <code>{settings['season']}</code>\n"
            f"• Episode: <code>{settings['episode']}</code>\n"
            f"• Total Episodes: <code>{settings['total_episode']}</code>\n"
            f"• Videos Done: <code>{settings['video_count']}/{len(settings['selected_qualities'])}</code>\n\n"
            f"🎯 <b>Target Channel:</b> {channel_status}\n"
            f"🎥 <b>Selected Qualities:</b>\n"
            f"{', '.join(settings['selected_qualities']) if settings['selected_qualities'] else 'None selected'}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "back_to_main":
        # Clear any waiting input state
        if user_id in waiting_for_input:
            del waiting_for_input[user_id]
        if f"{user_id}_welcome_data" in waiting_for_input:
            del waiting_for_input[f"{user_id}_welcome_data"]
            
        try:
            await callback_query.message.delete()
        except:
            pass
            
        sent = await client.send_message(
            chat_id, 
            "👋 <b>Main Menu</b>\n\nUse the buttons below to manage your settings.",
            parse_mode=ParseMode.HTML, 
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "reset":
        async with get_user_lock(user_id):
            settings["episode"] = 1
            settings["video_count"] = 0
            await save_user_settings(settings)
        
        sent = await callback_query.message.reply(
            "🔄 <b>Episode Progress Reset!</b>\n\n"
            "• Episode is now: <code>1</code>\n"
            "• Video count is now: <code>0</code>\n\n"
            "You can start uploading from Episode 1 again.",
            parse_mode=ParseMode.HTML, 
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "cancel":
        # Clear any waiting input state
        if user_id in waiting_for_input:
            del waiting_for_input[user_id]
        if f"{user_id}_welcome_data" in waiting_for_input:
            del waiting_for_input[f"{user_id}_welcome_data"]
            
        try:
            await callback_query.message.delete()
        except:
            pass

# ===== WEB SERVER =====

async def health_check(request):
    """Health check endpoint for Render"""
    total_handlers = sum(len(handlers) for handlers in app.dispatcher.groups.values())
    
    if total_handlers == 0:
        logger.warning("⚠️ Health check: Zero handlers registered!")
        return web.Response(
            text=f"Bot Running but NO HANDLERS! Handlers: {total_handlers}",
            status=200
        )
    
    return web.Response(
        text=f"Bot Running ✅ | Handlers: {total_handlers}",
        status=200
    )


async def self_ping():
    """Self-ping to keep Render service awake"""
    if not RENDER_EXTERNAL_URL:
        logger.warning("⚠️ RENDER_EXTERNAL_URL not set. Self-ping disabled.")
        return

    health_url = f"{RENDER_EXTERNAL_URL}/health"
    logger.info(f"🔗 Starting self-ping to {health_url}")
    
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                response = await client.get(health_url)
                if response.status_code == 200:
                    logger.debug(f"✅ Self-ping successful")
                else:
                    logger.warning(f"⚠️ Self-ping status: {response.status_code}")
                
                await asyncio.sleep(600)  # Ping every 10 minutes
            except Exception as e:
                logger.error(f"❌ Self-ping error: {e}")
                await asyncio.sleep(600)


async def start_web_server():
    """Start the web server for Render"""
    web_app.add_routes([web.get("/health", health_check)])
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    
    logger.info(f"🌐 Starting web server on 0.0.0.0:{PORT}")
    await site.start()
    
    # Keep web server running
    while True:
        await asyncio.sleep(3600)


async def run_forever():
    """Keep Pyrogram client running and verify handlers"""
    logger.info("🔄 Keep-Alive: Initializing...")
    
    # Give handlers time to register
    await asyncio.sleep(2)
    
    # Verify handlers
    total_handlers = sum(len(handlers) for handlers in app.dispatcher.groups.values())
    logger.info(f"📋 Total handlers registered: {total_handlers}")
    
    if total_handlers == 0:
        logger.error("❌ CRITICAL: No handlers registered!")
        logger.error("❌ Bot will NOT respond to messages!")
        logger.error("❌ Check decorator syntax and imports!")
    else:
        logger.info("✅ Handlers successfully registered")
        logger.info("✅ Bot is ready to process messages")
    
    # Keep running
    logger.info("♾️ Keep-Alive loop active...")
    while True:
        await asyncio.sleep(3600)


# ===== MAIN FUNCTION =====

async def main():
    """Main execution function"""
    logger.info("=" * 60)
    logger.info("🚀 STARTING TELEGRAM ANIME CAPTION BOT")
    logger.info("=" * 60)
    
    # Initialize database
    await init_db()
    
    try:
        # Start Pyrogram client
        logger.info("📡 Starting Pyrogram client...")
        await app.start()
        logger.info("✅ Pyrogram client started successfully")
        
        # Verify handlers are registered
        total_handlers = sum(len(handlers) for handlers in app.dispatcher.groups.values())
        logger.info(f"📋 Registered handlers: {total_handlers}")

        # Debug: List all registered handlers
        for group_id, handlers_list in app.dispatcher.groups.items():
            logger.info(f"Group {group_id}: {len(handlers_list)} handlers")
            for handler in handlers_list:
                logger.info(f"  - {handler.__class__.__name__}")

        if total_handlers == 0:
            logger.error("❌ CRITICAL: No handlers registered!")
            logger.error("❌ Bot will not respond to messages!")
        else:
            logger.info("✅ All handlers registered successfully")
        
        logger.info("=" * 60)
        logger.info("✅ ALL SYSTEMS OPERATIONAL")
        logger.info("=" * 60)
        logger.info("📡 Running in CONCURRENT mode:")
        logger.info("   • Pyrogram Keep-Alive")
        logger.info("   • Web Server (Health Check)")
        logger.info("   • Self-Ping (24/7 Uptime)")
        logger.info("=" * 60)
        
        # Run all tasks concurrently
        await asyncio.gather(
            run_forever(),
            start_web_server(),
            self_ping(),
            return_exceptions=False
        )

    except Exception as e:
        logger.error(f"❌ Critical error in main: {e}", exc_info=True)
    finally:
        logger.info("🛑 Shutting down...")
        try:
            await app.stop()
            logger.info("✅ Pyrogram client stopped")
        except Exception as e:
            logger.error(f"Error stopping client: {e}")
        
        if db_pool:
            try:
                await db_pool.close()
                logger.info("✅ Database pool closed")
            except Exception as e:
                logger.error(f"Error closing DB pool: {e}")


# ===== ENTRY POINT =====

if __name__ == "__main__":
    try:
        logger.info("🎬 Bot script starting...")
        
        # Force module to execute and register decorators
        import sys
        logger.info(f"✅ Module loaded: {__name__}")
        logger.info(f"✅ App object: {app}")
        
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⌨️ Keyboard interrupt received. Shutting down...")
    except Exception as e:
        logger.error(f"❌ Top-level error: {e}", exc_info=True)
    finally:
        logger.info("👋 Bot terminated")

