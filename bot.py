# ---------- bot (final fixed) ----------
# Preserves all original features. Fixes webhook processing for Pyrogram v2.x (awaits _parse).
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
from psycopg_pool import AsyncConnectionPool
from datetime import datetime
import logging

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Environment / config
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
PORT = int(os.getenv('PORT', '10000'))
RENDER_EXTERNAL_URL = os.getenv('RENDER_EXTERNAL_URL', '')
DATABASE_URL = os.getenv('DATABASE_URL', '')

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else ''

ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]
if not ADMIN_IDS:
    ADMIN_IDS = [123456789]  # change to your admin id(s)

logger.info(f"🔧 Admin IDs configured: {ADMIN_IDS}")
logger.info(f"🔧 Webhook URL: {WEBHOOK_URL if WEBHOOK_URL else 'Not configured (using polling)'}")

# Defaults
ALL_QUALITIES = ["480p", "720p", "1080p", "4K", "2160p"]
DEFAULT_CAPTION = ("<b>Anime</b> - <i>@Your_Channel</i>\n"
                  "Season {season} - Episode {episode} ({total_episode}) - {quality}\n"
                  "<blockquote>Don't miss this episode!</blockquote>")

# DB pool placeholder
db_pool = None

# Pyrogram client
app = Client(
    "auto_caption_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=4,
    in_memory=True
)

logger.info("🔧 Pyrogram Client initialized")

# Runtime state
waiting_for_input = {}
last_bot_messages = {}
user_locks = {}

# aiohttp app
web_app = web.Application()

# -------------------------
# Utilities
# -------------------------
def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

# -------------------------
# Database init & helpers
# -------------------------
async def init_db():
    global db_pool
    if DATABASE_URL:
        try:
            db_pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False)
            await db_pool.open()
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    # Create tables if not exist (keeps original schema)
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
                    await cur.execute('CREATE INDEX IF NOT EXISTS idx_upload_history_user_id ON upload_history(user_id)')
                    await cur.execute('CREATE INDEX IF NOT EXISTS idx_upload_history_uploaded_at ON upload_history(uploaded_at)')
                await conn.commit()
            logger.info("✅ PostgreSQL database initialized successfully")
        except Exception as e:
            logger.exception("❌ Database initialization failed; falling back to JSON storage.")
            db_pool = None
    else:
        logger.info("⚠️ DATABASE_URL not set — using JSON fallback storage")

# user settings
async def get_user_settings(user_id, username=None, first_name=None):
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT * FROM user_settings WHERE user_id = %s', (user_id,))
                    row = await cur.fetchone()
                    if row:
                        colnames = [desc[0] for desc in cur.description]
                        r = dict(zip(colnames, row))
                        return {
                            'user_id': r['user_id'],
                            'season': r['season'],
                            'episode': r['episode'],
                            'total_episode': r['total_episode'],
                            'video_count': r['video_count'],
                            'selected_qualities': r['selected_qualities'].split(',') if r['selected_qualities'] else [],
                            'base_caption': r['base_caption'],
                            'target_chat_id': r['target_chat_id']
                        }
                    # create default
                    default = {
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
                        INSERT INTO user_settings (user_id, username, first_name, season, episode, total_episode, video_count, selected_qualities, base_caption, target_chat_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (user_id, username, first_name, 1, 1, 1, 0, '480p,720p,1080p', DEFAULT_CAPTION, None))
                    await conn.commit()
                    return default
        except Exception:
            logger.exception("Error reading/saving user settings from DB; falling back to JSON")
    # JSON fallback
    p = Path(f"user_{user_id}_progress.json")
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            logger.exception("Error reading JSON user settings; returning default")
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
    user_id = settings['user_id']
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('''
                        UPDATE user_settings SET season=%s, episode=%s, total_episode=%s, video_count=%s,
                            selected_qualities=%s, base_caption=%s, target_chat_id=%s, updated_at=CURRENT_TIMESTAMP
                        WHERE user_id=%s
                    ''', (settings['season'], settings['episode'], settings['total_episode'],
                          settings.get('video_count', 0),
                          ','.join(settings['selected_qualities']) if settings.get('selected_qualities') else None,
                          settings['base_caption'], settings.get('target_chat_id'), user_id))
                await conn.commit()
            return
        except Exception:
            logger.exception("Error saving user settings to DB; will fallback to JSON")
    # JSON fallback
    p = Path(f"user_{user_id}_progress.json")
    p.write_text(json.dumps(settings, indent=2))

async def log_upload(user_id, season, episode, total_episode, quality, file_id, caption, target_chat_id):
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('''
                        INSERT INTO upload_history (user_id, season, episode, total_episode, quality, file_id, caption, target_chat_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ''', (user_id, season, episode, total_episode, quality, file_id, caption, target_chat_id))
                await conn.commit()
        except Exception:
            logger.exception("Error logging upload to DB")

async def save_channel_info(user_id, chat_id, username, title, chat_type):
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('''
                        INSERT INTO channel_info (user_id, chat_id, username, title, type)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (user_id, chat_id) DO UPDATE SET username=EXCLUDED.username, title=EXCLUDED.title, type=EXCLUDED.type, added_at=CURRENT_TIMESTAMP
                    ''', (user_id, chat_id, username, title, chat_type))
                await conn.commit()
        except Exception:
            logger.exception("Error saving channel info")

async def get_user_upload_stats(user_id):
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT COUNT(*) FROM upload_history WHERE user_id=%s', (user_id,))
                    total = (await cur.fetchone())[0] or 0
                    await cur.execute('SELECT COUNT(*) FROM upload_history WHERE user_id=%s AND DATE(uploaded_at)=CURRENT_DATE', (user_id,))
                    today = (await cur.fetchone())[0] or 0
                    return total, today
        except Exception:
            logger.exception("Error reading stats from DB")
    return 0, 0

async def get_all_users_count():
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT COUNT(*) FROM user_settings')
                    return (await cur.fetchone())[0] or 0
        except Exception:
            logger.exception("Error counting users")
    # fallback to counting JSON files (not ideal but works)
    return len(list(Path('.').glob('user_*_progress.json')))

async def get_welcome_message():
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('SELECT * FROM welcome_settings ORDER BY id DESC LIMIT 1')
                    row = await cur.fetchone()
                    if row:
                        cols = [d[0] for d in cur.description]
                        r = dict(zip(cols, row))
                        return {'message_type': r['message_type'], 'file_id': r['file_id'], 'caption': r['caption']}
        except Exception:
            logger.exception("Error fetching welcome message")
    return None

async def save_welcome_message(message_type, file_id, caption):
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('DELETE FROM welcome_settings')
                    await cur.execute('INSERT INTO welcome_settings (message_type, file_id, caption) VALUES (%s,%s,%s)', (message_type, file_id, caption))
                await conn.commit()
            return True
        except Exception:
            logger.exception("Error saving welcome message")
    return False

async def delete_last_message(client, chat_id):
    if chat_id in last_bot_messages:
        try:
            await client.delete_messages(chat_id, last_bot_messages[chat_id])
        except Exception:
            pass
        del last_bot_messages[chat_id]

# -------------------------
# Markups
# -------------------------
def get_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Preview Caption", callback_data="preview")],
        [InlineKeyboardButton("✏️ Set Caption", callback_data="set_caption")],
        [InlineKeyboardButton("📺 Set Season", callback_data="set_season"),
         InlineKeyboardButton("🎬 Set Episode", callback_data="set_episode")],
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
        [[InlineKeyboardButton(f"{'✅ ' if q in selected_qualities else ''}{q}", callback_data=f"toggle_quality_{q}")] for q in ALL_QUALITIES]
        + [[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")]]
    )

def get_channel_set_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Forward Message", callback_data="forward_channel")],
        [InlineKeyboardButton("🔗 Send Username/ID", callback_data="send_channel_id")],
        [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="back_to_main")]
    ])

# -------------------------
# Handlers (kept same as your original file)
# -------------------------
@app.on_message(filters.private & filters.command("start"))
async def start(client, message):
    logger.info(f"📨 /start from user {message.from_user.id} (@{message.from_user.username})")
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    try:
        settings = await get_user_settings(user_id, username, first_name)
        try:
            await message.delete()
        except Exception:
            pass
    except Exception:
        logger.exception("Error in start hook")
    await delete_last_message(client, message.chat.id)
    welcome_data = await get_welcome_message()
    if welcome_data and welcome_data['file_id']:
        try:
            if welcome_data['message_type'] == 'photo':
                sent = await client.send_photo(message.chat.id, photo=welcome_data['file_id'],
                                               caption=welcome_data['caption'].format(first_name=first_name, user_id=user_id),
                                               parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            elif welcome_data['message_type'] == 'video':
                sent = await client.send_video(message.chat.id, video=welcome_data['file_id'],
                                               caption=welcome_data['caption'].format(first_name=first_name, user_id=user_id),
                                               parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            elif welcome_data['message_type'] == 'animation':
                sent = await client.send_animation(message.chat.id, animation=welcome_data['file_id'],
                                                   caption=welcome_data['caption'].format(first_name=first_name, user_id=user_id),
                                                   parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            else:
                sent = await client.send_message(message.chat.id, welcome_data['caption'].format(first_name=first_name, user_id=user_id),
                                                 parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[message.chat.id] = sent.id
            return
        except Exception:
            logger.exception("Error sending custom welcome")
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
    sent = await client.send_message(message.chat.id, welcome_text, parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
    last_bot_messages[message.chat.id] = sent.id

@app.on_message(filters.private & filters.command("help"))
async def help_command(client, message):
    try:
        await message.delete()
    except:
        pass
    help_text = (
        "📚 <b>Bot Commands & Features</b>\n\n"
        "/start - show menu\n"
        "/help - this message\n"
        "/stats - your stats\n"
        "/admin - admin panel (admins only)\n"
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
        f"📤 <b>Uploads:</b>\n• Total: <code>{total}</code>\n• Today: <code>{today}</code>\n\n"
        f"📺 <b>Progress:</b>\n• Season: <code>{settings['season']}</code>\n• Episode: <code>{settings['episode']}</code>\n• Total Episodes: <code>{settings['total_episode']}</code>\n\n"
        f"🎯 <b>Channel:</b> {channel_status}"
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
        await message.reply("❌ <b>Access Denied!</b>\nYou are not an admin.", parse_mode=ParseMode.HTML)
        return
    total_users = await get_all_users_count()
    admin_text = (f"👑 <b>Admin Panel</b>\n\nTotal Users: <code>{total_users}</code>\n")
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

    # (kept identical logic for all callback cases)
    if data == "preview":
        if not settings['target_chat_id']:
            sent = await callback_query.message.reply("⚠️ <b>No target channel set!</b>", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
            return
        quality = settings["selected_qualities"][settings["video_count"] % len(settings["selected_qualities"])] if settings["selected_qualities"] else "N/A"
        preview_text = settings["base_caption"].replace("{season}", f"{settings['season']:02}").replace("{episode}", f"{settings['episode']:02}").replace("{total_episode}", f"{settings['total_episode']:02}").replace("{quality}", quality)
        sent = await callback_query.message.reply(f"📝 <b>Preview Caption:</b>\n\n{preview_text}", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
        last_bot_messages[chat_id] = sent.id

    elif data == "set_caption":
        waiting_for_input[user_id] = "caption"
        sent = await callback_query.message.reply("✏️ Send the new caption template (HTML). Placeholders: {season},{episode},{total_episode},{quality}", parse_mode=ParseMode.HTML)
        last_bot_messages[chat_id] = sent.id

    elif data == "set_season":
        waiting_for_input[user_id] = "season"
        sent = await callback_query.message.reply("📺 Send the new season number.", parse_mode=ParseMode.HTML)
        last_bot_messages[chat_id] = sent.id

    elif data == "set_episode":
        waiting_for_input[user_id] = "episode"
        sent = await callback_query.message.reply("🎬 Send the new episode number.", parse_mode=ParseMode.HTML)
        last_bot_messages[chat_id] = sent.id

    elif data == "set_total_episode":
        waiting_for_input[user_id] = "total_episode"
        sent = await callback_query.message.reply("🔢 Send total episodes count.", parse_mode=ParseMode.HTML)
        last_bot_messages[chat_id] = sent.id

    elif data == "quality_menu":
        sent = await callback_query.message.reply("🎥 Quality settings", parse_mode=ParseMode.HTML, reply_markup=get_quality_markup(settings['selected_qualities']))
        last_bot_messages[chat_id] = sent.id

    elif data.startswith("toggle_quality_"):
        q = data.replace("toggle_quality_", "")
        if q in settings['selected_qualities']:
            settings['selected_qualities'].remove(q)
        else:
            settings['selected_qualities'].append(q)
        settings['selected_qualities'] = [x for x in ALL_QUALITIES if x in settings['selected_qualities']]
        await save_user_settings(settings)
        try:
            await callback_query.message.edit_text("🎥 Quality settings updated.", reply_markup=get_quality_markup(settings['selected_qualities']), parse_mode=ParseMode.HTML)
        except:
            pass

    elif data == "set_channel":
        sent = await callback_query.message.reply("🎯 Choose how to set channel", parse_mode=ParseMode.HTML, reply_markup=get_channel_set_markup())
        last_bot_messages[chat_id] = sent.id

    elif data == "forward_channel":
        waiting_for_input[user_id] = "forward_channel"
        sent = await callback_query.message.reply("📤 Forward a message from your channel (make the bot admin in channel).", parse_mode=ParseMode.HTML)
        last_bot_messages[chat_id] = sent.id

    elif data == "send_channel_id":
        waiting_for_input[user_id] = "channel_id"
        sent = await callback_query.message.reply("🔗 Send the channel username (e.g. @mychannel) or ID (e.g. -1001234567890).", parse_mode=ParseMode.HTML)
        last_bot_messages[chat_id] = sent.id

    elif data == "stats":
        total, today = await get_user_upload_stats(user_id)
        channel_status = "✅ Set" if settings['target_chat_id'] else "❌ Not Set"
        sent = await callback_query.message.reply(f"📊 Total: {total} • Today: {today}\nChannel: {channel_status}", parse_mode=ParseMode.HTML)
        last_bot_messages[chat_id] = sent.id

    elif data == "reset":
        settings['episode'] = 1
        settings['video_count'] = 0
        await save_user_settings(settings)
        sent = await callback_query.message.reply("🔄 Episode counter reset.", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
        last_bot_messages[chat_id] = sent.id

    elif data == "cancel":
        if user_id in waiting_for_input:
            del waiting_for_input[user_id]
            sent = await callback_query.message.reply("❌ Process cancelled.", reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await callback_query.message.reply("No process to cancel.", reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id

    elif data == "admin_set_welcome":
        if user_id not in ADMIN_IDS:
            await callback_query.answer("❌ Admin only", show_alert=True)
            return
        waiting_for_input[user_id] = "admin_welcome"
        sent = await callback_query.message.reply("📝 Send photo/video/GIF with caption to use as welcome.", parse_mode=ParseMode.HTML)
        last_bot_messages[chat_id] = sent.id

    elif data == "admin_preview_welcome":
        welcome = await get_welcome_message()
        if welcome and welcome.get('file_id'):
            try:
                if welcome['message_type'] == 'photo':
                    await client.send_photo(chat_id, welcome['file_id'], caption=welcome['caption'], parse_mode=ParseMode.HTML)
                elif welcome['message_type'] == 'video':
                    await client.send_video(chat_id, welcome['file_id'], caption=welcome['caption'], parse_mode=ParseMode.HTML)
                elif welcome['message_type'] == 'animation':
                    await client.send_animation(chat_id, welcome['file_id'], caption=welcome['caption'], parse_mode=ParseMode.HTML)
            except Exception:
                logger.exception("Error previewing welcome")
        else:
            sent = await callback_query.message.reply("No welcome message set.", reply_markup=get_admin_menu_markup())
            last_bot_messages[chat_id] = sent.id

    elif data == "admin_global_stats":
        total_users = await get_all_users_count()
        sent = await callback_query.message.reply(f"📊 Total users: {total_users}\nDB: {'✅' if db_pool else '⚠️ JSON'}", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_markup())
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
            settings['target_chat_id'] = chat.id
            await save_user_settings(settings)
            await save_channel_info(user_id, chat.id, chat.username if chat.username else None, chat.title, str(chat.type))
            del waiting_for_input[user_id]
            sent = await client.send_message(message.chat.id, f"✅ Channel updated!\nID: <code>{chat.id}</code>", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[message.chat.id] = sent.id
        else:
            sent = await client.send_message(message.chat.id, "❌ Please forward from a channel.", parse_mode=ParseMode.HTML)
            last_bot_messages[message.chat.id] = sent.id

@app.on_message(filters.private & (filters.photo | filters.video | filters.animation))
async def handle_media_for_welcome(client, message: Message):
    user_id = message.from_user.id
    if user_id not in waiting_for_input or waiting_for_input[user_id] != "admin_welcome":
        return
    try:
        await message.delete()
    except:
        pass
    await delete_last_message(client, message.chat.id)
    msg_type, file_id = None, None
    caption = message.caption or "Welcome!"
    if message.photo:
        msg_type, file_id = 'photo', message.photo.file_id
    elif message.video:
        msg_type, file_id = 'video', message.video.file_id
    elif message.animation:
        msg_type, file_id = 'animation', message.animation.file_id
    if msg_type and file_id:
        ok = await save_welcome_message(msg_type, file_id, caption)
        if ok:
            del waiting_for_input[user_id]
            sent = await client.send_message(message.chat.id, f"✅ Welcome message updated ({msg_type})", parse_mode=ParseMode.HTML, reply_markup=get_admin_menu_markup())
            last_bot_messages[message.chat.id] = sent.id

@app.on_message(filters.private & filters.text & ~filters.forwarded)
async def receive_input(client, message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id
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
        settings['base_caption'] = message.text
        await save_user_settings(settings)
        del waiting_for_input[user_id]
        sent = await client.send_message(chat_id, "✅ Caption updated!", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
        last_bot_messages[chat_id] = sent.id
    elif input_type == "season":
        if message.text.isdigit():
            settings['season'] = int(message.text)
            await save_user_settings(settings)
            del waiting_for_input[user_id]
            sent = await client.send_message(chat_id, f"✅ Season set to {settings['season']}", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(chat_id, "❌ Please enter a number.", parse_mode=ParseMode.HTML)
            last_bot_messages[chat_id] = sent.id
    elif input_type == "episode":
        if message.text.isdigit():
            settings['episode'] = int(message.text)
            await save_user_settings(settings)
            del waiting_for_input[user_id]
            sent = await client.send_message(chat_id, f"✅ Episode set to {settings['episode']}", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(chat_id, "❌ Please enter a number.", parse_mode=ParseMode.HTML)
            last_bot_messages[chat_id] = sent.id
    elif input_type == "total_episode":
        if message.text.isdigit():
            settings['total_episode'] = int(message.text)
            await save_user_settings(settings)
            del waiting_for_input[user_id]
            sent = await client.send_message(chat_id, f"✅ Total episodes set to {settings['total_episode']}", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
        else:
            sent = await client.send_message(chat_id, "❌ Please enter a number.", parse_mode=ParseMode.HTML)
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
            settings['target_chat_id'] = chat.id
            await save_user_settings(settings)
            await save_channel_info(user_id, chat.id, chat.username if hasattr(chat, 'username') and chat.username else None, chat.title if hasattr(chat, 'title') else str(chat.id), str(chat.type))
            del waiting_for_input[user_id]
            sent = await client.send_message(chat_id, f"✅ Channel updated: <code>{chat.id}</code>", parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
            last_bot_messages[chat_id] = sent.id
        except Exception as e:
            sent = await client.send_message(chat_id, f"❌ Error: {e}", parse_mode=ParseMode.HTML)
            last_bot_messages[chat_id] = sent.id

@app.on_message(filters.private & filters.video)
async def auto_forward(client, message: Message):
    user_id = message.from_user.id
    if user_id in waiting_for_input:
        return
    lock = get_user_lock(user_id)
    async with lock:
        settings = await get_user_settings(user_id)
        if not settings['target_chat_id']:
            await message.reply("❌ No target channel set!", parse_mode=ParseMode.HTML)
            return
        if not settings['selected_qualities']:
            await message.reply("❌ No qualities selected!", parse_mode=ParseMode.HTML)
            return
        file_id = message.video.file_id
        quality = settings['selected_qualities'][settings['video_count'] % len(settings['selected_qualities'])] if settings['selected_qualities'] else "N/A"
        caption = settings['base_caption'].replace("{season}", f"{settings['season']:02}").replace("{episode}", f"{settings['episode']:02}").replace("{total_episode}", f"{settings['total_episode']:02}").replace("{quality}", quality)
        try:
            await client.send_video(chat_id=settings['target_chat_id'], video=file_id, caption=caption, parse_mode=ParseMode.HTML)
            await log_upload(user_id, settings['season'], settings['episode'], settings['total_episode'], quality, file_id, caption, settings['target_chat_id'])
            reply_msg = await message.reply(f"✅ Video forwarded! Season {settings['season']} Episode {settings['episode']} • {quality}", parse_mode=ParseMode.HTML)
            await asyncio.sleep(5)
            try:
                await reply_msg.delete()
                await message.delete()
            except:
                pass
            settings['video_count'] += 1
            if settings['video_count'] >= len(settings['selected_qualities']):
                settings['episode'] += 1
                settings['total_episode'] += 1
                settings['video_count'] = 0
            await save_user_settings(settings)
        except Exception:
            logger.exception("Error forwarding video")
            await message.reply("❌ Error forwarding. Ensure bot is admin in target channel and channel ID is correct.", parse_mode=ParseMode.HTML)

# -------------------------
# Webhook handling: parse raw JSON to Message/CallbackQuery objects (await _parse) and run handlers
# -------------------------
async def telegram_webhook(request):
    try:
        update_dict = await request.json()
        update_id = update_dict.get('update_id', 'unknown')
        logger.info(f"📨 Webhook received update ID: {update_id}")
        # process in background so webhook responds quickly
        asyncio.create_task(process_update_manually(update_dict))
        return web.Response(status=200, text="OK")
    except Exception:
        logger.exception("Webhook handler error")
        return web.Response(status=200, text="OK")

async def process_update_manually(update_dict):
    """
    Handles Telegram webhook updates manually by converting JSON into Message or CallbackQuery objects.
    Fix: normalize dict fields before passing to _parse().
    """
    try:
        from pyrogram.handlers import MessageHandler, CallbackQueryHandler
        from pyrogram import types as py_types

        if 'message' in update_dict:
            msg_data = update_dict['message']
            logger.info(f"📝 Message data keys: {list(msg_data.keys())}")
            logger.info(f"📝 Message text: {msg_data.get('text', 'N/A')}")

            # --- FIX: normalize structure for _parse() ---
            if "from" in msg_data and "from_user" not in msg_data:
                msg_data["from_user"] = msg_data.pop("from")
            if "chat" in msg_data and isinstance(msg_data["chat"], dict):
                msg_data["chat"] = {
                    "id": msg_data["chat"].get("id"),
                    "type": msg_data["chat"].get("type", "private"),
                    "title": msg_data["chat"].get("title"),
                    "username": msg_data["chat"].get("username"),
                    "first_name": msg_data["chat"].get("first_name"),
                    "last_name": msg_data["chat"].get("last_name"),
                }

            try:
                message_obj = await py_types.Message._parse(app, msg_data, {}, None)
                logger.info(f"✅ Parsed Message from {getattr(message_obj.from_user, 'id', 'N/A')}")

                for group in sorted(app.dispatcher.groups.keys()):
                    for handler in app.dispatcher.groups[group]:
                        if isinstance(handler, MessageHandler):
                            try:
                                if handler.filters is None or await handler.filters(app, message_obj):
                                    await handler.callback(app, message_obj)
                                    return
                            except Exception:
                                logger.exception("Handler execution error")
            except Exception:
                logger.exception("Error parsing Message object from webhook JSON")

        elif 'callback_query' in update_dict:
            cb_data = update_dict['callback_query']
            logger.info(f"🖲 Callback data: {cb_data.get('data', 'N/A')}")
            try:
                if "from" in cb_data and "from_user" not in cb_data:
                    cb_data["from_user"] = cb_data.pop("from")
                callback_obj = await py_types.CallbackQuery._parse(app, cb_data, {})
                for group in sorted(app.dispatcher.groups.keys()):
                    for handler in app.dispatcher.groups[group]:
                        if isinstance(handler, CallbackQueryHandler):
                            try:
                                if handler.filters is None or await handler.filters(app, callback_obj):
                                    await handler.callback(app, callback_obj)
                                    return
                            except Exception:
                                logger.exception("Callback handler error")
            except Exception:
                logger.exception("Error parsing CallbackQuery from webhook JSON")

        else:
            logger.info("⚙️ Unknown update type")
    except Exception:
        logger.exception("process_update_manually() failed")


# -------------------------
# Health & stats endpoints
# -------------------------
async def health_check(request):
    total_users = await get_all_users_count()
    return web.Response(text=f"Bot running! Users: {total_users}", content_type='text/plain')

async def stats_endpoint(request):
    total_users = await get_all_users_count()
    return web.json_response({'status':'running','total_users': total_users, 'timestamp': datetime.utcnow().isoformat(), 'webhook': WEBHOOK_URL if WEBHOOK_URL else 'polling'})

# -------------------------
# Webhook setup & self-ping
# -------------------------
async def setup_webhook():
    if not WEBHOOK_URL:
        logger.warning("⚠️ WEBHOOK_URL not set, using polling mode")
        return False
    try:
        async with httpx.AsyncClient() as client:
            await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook", json={"drop_pending_updates": True})
            resp = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", json={"url": WEBHOOK_URL, "drop_pending_updates": True, "allowed_updates": ["message","callback_query"]})
            data = resp.json()
            if data.get('ok'):
                logger.info(f"✅ Webhook set successfully: {WEBHOOK_URL}")
                info = (await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo")).json()
                if info.get('ok'):
                    logger.info(f"📡 Webhook info: {info.get('result')}")
                return True
            logger.error(f"❌ Failed to set webhook: {data}")
            return False
    except Exception:
        logger.exception("Webhook setup error")
        return False

async def self_ping():
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(600)
        if RENDER_EXTERNAL_URL:
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(f"{RENDER_EXTERNAL_URL}/health") as r:
                        logger.info(f"✅ Self-ping status: {r.status}")
            except Exception:
                logger.exception("Self-ping failed")

# -------------------------
# Start web server
# -------------------------
async def start_web_server():
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

# -------------------------
# Main
# -------------------------
async def main():
    logger.info("🌐 Starting web server...")
    await start_web_server()
    logger.info("🗄️ Initializing database...")
    await init_db()
    logger.info("🚀 Starting bot...")
    try:
        await app.start()
        me = await app.get_me()
        logger.info(f"✅ Bot started: @{me.username} (ID: {me.id})")
        if WEBHOOK_URL:
            ok = await setup_webhook()
            if ok:
                logger.info("🔗 Running in WEBHOOK mode")
            else:
                logger.warning("⚠️ Webhook setup failed, falling back to POLLING mode")
        else:
            logger.info("📡 Running in POLLING mode")
        logger.info("✅ ALL SYSTEMS OPERATIONAL")
        asyncio.create_task(self_ping())
        while True:
            await asyncio.sleep(3600)
    except Exception:
        logger.exception("Fatal error in main")
        raise
    finally:
        logger.info("🛑 Shutting down...")
        try:
            if WEBHOOK_URL:
                async with httpx.AsyncClient() as client:
                    await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook", json={"drop_pending_updates": False})
                logger.info("🗑️ Webhook deleted")
            await app.stop()
        except Exception:
            logger.exception("Error during shutdown")
        if db_pool:
            try:
                await db_pool.close()
            except:
                pass

if __name__ == "__main__":
    asyncio.run(main())
# ---------- end of file ----------

