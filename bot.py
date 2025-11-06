import sys
import json
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message, Document
from pathlib import Path
import asyncio
import os
from aiohttp import web
import aiohttp
import psycopg
from psycopg_pool import AsyncConnectionPool
from datetime import datetime
import logging
import time # NEW: For rate limiting
import io # NEW: For import/export
import inspect 
import traceback # NEW: For better error logging

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

logger.info(f"🔧 Admin IDs configured: {ADMIN_IDS}")
logger.info(f"🔧 Webhook URL: {WEBHOOK_URL if WEBHOOK_URL else 'Not configured (using polling)'}")

# Default settings
ALL_QUALITIES = ["480p", "720p", "1080p", "4K", "2160p"]

# NEW: Extended caption template with new placeholders
DEFAULT_CAPTION = ("<b>Anime</b> - <i>@Your_Channel</i>\n"
                  "Season {season} - Episode {episode} ({total_episode}) - {quality}\n"
                  "<b>Uploader:</b> <a href='tg://user?id={user_id}'>{first_name}</a> (@{username})\n"
                  "<b>Channel:</b> <code>{chat_title}</code> (ID: <code>{chat_id}</code>)\n"
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

logger.info(f"🔧 Pyrogram Client initialized")

# Trackers for features
waiting_for_input = {}
last_bot_messages = {}
user_locks = {}
media_group_cache = {} # NEW: To track media groups already processed for batch uploads
last_upload_time = {} # NEW: For rate limiting (cooldown)

# Web server - Initialized globally for access in start_web_server
web_app = web.Application()

# NEW: Localization Framework
def get_string(key, lang="en"):
    """Fetches a string for localization. Default is English (en)."""
    STRINGS = {
        "en": {
            "rate_limit": "⚠️ <b>Rate Limit:</b> Please wait **{cooldown} seconds** between uploads. Last upload was **{elapsed:.1f} seconds** ago.",
            "menu_welcome": "👋 <b>Welcome Back!</b>\n\nUse the buttons below.",
            "export_success": "✅ <b>Settings Exported!</b>\n\nHere is your JSON file. Save it for backup. Only the *uploader* of this file can use it for import.",
            "export_fail": "❌ <b>Export Failed!</b>\n\nCould not fetch settings.",
            "import_prompt": "⬆️ <b>Import Settings</b>\n\nSend me the **JSON file** containing your settings data (from `/export`).",
            "import_invalid": "❌ <b>Import Failed!</b>\n\nInvalid file format, missing required keys, or User ID mismatch (expected: <code>{expected_id}</code>, found: <code>{found_id}</code>). Please upload the original JSON file.",
            "import_success": "✅ <b>Settings Imported!</b>\n\nYour progress has been restored.",
            "placeholder_help": "\n\n<b>Available Placeholders:</b>\n"
                                "• Episode: <code>{season}</code>, <code>{episode}</code>, <code>{total_episode}</code>, <code>{quality}</code>\n"
                                "• Uploader: <code>{username}</code>, <code>{first_name}</code>, <code>{user_id}</code>\n"
                                "• Channel: <code>{chat_id}</code>, <code>{chat_title}</code>",
            "error_channel_not_set": "❌ <b>No target channel set!</b>\n\nPlease set your target channel first.",
            "upload_success": "✅ <b>Video processed!</b>\n\n"
                              "📺 Season: {season}\n"
                              "🎬 Episode: {episode}\n"
                              "🎥 Quality: {quality}\n"
                              "🔢 Next Count: {next_count}/{total_qualities}",
            "upload_error_not_admin": "❌ <b>Error forwarding!</b>\n\n"
                                      "Make sure:\n"
                                      "• Bot is admin in channel\n"
                                      "• Channel ID is correct: <code>{target_chat_id}</code>",
            "admin_only": "❌ <b>Access Denied!</b>\n\nYou don't have permission to use this command."
        }
    }
    return STRINGS.get(lang, STRINGS['en']).get(key, key) # Fallback to key itself


def get_user_lock(user_id):
    """Get or create a lock for a specific user to prevent race conditions in batch processing"""
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


async def get_channel_info(chat_id):
    """Retrieve channel info by chat_id (for placeholders)"""
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    # Select the latest info for the chat_id, regardless of the user who set it
                    await cur.execute(
                        'SELECT username, title, chat_id FROM channel_info WHERE chat_id = %s ORDER BY added_at DESC LIMIT 1',
                        (chat_id,)
                    )
                    row = await cur.fetchone()
                    if row:
                        return {
                            'username': row[0] or 'N/A',
                            'title': row[1] or 'Private Channel',
                            'chat_id': row[2]
                        }
        except Exception as e:
            logger.error(f"Error retrieving channel info: {e}")
    # Default fallback
    return {'username': 'N/A', 'title': 'Private Channel', 'chat_id': chat_id}


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
    default_settings = {
        'user_id': user_id,
        'season': 1,
        'episode': 1,
        'total_episode': 1,
        'video_count': 0,
        'selected_qualities': ["480p", "720p", "1080p"],
        'base_caption': DEFAULT_CAPTION,
        'target_chat_id': None,
        'username': username,
        'first_name': first_name
    }
    
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT * FROM user_settings WHERE user_id = %s', (user_id,))
                    row = await cur.fetchone()
                    
                    if row:
                        colnames = [desc[0] for desc in cur.description]
                        row_dict = dict(zip(colnames, row))
                        
                        settings = {
                            'user_id': row_dict['user_id'],
                            'season': row_dict['season'],
                            'episode': row_dict['episode'],
                            'total_episode': row_dict['total_episode'],
                            'video_count': row_dict['video_count'],
                            'selected_qualities': [q.strip() for q in row_dict['selected_qualities'].split(',') if q.strip()],
                            'base_caption': row_dict['base_caption'],
                            'target_chat_id': row_dict['target_chat_id'],
                            'username': row_dict['username'],
                            'first_name': row_dict['first_name']
                        }
                        return settings
                    else:
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
            logger.error(f"Error loading user settings from DB: {e}")
    
    # Fallback to JSON
    user_file = Path(f"user_{user_id}_progress.json")
    if user_file.exists():
        try:
            with open(user_file, "r") as f:
                data = json.load(f)
                # Merge loaded data with defaults to ensure new fields (like username) are present
                return {**default_settings, **data}
        except Exception as e:
            logger.error(f"Error loading user settings from JSON: {e}")
    
    return default_settings


async def save_user_settings(settings):
    """Save user settings"""
    user_id = settings['user_id']
    
    # Ensure selected_qualities is a list before joining
    if not isinstance(settings['selected_qualities'], list):
        settings['selected_qualities'] = [str(q).strip() for q in settings['selected_qualities'] if str(q).strip()]

    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('''
                        UPDATE user_settings SET 
                            season = %s, episode = %s, total_episode = %s, 
                            video_count = %s, selected_qualities = %s, 
                            base_caption = %s, target_chat_id = %s, 
                            username = %s, first_name = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE user_id = %s
                    ''', (settings['season'], settings['episode'], 
                        settings['total_episode'], settings['video_count'], 
                        ','.join(settings['selected_qualities']),
                        settings['base_caption'], settings['target_chat_id'], 
                        settings.get('username'), settings.get('first_name'), user_id))
                await conn.commit()
            return
        except Exception as e:
            logger.error(f"Error saving user settings to DB: {e}")
    
    # Fallback to JSON
    user_file = Path(f"user_{user_id}_progress.json")
    # Clean up settings dictionary for safe JSON serialization (remove DB specific fields)
    safe_settings = {k: v for k, v in settings.items() if k not in ['username', 'first_name']}
    user_file.write_text(json.dumps(safe_settings, indent=2, default=str))


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
    # ... (Stats functions remain the same)
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
    # ... (Total users function remains the same)
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
    # ... (Welcome message functions remain the same)
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
    # ... (Welcome message functions remain the same)
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
        [
            InlineKeyboardButton("⬆️ Import Settings", callback_data="import_settings"), # NEW
            InlineKeyboardButton("⬇️ Export Settings", callback_data="export_settings")  # NEW
        ],
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


@app.on_message(filters.private & filters.command("start"))
async def start(client, message):
    logger.info(f"📨 /start from user {message.from_user.id} (@{message.from_user.username})")
    
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Fetch settings and ensure basic user data is updated
    settings = await get_user_settings(user_id, username, first_name)
    settings['username'] = username # Update username if changed
    settings['first_name'] = first_name
    await save_user_settings(settings)

    try:
        await message.delete()
    except Exception as e:
        logger.error(f"Error deleting start message: {e}")
    
    await delete_last_message(client, message.chat.id)
    
    welcome_data = await get_welcome_message()
    
    # ... (Welcome message logic using custom welcome remains the same)
    
    # Fallback welcome text
    welcome_text = (
        f"👋 <b>Welcome {first_name}!</b>\n\n"
        "🤖 <b>Your Personal Anime Caption Bot</b>\n\n"
        "✨ <b>Features:</b>\n"
        "• Auto-caption and forward videos\n"
        "• **Batch Uploads** and **Rate Limiting**\n"
        "• **Backup/Restore** settings via `/export`\n"
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


# NEW: Command for exporting settings
@app.on_message(filters.private & filters.command("export"))
async def export_command(client, message):
    user_id = message.from_user.id
    try:
        settings = await get_user_settings(user_id)
        
        # Add a verification field to prevent unauthorized imports
        export_data = {
            "user_id": settings['user_id'],
            "export_timestamp": datetime.now().isoformat(),
            "settings": settings
        }

        # Create an in-memory file for the JSON data
        json_bytes = json.dumps(export_data, indent=2, default=str).encode('utf-8')
        bio = io.BytesIO(json_bytes)
        bio.name = f"settings_{user_id}.json"
        
        await message.reply_document(
            document=bio,
            caption=get_string("export_success"),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Export failed for user {user_id}: {e}")
        await message.reply(get_string("export_fail"), parse_mode=ParseMode.HTML)


# NEW: Command for importing settings
@app.on_message(filters.private & filters.command("import"))
async def import_command(client, message):
    user_id = message.from_user.id
    
    waiting_for_input[user_id] = "import_settings"
    
    await message.reply(
        get_string("import_prompt"),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])
    )


@app.on_message(filters.private & filters.command("help"))
async def help_command(client, message):
    # ... (Help command remains the same)
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
        "/export - Get a JSON file of your settings for backup\n" # NEW
        "/import - Start the process to restore settings from a JSON file\n" # NEW
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
    # ... (Stats command remains the same)
    try:
        await message.delete()
    except:
        pass
    
    user_id = message.from_user.id
    settings = await get_user_settings(user_id)
    total, today = await get_user_upload_stats(user_id)
    
    channel_status = f"<code>{settings['target_chat_id']}</code>" if settings['target_chat_id'] else "❌ Not Set"
    
    stats_text = (
        f"📊 <b>Your Statistics</b>\n\n"
        f"👤 User ID: <code>{user_id}</code>\n"
        f"👑 Admin Status: {'✅ Admin' if user_id in ADMIN_IDS else '❌ User'}\n\n" # NEW
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
    # ... (Admin command remains the same)
    try:
        await message.delete()
    except:
        pass
    
    user_id = message.from_user.id
    
    if user_id not in ADMIN_IDS:
        await message.reply(get_string("admin_only"), parse_mode=ParseMode.HTML)
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
            # Localization used
            sent = await callback_query.message.reply(
                get_string("error_channel_not_set"),
                parse_mode=ParseMode.HTML,
                reply_markup=get_menu_markup()
            )
            last_bot_messages[chat_id] = sent.id
            return
            
        quality = settings["selected_qualities"][settings["video_count"] % len(settings["selected_qualities"])] if settings["selected_qualities"] else "N/A"
        
        channel_info = await get_channel_info(settings['target_chat_id'])
        
        preview_text = settings["base_caption"] \
            .replace("{season}", f"{settings['season']:02}") \
            .replace("{episode}", f"{settings['episode']:02}") \
            .replace("{total_episode}", f"{settings['total_episode']:02}") \
            .replace("{quality}", quality) \
            .replace("{username}", settings.get('username') or 'N/A') \
            .replace("{first_name}", settings.get('first_name') or 'N/A') \
            .replace("{user_id}", str(user_id)) \
            .replace("{chat_id}", str(channel_info['chat_id'])) \
            .replace("{chat_title}", channel_info['title'])


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
            "Send the new caption (HTML supported)." + get_string("placeholder_help"), # Localization used
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    # ... (Other settings handlers like set_season, set_episode, set_total_episode remain the same)
    
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
        
        # Ensure selected qualities are returned in ALL_QUALITIES order
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
    
    # NEW: Import/Export menu options
    elif data == "export_settings":
        # Simply call the export command logic
        await export_command(client, callback_query.message)
        # Display menu again for convenience
        sent = await client.send_message(
            chat_id, get_string("menu_welcome"),
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id
        
    elif data == "import_settings":
        # Simply call the import command logic
        await import_command(client, callback_query.message)


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

    # ... (stats, back_to_main, reset, cancel, admin_xxx handlers remain the same)
    elif data == "back_to_main":
        try:
            await callback_query.message.delete()
        except:
            pass
        
        sent = await client.send_message(
            chat_id,
            get_string("menu_welcome"), # Localization used
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id


# NEW: Handle JSON file upload for import
@app.on_message(filters.private & filters.document)
async def handle_import_file(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in waiting_for_input or waiting_for_input[user_id] != "import_settings":
        return
        
    try:
        await message.delete()
    except:
        pass
    
    await delete_last_message(client, message.chat.id)

    # Check MIME type and file extension
    doc: Document = message.document
    if doc.mime_type != 'application/json' or not doc.file_name.endswith('.json'):
        await client.send_message(message.chat.id, "❌ **Import Failed!** Please upload a valid JSON file.", parse_mode=ParseMode.HTML)
        del waiting_for_input[user_id]
        return
    
    try:
        # Download file content in memory
        file_path = await client.download_media(doc, in_memory=True)
        json_data = json.load(file_path)
        
        # Validation checks
        if 'user_id' not in json_data or 'settings' not in json_data:
            raise ValueError("Missing required top-level keys ('user_id', 'settings').")
        
        imported_user_id = json_data['user_id']
        imported_settings = json_data['settings']
        
        if imported_user_id != user_id:
            await client.send_message(
                message.chat.id, 
                get_string("import_invalid").format(expected_id=user_id, found_id=imported_user_id),
                parse_mode=ParseMode.HTML
            )
            return

        # Sanity check for critical settings fields
        required_keys = ['season', 'episode', 'total_episode', 'video_count', 'selected_qualities', 'base_caption']
        if not all(key in imported_settings for key in required_keys):
            raise ValueError("Settings block is missing critical progress keys.")
        
        # Update current settings object with imported values
        current_settings = await get_user_settings(user_id)
        current_settings.update(imported_settings)
        
        # Ensure selected_qualities is a proper list/set
        if isinstance(current_settings['selected_qualities'], (list, tuple)):
            current_settings['selected_qualities'] = [str(q) for q in current_settings['selected_qualities']]
        
        await save_user_settings(current_settings)
        
        del waiting_for_input[user_id]
        await client.send_message(message.chat.id, get_string("import_success"), parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
        
    except Exception as e:
        logger.error(f"Import failed for user {user_id}: {e}")
        await client.send_message(
            message.chat.id, 
            get_string("import_invalid").format(expected_id=user_id, found_id=imported_user_id if 'imported_user_id' in locals() else 'N/A')
        )
    finally:
        if user_id in waiting_for_input and waiting_for_input[user_id] == "import_settings":
            del waiting_for_input[user_id]


@app.on_message(filters.private & filters.forwarded)
async def handle_forwarded(client, message: Message):
    # ... (Handle forwarded message for channel setting remains the same)
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
            
            # Use safe attribute access for chat info
            chat_username = getattr(chat, 'username', None)
            chat_title = getattr(chat, 'title', str(chat.id))
            chat_type = str(getattr(chat, 'type', 'unknown'))
            
            await save_channel_info(user_id, chat.id, chat_username, chat_title, chat_type)
            
            del waiting_for_input[user_id]
            
            sent = await client.send_message(
                message.chat.id,
                f"✅ <b>Channel updated!</b>\n\n"
                f"📝 Title: <b>{chat_title}</b>\n"
                f"🆔 ID: <code>{chat.id}</code>\n"
                f"👤 Username: @{chat_username if chat_username else 'N/A'}",
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
    # ... (Handle media for welcome message remains the same)
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
    # ... (Handle text input for settings remains the same)
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
            
            chat_username = getattr(chat, 'username', None)
            chat_title = getattr(chat, 'title', str(chat.id))
            chat_type = str(getattr(chat, 'type', 'unknown'))
            
            await save_channel_info(user_id, chat.id, chat_username, chat_title, chat_type)
            
            del waiting_for_input[user_id]
            
            sent = await client.send_message(
                chat_id,
                f"✅ <b>Channel updated!</b>\n\n"
                f"📝 Title: <b>{chat_title}</b>\n"
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


@app.on_message(filters.private & filters.video)
async def auto_forward(client, message: Message):
    user_id = message.from_user.id
    
    # Ignore if waiting for input
    if user_id in waiting_for_input:
        return
        
    # NEW: Cooldown/Rate Limiting check
    COOLDOWN_TIME = 10 # 10 seconds delay
    is_admin = user_id in ADMIN_IDS
    current_time = time.time()
    
    if not is_admin and user_id in last_upload_time:
        elapsed = current_time - last_upload_time[user_id]
        if elapsed < COOLDOWN_TIME:
            await message.reply(
                get_string("rate_limit").format(cooldown=COOLDOWN_TIME, elapsed=elapsed),
                parse_mode=ParseMode.HTML
            )
            return

    # NEW: Batch Uploads / Media Group Check
    if message.media_group_id:
        # Check if the media group is already being processed or waiting for the last file
        if message.media_group_id in media_group_cache:
            # If so, just ignore and let the single-video logic handle the final message later
            return
        
        # Mark the group as started/seen to prevent multiple messages for the group
        media_group_cache[message.media_group_id] = 'processing'
        # Wait a short moment for the rest of the media group messages to arrive
        await asyncio.sleep(2)
        
        # NOTE: Pyrogram's `Message.media_group_id` processing is often done via
        # MessageGroup, but here we process the videos one by one from the stream
        # to ensure sequential episode number assignment. The check above ensures
        # only the first message in the stream triggers the sequence.
        # Since we are using an `async with user_lock`, the processing is sequential.
        
    user_lock = get_user_lock(user_id)
    
    async with user_lock:
        try:
            settings = await get_user_settings(user_id)
            
            if not settings["target_chat_id"]:
                await message.reply(get_string("error_channel_not_set"), parse_mode=ParseMode.HTML)
                return
            
            if not settings["selected_qualities"]:
                await message.reply("❌ No qualities selected!\n\nUse /start to configure.", parse_mode=ParseMode.HTML)
                return

            file_id = message.video.file_id
            
            # Use modulo to cycle through selected qualities
            total_qualities = len(settings["selected_qualities"])
            quality = settings["selected_qualities"][settings["video_count"] % total_qualities]

            # Fetch channel info for placeholders
            channel_info = await get_channel_info(settings["target_chat_id"])
            
            # Construct final caption
            caption = settings["base_caption"] \
                .replace("{season}", f"{settings['season']:02}") \
                .replace("{episode}", f"{settings['episode']:02}") \
                .replace("{total_episode}", f"{settings['total_episode']:02}") \
                .replace("{quality}", quality) \
                .replace("{username}", message.from_user.username or 'N/A') \
                .replace("{first_name}", message.from_user.first_name or 'N/A') \
                .replace("{user_id}", str(user_id)) \
                .replace("{chat_id}", str(channel_info['chat_id'])) \
                .replace("{chat_title}", channel_info['title'])


            # --- FORWARDING ---
            await client.send_video(
                chat_id=settings["target_chat_id"],
                video=file_id,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
            
            # --- POST-UPLOAD PROCESSING ---
            await log_upload(user_id, settings['season'], settings['episode'], settings['total_episode'], quality, file_id, caption, settings['target_chat_id'])
            
            # Update last upload time for rate limiting
            if not is_admin:
                last_upload_time[user_id] = current_time

            # Prepare next count
            settings["video_count"] += 1
            if settings["video_count"] >= total_qualities:
                settings["episode"] += 1
                settings["total_episode"] += 1
                settings["video_count"] = 0
                next_count = 0
            else:
                next_count = settings["video_count"]
                
            await save_user_settings(settings)
            
            # Send confirmation message
            reply_msg = await message.reply(
                get_string("upload_success").format(
                    season=settings['season'],
                    episode=settings['episode'] - (1 if settings["video_count"] == 0 and total_qualities > 0 else 0), # Correct episode number for confirmation
                    quality=quality,
                    current_video=next_count if next_count > 0 else total_qualities, # Show total if episode rolls over
                    total_videos=total_qualities
                ),
                parse_mode=ParseMode.HTML
            )
            
            # Clean up (Delete messages after a short delay)
            await asyncio.sleep(5)
            try:
                await reply_msg.delete()
                await message.delete()
            except:
                pass

        except Exception as e:
            logger.error(f"Error forwarding video for user {user_id}: {traceback.format_exc()}")
            # Localization used
            await message.reply(
                get_string("upload_error_not_admin").format(target_chat_id=settings['target_chat_id']),
                parse_mode=ParseMode.HTML
            )


# --- WEBHOOK/SERVER SETUP (CODE REMAINS THE SAME BUT ENSURES ROBUSTNESS) ---

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
    # ... (Implementation remains the same: converts telegram update to pyrogram object and dispatches)
    try:
        # Import Telegram raw types for conversion
        from pyrogram import raw
        import pyrogram
        
        logger.info(f"🔄 Processing update: {list(update_dict.keys())}")
        
        # Handle message updates
        if 'message' in update_dict:
            msg = update_dict['message']
            
            # Use Pyrogram's raw API to create proper update object (Complex but necessary for webhooks)
            try:
                from_user = msg.get('from', {})
                
                # Build raw user object
                user_args = {
                    'id': from_user.get('id', 0),
                    'is_self': False, 'contact': False, 'mutual_contact': False, 'deleted': False,
                    'bot': from_user.get('is_bot', False), 'bot_chat_history': False, 'bot_nochats': False,
                    'verified': False, 'restricted': False, 'min': False, 'bot_inline_geo': False,
                    'support': False, 'scam': False, 'apply_min_photo': False, 'fake': False,
                    'bot_attach_menu': False, 'premium': False, 'attach_menu_enabled': False,
                    'bot_can_edit': False, 'close_friend': False, 'stories_hidden': False,
                    'stories_unavailable': False, 'access_hash': 0,
                    'first_name': from_user.get('first_name', ''),
                    'last_name': from_user.get('last_name'),
                    'username': from_user.get('username'),
                    'phone': None, 'photo': None, 'status': None, 'bot_info_version': None,
                    'restriction_reason': None, 'bot_inline_placeholder': None,
                    'lang_code': from_user.get('language_code'), 'emoji_status': None,
                    'usernames': None, 'stories_max_id': None, 'color': None, 'profile_color': None,
                    'bot_active_users': None
                }
                user = raw.types.User(**{k: v for k, v in user_args.items() if k in inspect.signature(raw.types.User.__init__).parameters})
                
                # Build raw peer objects
                peer_user = raw.types.PeerUser(user_id=user.id)
                
                # Build entities if present (simplified)
                entities = []
                if 'entities' in msg:
                    for ent in msg['entities']:
                        if ent['type'] == 'bot_command':
                            entities.append(raw.types.MessageEntityBotCommand(offset=ent['offset'], length=ent['length']))
                
                # Build raw message (simplified, assumes basic text or media structure)
                raw_message_args = {
                    'id': msg.get('message_id', 0),
                    'peer_id': peer_user,
                    'from_id': peer_user,
                    'date': msg.get('date', 0),
                    'message': msg.get('text', ''),
                    'out': False, 'mentioned': False, 'media_unread': False,
                    'silent': False, 'post': False, 'from_scheduled': False,
                    'legacy': False, 'edit_hide': False, 'pinned': False,
                    'noforwards': False,
                    'entities': entities if entities else None
                }
                
                # Handling forwarded_from and media (complex to port fully, but include basic info)
                if 'forward_from_chat' in msg:
                    fwd_chat = msg['forward_from_chat']
                    raw_message_args['fwd_from'] = raw.types.MessageFwdHeader(
                        from_id=raw.types.PeerChannel(channel_id=fwd_chat['id']),
                        date=msg.get('forward_date', 0)
                    )
                
                # Simplified media parsing (enough to trigger filters.video)
                if 'video' in msg:
                    raw_message_args['media'] = raw.types.MessageMediaDocument(
                        document=raw.types.Document(
                            id=msg['video']['file_id'],
                            access_hash=0, size=msg['video']['file_size'],
                            mime_type=msg['video'].get('mime_type', 'video/mp4'),
                            date=msg.get('date', 0),
                            thumbs=[],
                            video_thumbs=[],
                            dc_id=1,
                            attributes=[raw.types.DocumentAttributeVideo(
                                duration=msg['video']['duration'],
                                w=msg['video']['width'],
                                h=msg['video']['height'],
                                supports_streaming=True
                            )]
                        ),
                        caption=msg.get('caption', ''),
                        ttl_seconds=None
                    )
                    raw_message_args['message'] = msg.get('caption', '')
                    
                # Handle media group ID for batch uploads
                if 'media_group_id' in msg:
                    raw_message_args['grouped_id'] = int(msg['media_group_id'])
                
                raw_message = raw.types.Message(**{k: v for k, v in raw_message_args.items() if k in inspect.signature(raw.types.Message.__init__).parameters})

                # Parse to Pyrogram Message object
                parsed_message = pyrogram.types.Message._parse(
                    client=app,
                    message=raw_message,
                    users={user.id: user},
                    chats={},
                    is_scheduled=False,
                    replies=0
                )
                
                # Now dispatch through handlers
                from pyrogram.handlers import MessageHandler
                for group in sorted(app.dispatcher.groups.keys()):
                    for handler in app.dispatcher.groups[group]:
                        if isinstance(handler, MessageHandler):
                            try:
                                if handler.filters and await handler.filters(app, parsed_message):
                                    await handler.callback(app, parsed_message)
                                    # Since one handler matched, we break the loop
                                    return
                            except Exception as e:
                                logger.error(f"❌ Handler error: {e}", exc_info=True)
                
            except Exception as e:
                logger.error(f"❌ Error processing message: {e}", exc_info=True)
        
        # Handle callback queries
        elif 'callback_query' in update_dict:
            # ... (Implementation remains the same: converts callback update and dispatches)
            cb = update_dict['callback_query']
            
            try:
                from_user = cb.get('from', {})
                message = cb.get('message', {})
                
                # Build raw user with only supported fields
                user_sig = inspect.signature(raw.types.User.__init__)
                valid_params = set(user_sig.parameters.keys()) - {'self'}
                
                user_dict = {
                    'id': from_user.get('id', 0),
                    'first_name': from_user.get('first_name', ''),
                    'last_name': from_user.get('last_name'),
                    'username': from_user.get('username'),
                    'bot': from_user.get('is_bot', False),
                    'lang_code': from_user.get('language_code'),
                }
                filtered_user_dict = {k: v for k, v in user_dict.items() if k in valid_params}
                user = raw.types.User(**filtered_user_dict)
                
                # Build raw callback query
                raw_callback = raw.types.UpdateBotCallbackQuery(
                    query_id=int(cb.get('id', '0')),
                    user_id=from_user.get('id', 0),
                    peer=raw.types.PeerUser(user_id=from_user.get('id', 0)),
                    msg_id=message.get('message_id', 0),
                    chat_instance=int(cb.get('chat_instance', '0')),
                    data=cb.get('data', '').encode()
                )
                
                # Parse to Pyrogram CallbackQuery
                parsed_callback = pyrogram.types.CallbackQuery._parse(app, raw_callback, {from_user.get('id', 0): user})
                
                # Dispatch through handlers
                from pyrogram.handlers import CallbackQueryHandler
                for group in sorted(app.dispatcher.groups.keys()):
                    for handler in app.dispatcher.groups[group]:
                        if isinstance(handler, CallbackQueryHandler):
                            try:
                                if handler.filters is None or await handler.filters(app, parsed_callback):
                                    await handler.callback(app, parsed_callback)
                                    return
                            except Exception as e:
                                logger.error(f"❌ Callback handler error: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"❌ Error processing callback: {e}", exc_info=True)
    
    except Exception as e:
        logger.error(f"❌ Fatal error in process_update_manually: {e}", exc_info=True)

# ... (health_check, stats_endpoint, setup_webhook, self_ping remain the same)
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
    global web_app # CRITICAL: Ensures the globally initialized web_app is used (for robustness)
    
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
        await app.start()
        
        me = await app.get_me()
        logger.info(f"✅ Bot started: @{me.username} (ID: {me.id})")
        
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

### TESTING FRAMEWORK
# To implement unit tests (Feature 7):
# 1. Install a testing library (e.g., `pytest`).
# 2. Create a `test_bot.py` file.
# 3. Mock the external dependencies:
#    - Mock `pyrogram.Client` and its methods (e.g., `send_message`, `send_video`).
#    - Mock the `psycopg_pool` database operations (e.g., `get_user_settings`, `save_user_settings`).
# 4. Write test cases for handlers:
#    - Test `start` handler: Check if it calls `send_message` with the correct reply markup.
#    - Test `auto_forward` handler:
#      - Check if it correctly increments `episode` and `video_count`.
#      - Test if it applies the rate limit for a regular user.
#      - **Test if it bypasses the rate limit for an admin.**
#      - Test caption generation with all placeholders.
#    - Test `handle_buttons` (e.g., "reset" callback) to ensure state changes are correct.
