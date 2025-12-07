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
import httpx
import psycopg
from psycopg_pool import AsyncConnectionPool
from datetime import datetime
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Bot credentials and config
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
PORT = int(os.getenv('PORT', '10000'))
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL', '')
DATABASE_URL = os.getenv('DATABASE_URL', '')

# Admin IDs
ADMIN_IDS = [
    int(id.strip()) for id in os.getenv('ADMIN_IDS', '').split(',') if id.strip()
]

if not ADMIN_IDS:
    ADMIN_IDS = [123456789]

logger.info(f"🔧 Admin IDs configured: {ADMIN_IDS}")

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

logger.info("🔧 Pyrogram Client initialized")

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
        [InlineKeyboardButton("🔄 Preview Caption", callback_data="preview")],
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


# Handler functions

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
    
    await delete_last_message(client, message.chat.id)
    
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
        "Contact..."
    )
    
    sent = await client.send_message(
        message.chat.id,
        help_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_menu_markup() if message.from_user.id not in ADMIN_IDS else get_admin_menu_markup()
    )
    last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & filters.command("stats"))
async def stats_command(client, message):
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
        f"• Total Episodes: <code>{settings['total_episode']}</code>\n\n"
        f"🎯 <b>Channel:</b> {channel_status}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_menu_markup()
    )
    last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & filters.command("admin"))
async def admin_command(client, message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("❌ **Access Denied**. You are not an admin.")
        return
        
    try:
        await message.delete()
    except:
        pass
        
    await delete_last_message(client, message.chat.id)
    
    sent = await client.send_message(
        message.chat.id,
        "👑 **Admin Panel**\n\nWelcome, Admin. Use the buttons below to manage global settings.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_menu_markup()
    )
    last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & (filters.text | filters.sticker) & filters.incoming & ~filters.command(["start", "help", "stats", "admin"]))
async def handle_user_input(client, message):
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
                sent = await client.send_message(chat_id, "✅ Caption template updated!", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
                last_bot_messages[chat_id] = sent.id
            else:
                sent = await client.send_message(chat_id, "❌ Please enter a valid text caption.", parse_mode=ParseMode.HTML)
                last_bot_messages[chat_id] = sent.id
                
        elif input_type == "season":
            if message.text and message.text.isdigit() and int(message.text) > 0:
                settings["season"] = int(message.text)
                await save_user_settings(settings)
                del waiting_for_input[user_id]
                sent = await client.send_message(chat_id, f"✅ Season updated to {settings['season']}!", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
                last_bot_messages[chat_id] = sent.id
            else:
                sent = await client.send_message(chat_id, "❌ Please enter a valid number greater than 0.", parse_mode=ParseMode.HTML)
                last_bot_messages[chat_id] = sent.id

        elif input_type == "episode":
            if message.text and message.text.isdigit() and int(message.text) > 0:
                settings["episode"] = int(message.text)
                settings["video_count"] = 0
                await save_user_settings(settings)
                del waiting_for_input[user_id]
                sent = await client.send_message(chat_id, f"✅ Episode updated to {settings['episode']}! Video count reset.", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
                last_bot_messages[chat_id] = sent.id
            else:
                sent = await client.send_message(chat_id, "❌ Please enter a valid number greater than 0.", parse_mode=ParseMode.HTML)
                last_bot_messages[chat_id] = sent.id

        elif input_type == "total_episode":
            if message.text and message.text.isdigit() and int(message.text) > 0:
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
                    f"❌ Error: Could not find channel or bot is not admin.\n\n{str(e)}", 
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
                        sent = await client.send_message(chat_id, "✅ Welcome message updated!", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_markup())
                        last_bot_messages[chat_id] = sent.id
                    else:
                        sent = await client.send_message(chat_id, "❌ Failed to save welcome message to database.", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_markup())
                        last_bot_messages[chat_id] = sent.id
                else:
                    del waiting_for_input[user_id]
                    sent = await client.send_message(chat_id, "⚠️ **Error:** Media data lost. Please start over from `/admin`.", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_markup())
                    last_bot_messages[chat_id] = sent.id
            else:
                sent = await client.send_message(chat_id, "❌ Please enter a valid text caption.", parse_mode=ParseMode.HTML)
                last_bot_messages[chat_id] = sent.id


@app.on_message(filters.private & filters.forwarded)
async def handle_forward(client, message):
    user_id = message.from_user.id
    
    # Check if user is waiting to forward a channel message
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
                "❌ Please forward from a **channel** or **group**, not a user.", 
                parse_mode=ParseMode.HTML
            )
            last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & (filters.photo | filters.video | filters.animation))
async def handle_media_for_welcome(client, message: Message):
    user_id = message.from_user.id
    # Only process if admin is setting welcome message
    if user_id not in waiting_for_input or waiting_for_input[user_id] != "admin_welcome":
        return 
    
    # Only admins can set welcome media
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
        waiting_for_input[f"{user_id}_welcome_data"] = {
            'message_type': message_type,
            'file_id': file_id
        }
        
        waiting_for_input[user_id] = "admin_welcome_caption"
        
        caption_prompt = (
            f"🖼️ **Media received!** Type: `{message_type}`.\n\n"
            "Now, **send the final caption** (HTML supported).\n"
            "**Current Caption Draft:**\n"
            f"```\n{caption if caption else 'No caption'}\n```\n\n"
            "**Placeholders:**\n"
            "• `{first_name}`: User's first name\n"
            "• `{user_id}`: User's Telegram ID\n"
            "• Use `//` to use your draft caption."
        )
        
        sent = await client.send_message(
            message.chat.id,
            caption_prompt,
            parse_mode=ParseMode.MARKDOWN
        )
        last_bot_messages[message.chat.id] = sent.id
    else:
        sent = await client.send_message(
            message.chat.id,
            "❌ Only **Photo**, **Video**, or **GIF/Animation** are supported for the welcome message.",
            reply_markup=get_admin_menu_markup()
        )
        last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & filters.video & ~filters.forwarded & ~filters.media_group)
async def auto_forward(client, message):
    user_id = message.from_user.id
    
    # Ignore if waiting for input
    if user_id in waiting_for_input:
        return
        
    user_lock = get_user_lock(user_id)
    async with user_lock:
        try:
            settings = await get_user_settings(user_id)
            target_chat_id = settings.get('target_chat_id')
            
            if not target_chat_id:
                await message.reply_text(
                    "⚠️ **Error:** No target channel set.\n"
                    "Use the main menu or `/start` to set your target channel first.",
                    parse_mode=ParseMode.HTML
                )
                return

            selected_qualities = settings.get('selected_qualities', [])
            if not selected_qualities:
                await message.reply_text(
                    "⚠️ **Error:** No video qualities selected.\n"
                    "Use the main menu or `/start` to configure your quality settings.",
                    parse_mode=ParseMode.HTML
                )
                return

            quality_index = settings['video_count'] % len(selected_qualities)
            current_quality = selected_qualities[quality_index]
            
            current_caption = settings["base_caption"] \
                .replace("{season}", f"{settings['season']:02}") \
                .replace("{episode}", f"{settings['episode']:02}") \
                .replace("{total_episode}", f"{settings['total_episode']:02}") \
                .replace("{quality}", current_quality)

            await client.copy_message(
                chat_id=target_chat_id,
                from_chat_id=message.chat.id,
                message_id=message.id,
                caption=current_caption,
                parse_mode=ParseMode.HTML
            )
            
            await log_upload(
                user_id, settings['season'], settings['episode'], 
                settings['total_episode'], current_quality, 
                message.video.file_id if message.video else "N/A", 
                current_caption, target_chat_id
            )
            
            settings['video_count'] += 1
            
            if settings['video_count'] >= len(selected_qualities):
                settings['episode'] += 1
                settings['video_count'] = 0
                
                await message.reply_text(
                    f"✅ **Upload Complete!**\n\n"
                    f"Episode {settings['episode']-1} successfully uploaded in all qualities.\n"
                    f"Next episode: **{settings['episode']}** (Season {settings['season']}).",
                    parse_mode=ParseMode.HTML
                )
            else:
                next_quality_index = settings['video_count'] % len(selected_qualities)
                next_quality = selected_qualities[next_quality_index]
                
                await message.reply_text(
                    f"✅ **Upload Successful!**\n\n"
                    f"Quality: **{current_quality}** (Video {settings['video_count']}/{len(selected_qualities)}).\n"
                    f"Next quality to upload: **{next_quality}**.",
                    parse_mode=ParseMode.HTML
                )

            await save_user_settings(settings)
            
        except Exception as e:
            logger.error(f"Error during auto-forward for user {user_id}: {e}", exc_info=True)
            await message.reply_text(
                f"❌ **An Error Occurred**:\n\n`{e}`\n\n"
                "Possible reasons:\n"
                "• Bot is not an **Admin** in the target channel.\n"
                "• Target channel ID is incorrect/private.\n"
                "• The video file is corrupted or too large."
            )


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
    
    # --- ADMIN ACTIONS ---
    if data.startswith("admin_") and user_id not in ADMIN_IDS:
        await callback_query.message.reply("❌ **Access Denied**. This feature is for admins only.", parse_mode=ParseMode.HTML)
        return

    if data == "admin_set_welcome":
        waiting_for_input[user_id] = "admin_welcome"
        sent = await callback_query.message.reply(
            "📝 **Set Welcome Message**\n\n"
            "**1. Send Media (Photo, Video, or GIF)** with your desired caption **draft**.\n"
            "**2. The bot will then ask for the final caption.**\n\n"
            "Use `/admin` to cancel.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_admin_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id
        return

    elif data == "admin_preview_welcome":
        welcome_data = await get_welcome_message()
        if not welcome_data:
            text = "⚠️ **No custom welcome message set.**"
            sent = await callback_query.message.reply(text, parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_markup())
        else:
            text = (
                f"👁️ **Welcome Message Preview** (Type: `{welcome_data['message_type']}`)\n\n"
                f"**Caption:**\n{welcome_data['caption'].format(first_name='Test User', user_id=123456789)}\n\n"
                f"**Media ID:**\n<code>{welcome_data['file_id']}</code>"
            )
            try:
                if welcome_data['message_type'] == 'photo':
                    await client.send_photo(chat_id, welcome_data['file_id'], caption=text, parse_mode=ParseMode.HTML)
                elif welcome_data['message_type'] == 'video':
                    await client.send_video(chat_id, welcome_data['file_id'], caption=text, parse_mode=ParseMode.HTML)
                elif welcome_data['message_type'] == 'animation':
                    await client.send_animation(chat_id, welcome_data['file_id'], caption=text, parse_mode=ParseMode.HTML)
                else:
                    await client.send_message(chat_id, text, parse_mode=ParseMode.HTML)
                
            except Exception as e:
                text += f"\n\n❌ **Error during media preview:** `{e}`"
                await client.send_message(chat_id, text, parse_mode=ParseMode.HTML)
            
            sent = await client.send_message(chat_id, "Use the buttons below.", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_markup())
        
        last_bot_messages[chat_id] = sent.id
        return

    elif data == "admin_global_stats":
        total_users = await get_all_users_count()
        
        sent = await callback_query.message.reply(
            f"📊 **Global Statistics**\n\n"
            f"👥 **Total Users:** <code>{total_users}</code>\n"
            f"💾 **DB Status:** {'✅ Connected' if db_pool else '❌ JSON Fallback'}\n"
            f"🔗 **Render URL:** <code>{RENDER_EXTERNAL_URL}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_admin_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id
        return

    # --- USER ACTIONS ---
    elif data == "preview":
        if not settings['target_chat_id']:
            target_chat_display = "❌ Not Set"
        else:
            target_chat_display = f"<code>{settings['target_chat_id']}</code>"
            
        quality = settings["selected_qualities"][settings["video_count"] % len(settings["selected_qualities"])] if settings["selected_qualities"] else "N/A"
        
        preview_text = settings["base_caption"] \
            .replace("{season}", f"{settings['season']:02}") \
            .replace("{episode}", f"{settings['episode']:02}") \
            .replace("{total_episode}", f"{settings['total_episode']:02}") \
            .replace("{quality}", quality)
            
        sent = await callback_query.message.reply(
            f"🔄 <b>Preview Caption:</b>\n\n{preview_text}\n\n"
            f"<b>Current Settings:</b>\n"
            f"Season: {settings['season']}\n"
            f"Episode: {settings['episode']}\n"
            f"Videos Done: {settings['video_count']}/{len(settings['selected_qualities'])}\n"
            f"Next Quality: {quality}\n"
            f"Channel ID: {target_chat_display}\n"
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
            "• <code>{season}</code> (e.g., 01)\n"
            "• <code>{episode}</code> (e.g., 10)\n"
            "• <code>{total_episode}</code> (e.g., 03 - total qualities)\n"
            "• <code>{quality}</code> (e.g., 1080p)\n\n"
            f"<b>Current Caption:</b>\n{settings['base_caption']}",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_season":
        waiting_for_input[user_id] = "season"
        sent = await callback_query.message.reply(
            f"📺 <b>Set Season Number</b>\n\n"
            f"Send the new season number (current: <code>{settings['season']}</code>).",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_episode":
        waiting_for_input[user_id] = "episode"
        sent = await callback_query.message.reply(
            f"🎬 <b>Set Episode Number</b>\n\n"
            f"Send the new episode number (current: <code>{settings['episode']}</code>).\n\n"
            "⚠️ **Note:** This resets your video count for the current episode.",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "set_total_episode":
        waiting_for_input[user_id] = "total_episode"
        sent = await callback_query.message.reply(
            f"🔢 <b>Set Total Qualities per Episode</b>\n\n"
            f"Send the total number of video files you will upload per episode (e.g., 3 for 3 qualities).\n"
            f"Current: <code>{settings['total_episode']}</code>.",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "quality_menu":
        sent = await callback_query.message.reply(
            "🎥 <b>Quality Settings</b>\n\n"
            "Toggle the video qualities you plan to upload for each episode. The bot will cycle through these in order.",
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
                
            selected_qualities.sort(key=lambda q: ALL_QUALITIES.index(q))
            
            settings['selected_qualities'] = selected_qualities
            
            await save_user_settings(settings)
            
            try:
                await callback_query.message.edit_text(
                    "🎥 <b>Quality Settings</b>\n\n"
                    "Toggle the video qualities you plan to upload for each episode. The bot will cycle through these in order.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_quality_markup(settings['selected_qualities'])
                )
            except:
                pass
                
    elif data == "set_channel":
        sent = await callback_query.message.reply(
            "🎯 <b>Set Target Channel</b>\n\n"
            "How would you like to set your target channel?\n\n"
            "⚠️ **Note:** The bot must be an **admin** in the target channel!",
            parse_mode=ParseMode.HTML,
            reply_markup=get_channel_set_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "forward_channel":
        waiting_for_input[user_id] = "forward_channel"
        sent = await callback_query.message.reply(
            "📤 **Forward a Message from your Target Channel**\n\n"
            "Please forward any message from your target channel/group now.\n\n"
            "⚠️ Make sure I'm an admin!",
            parse_mode=ParseMode.HTML
        )
        last_bot_messages[chat_id] = sent.id
        
    elif data == "send_channel_id":
        waiting_for_input[user_id] = "channel_id"
        sent = await callback_query.message.reply(
            "🔗 **Send Channel Username or ID**\n\n"
            "Send the channel username (e.g., `@mychannel`) or ID (e.g., `-1001234567890`).\n\n"
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
            f"• Total Qualities: <code>{settings['total_episode']}</code>\n\n"
            f"🎯 <b>Channel:</b> {channel_status}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "back_to_main":
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
            "🔄 **Episode Progress Reset!**\n\n"
            "Episode is set back to **1** and video count is **0**.",
            parse_mode=ParseMode.HTML, 
            reply_markup=get_menu_markup()
        )
        last_bot_messages[chat_id] = sent.id

    elif data == "cancel":
        if user_id in waiting_for_input:
            del waiting_for_input[user_id]
        if f"{user_id}_welcome_data" in waiting_for_input:
            del waiting_for_input[f"{user_id}_welcome_data"]
            
        try:
            await callback_query.message.delete()
        except:
            pass


# --- WEB SERVER AND CONCURRENT EXECUTION ---

async def health_check(request):
    """Health check endpoint for Render to keep the service alive."""
    total_handlers_registered = sum(len(handlers) for handlers in app.dispatcher.groups.values())
    if total_handlers_registered == 0:
        logger.warning("⚠️ Health check found zero handlers. Bot may be non-responsive.")
    
    return web.Response(text=f"Bot Running. Handlers found: {total_handlers_registered}", status=200)

async def self_ping():
    """Pings the health check endpoint periodically to keep the Render service awake."""
    if not RENDER_EXTERNAL_URL:
        logger.warning("⚠️ RENDER_EXTERNAL_URL is not set. Self-ping is disabled.")
        return

    health_url = f"{RENDER_EXTERNAL_URL}/health"
    logger.info(f"🔗 Starting self-ping to {health_url}")
    
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                response = await client.get(health_url)
                if response.status_code != 200:
                    logger.warning(f"Self-ping failed (Status {response.status_code}).")
                else:
                    logger.debug(f"Self-ping successful (Status {response.status_code}).")
                
                await asyncio.sleep(600)
            except Exception as e:
                logger.error(f"❌ Self-ping error: {e}")
                await asyncio.sleep(600)

async def start_web_server():
    """Sets up the web application and binds to the Render port."""
    web_app.add_routes([web.get("/health", health_check)])
    
    runner = web.AppRunner(web_app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    
    logger.info(f"🌐 Starting web server on 0.0.0.0:{PORT}")
    await site.start()
    
    while True:
        await asyncio.sleep(3600)

async def run_forever():
    """Keeps the Pyrogram client's event loop running indefinitely."""
    logger.info("Keep-Alive Task running...")
    while True:
        await asyncio.sleep(3600)


async def main():
    await init_db()
    
    try:
        await app.start()
        logger.info("✅ Pyrogram Client started successfully.")
        
        logger.info("📡 Running in CONCURRENT POLLING mode (Keep-Alive + Web Server).")
        logger.info("=" * 50)
        logger.info("✅ ALL SYSTEMS OPERATIONAL")
        logger.info("=" * 50)
        
        await asyncio.gather(
            run_forever(),
            start_web_server(),
            self_ping(),
            return_exceptions=False
        )

    except Exception as e:
        logger.error(f"❌ Critical error in main execution: {e}", exc_info=True)
    finally:
        logger.info("🛑 Shutting down...")
        try:
            await app.stop()
        except Exception as e:
            logger.error(f"Error during client shutdown: {e}")
        if db_pool:
            try:
                await db_pool.close()
            except Exception as e:
                logger.error(f"Error during DB pool close: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down bot via KeyboardInterrupt.")
    except Exception as e:
        logger.error(f"Top-level execution error: {e}")
