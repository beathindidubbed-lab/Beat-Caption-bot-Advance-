#!/usr/bin/env python3
"""
bot.py — Pyrogram bot with long polling (Render-ready)
"""

# ==================== PART 1: IMPORTS AND CONFIGURATION ====================

import os
import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

# Optional DB drivers
try:
    import asyncpg
except Exception:
    asyncpg = None

try:
    import psycopg
    from psycopg_pool import AsyncConnectionPool
except Exception:
    psycopg = None
    AsyncConnectionPool = None

from aiohttp import web, ClientSession
from pyrogram import Client, filters, idle  # ⚠️ IMPORTANT: idle imported here
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode

# ---- Logging ----
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

# ---- Config ----
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST', '0.0.0.0')
WEBHOOK_PORT = int(os.getenv('WEBHOOK_PORT', '8080'))
DATA_FILE = Path(os.getenv('DATA_FILE', 'data.json'))
DATABASE_URL = os.getenv('DATABASE_URL')
SELF_PING_URL = os.getenv('SELF_PING_URL')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip()]

if not BOT_TOKEN or not API_HASH or API_ID == 0:
    logger.warning('BOT_TOKEN, API_ID or API_HASH missing. Set env vars.')

# ---- App objects ----
web_app = web.Application()
bot = Client('uploader_bot', api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---- DB globals ----
_pg_pool = None
_psycopg_pool = None
USE_ASYNCPG = False
USE_PSYCOG = False

# ---- In-memory/fallback storage ----
user_locks = {}
fallback = {'users': {}, 'uploads': [], 'global': {'total_uploads': 0}}
last_bot_msgs = {}
waiting_for_input = {}

# ---- Defaults ----
ALL_QUALITIES = ['480p', '720p', '1080p', '4K', '2160p']
DEFAULT_CAPTION = """• 𝗦𝗘𝗔𝗦𝗢𝗡 {season} || Episode {episode} ({quality})\n{total_episode_text}"""

# ==================== END OF PART 1 ====================

# ==================== PART 2: DATABASE AND STORAGE FUNCTIONS ====================

# ---- Fallback file helpers ----
def load_fallback():
    if DATA_FILE.exists():
        try:
            d = json.loads(DATA_FILE.read_text(encoding='utf-8'))
            fallback['users'].update(d.get('users', {}))
            fallback['uploads'].extend(d.get('uploads', []))
            fallback['global'].update(d.get('global', {}))
            logger.info('Loaded JSON fallback storage')
        except Exception:
            logger.exception('Failed to load fallback file')

def save_fallback_sync():
    try:
        DATA_FILE.write_text(json.dumps(fallback, default=str, indent=2), encoding='utf-8')
    except Exception:
        logger.exception('Failed to save fallback file')

async def save_fallback():
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, save_fallback_sync)

# ---- DB init ----
async def init_db():
    global _pg_pool, _psycopg_pool, USE_ASYNCPG, USE_PSYCOG

    if DATABASE_URL and asyncpg is not None:
        try:
            _pg_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
            USE_ASYNCPG = True
            logger.info('Connected to Postgres via asyncpg')
            async with _pg_pool.acquire() as conn:
                await conn.execute('CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, settings JSONB)')
                await conn.execute('CREATE TABLE IF NOT EXISTS uploads (id SERIAL PRIMARY KEY, user_id BIGINT, ts TIMESTAMP WITH TIME ZONE, data JSONB)')
            return
        except Exception:
            logger.exception('asyncpg init failed, falling back')

    if DATABASE_URL and psycopg is not None and AsyncConnectionPool is not None:
        try:
            _psycopg_pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=10)
            USE_PSYCOG = True
            logger.info('Connected to Postgres via psycopg')
            async with _psycopg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, settings JSONB)")
                    await cur.execute("CREATE TABLE IF NOT EXISTS uploads (id SERIAL PRIMARY KEY, user_id BIGINT, ts TIMESTAMP WITH TIME ZONE, data JSONB)")
                    await conn.commit()
            return
        except Exception:
            logger.exception('psycopg init failed, falling back')

    load_fallback()
    logger.info('Using JSON fallback storage')

# ---- User settings helpers ----
async def default_user_settings(user_id=None):
    return {
        'user_id': user_id,
        'season': 1,
        'episode': 1,
        'total_episode': 0,
        'video_count': 0,
        'selected_qualities': ['480p', '720p', '1080p'],
        'base_caption': DEFAULT_CAPTION,
        'target_chat_id': None
    }

async def get_user_settings(user_id: int) -> dict:
    if USE_ASYNCPG and _pg_pool:
        async with _pg_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT settings FROM users WHERE user_id=$1', user_id)
            if row and row['settings']:
                return dict(row['settings'])
            d = await default_user_settings(user_id)
            await conn.execute('INSERT INTO users (user_id, settings) VALUES ($1, $2) ON CONFLICT DO NOTHING', user_id, d)
            return d

    if USE_PSYCOG and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute('SELECT settings FROM users WHERE user_id = %s', (user_id,))
                row = await cur.fetchone()
                if row and row[0]:
                    return row[0]
                d = await default_user_settings(user_id)
                await cur.execute('INSERT INTO users (user_id, settings) VALUES (%s, %s) ON CONFLICT DO NOTHING', (user_id, json.dumps(d)))
                await conn.commit()
                return d

    key = str(user_id)
    if key in fallback['users']:
        return fallback['users'][key]
    d = await default_user_settings(user_id)
    fallback['users'][key] = d
    await save_fallback()
    return d

async def set_user_settings(user_id: int, settings: dict):
    if USE_ASYNCPG and _pg_pool:
        async with _pg_pool.acquire() as conn:
            await conn.execute('INSERT INTO users (user_id, settings) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET settings = $2', user_id, settings)
        return
    if USE_PSYCOG and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute('INSERT INTO users (user_id, settings) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET settings = %s', (user_id, json.dumps(settings), json.dumps(settings)))
                await conn.commit()
        return
    fallback['users'][str(user_id)] = settings
    await save_fallback()

async def log_upload_event(user_id: int, data: dict):
    if USE_ASYNCPG and _pg_pool:
        async with _pg_pool.acquire() as conn:
            await conn.execute('INSERT INTO uploads (user_id, ts, data) VALUES ($1, $2, $3)', user_id, datetime.now(timezone.utc), data)
            try:
                await conn.execute("UPDATE users SET settings = settings || jsonb_build_object('global', jsonb_build_object('total_uploads', (COALESCE((settings->'global'->>'total_uploads')::int,0)+1))) WHERE user_id = $1", user_id)
            except Exception:
                logger.exception('Failed to bump total_uploads (asyncpg)')
        return

    if USE_PSYCOG and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute('INSERT INTO uploads (user_id, ts, data) VALUES (%s, %s, %s)', (user_id, datetime.now(timezone.utc), json.dumps(data)))
                try:
                    await cur.execute("UPDATE users SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{global,total_uploads}', to_jsonb((COALESCE((settings->'global'->>'total_uploads')::int,0)+1))) WHERE user_id = %s", (user_id,))
                    await conn.commit()
                except Exception:
                    logger.exception('Failed to bump total_uploads (psycopg)')
        return

    fallback['uploads'].append({'user_id': user_id, 'ts': datetime.now(timezone.utc).isoformat(), 'data': data})
    fallback['global']['total_uploads'] = fallback['global'].get('total_uploads', 0) + 1
    await save_fallback()

# ==================== END OF PART 2 ====================

# ==================== PART 3: UI UTILITIES AND MARKUP FUNCTIONS ====================

def get_lock(user_id: int) -> asyncio.Lock:
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]

def render_caption(template: str, settings: dict, quality: str) -> str:
    total_episode_text = f'Total Episodes: {settings.get("total_episode")}' if settings.get('total_episode') else ''
    try:
        return template.format(season=f"{settings.get('season',1):02}", episode=f"{settings.get('episode',1):02}", total_episode_text=total_episode_text, quality=quality)
    except Exception:
        return DEFAULT_CAPTION.format(season=settings.get('season', 1), episode=settings.get('episode', 1), quality=quality, total_episode_text=total_episode_text)

def menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🔍 Preview Caption', callback_data='preview')],
        [InlineKeyboardButton('✏️ Set Caption', callback_data='set_caption')],
        [InlineKeyboardButton('📺 Set Season', callback_data='set_season'), InlineKeyboardButton('🎬 Set Episode', callback_data='set_episode')],
        [InlineKeyboardButton('🔢 Set Total Episode', callback_data='set_total_episode')],
        [InlineKeyboardButton('🎥 Quality Settings', callback_data='quality_menu')],
        [InlineKeyboardButton('🎯 Set Target Channel', callback_data='set_channel')],
        [InlineKeyboardButton('📊 My Statistics', callback_data='stats')],
        [InlineKeyboardButton('🔄 Reset Episode', callback_data='reset')],
        [InlineKeyboardButton('❌ Cancel', callback_data='cancel')]
    ])

def admin_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 Set Welcome Message', callback_data='admin_set_welcome'), InlineKeyboardButton('👁️ Preview Welcome', callback_data='admin_preview_welcome')],
        [InlineKeyboardButton('📊 Global Stats', callback_data='admin_global_stats')],
        [InlineKeyboardButton('⬅️ Back to Main', callback_data='back_to_main')]
    ])

def quality_markup(selected):
    buttons = [[InlineKeyboardButton(('✅ ' if q in selected else '') + q, callback_data=f'toggle_quality_{q}')] for q in ALL_QUALITIES]
    buttons.append([InlineKeyboardButton('⬅️ Back', callback_data='back_to_main')])
    return InlineKeyboardMarkup(buttons)

def channel_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton('📤 Forward Message', callback_data='forward_channel'), InlineKeyboardButton('🔗 Send Username/ID', callback_data='send_channel_id')], [InlineKeyboardButton('⬅️ Back', callback_data='back_to_main')]])

# ==================== END OF PART 3 ====================

# ==================== PART 4: MESSAGE HANDLERS (COMMANDS) ====================

@bot.on_message(filters.private & filters.command('start'))
async def handle_start(c: Client, m: Message):
    user_id = m.from_user.id
    first_name = m.from_user.first_name or 'User'
    settings = await get_user_settings(user_id)
    try:
        await m.delete()
    except Exception:
        pass

    welcome = await _get_welcome()
    if welcome and welcome.get('file_id'):
        caption = (welcome.get('caption') or '').format(first_name=first_name, user_id=user_id)
        try:
            if welcome.get('message_type') == 'photo':
                sent = await c.send_photo(m.chat.id, welcome['file_id'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=menu_markup())
            elif welcome.get('message_type') == 'video':
                sent = await c.send_video(m.chat.id, welcome['file_id'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=menu_markup())
            elif welcome.get('message_type') == 'animation':
                sent = await c.send_animation(m.chat.id, welcome['file_id'], caption=caption, parse_mode=ParseMode.HTML, reply_markup=menu_markup())
            else:
                sent = await c.send_message(m.chat.id, caption or f'Welcome {first_name}!', parse_mode=ParseMode.HTML, reply_markup=menu_markup())
            last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
            return
        except Exception:
            logger.exception('Failed sending welcome media')

    text = (f"""👋 <b>Welcome {first_name}!</b>\n\n"""
            """🤖 <b>Your Upload Assistant</b>\n\n"""
            """• Auto-caption and forward videos\n"""
            """• Multi-quality support\n"""
            """• Episode tracking (per user)\n"""
            """• Channel setup and preview\n\n"""
            """Start by setting your target channel and caption.""")
    sent = await c.send_message(m.chat.id, text, parse_mode=ParseMode.HTML, reply_markup=menu_markup())
    last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))

@bot.on_message(filters.private & filters.command('help'))
async def handle_help(c: Client, m: Message):
    try:
        await m.delete()
    except:
        pass
    await _delete_last(c, m.chat.id)
    text = ("/start - Open menu\n/help - This help\n/stats - Your stats\n/admin - Admin panel (admins only)")
    sent = await c.send_message(m.chat.id, text, parse_mode=ParseMode.HTML, reply_markup=menu_markup())
    last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))

@bot.on_message(filters.private & filters.command('stats'))
async def handle_stats(c: Client, m: Message):
    user_id = m.from_user.id
    settings = await get_user_settings(user_id)
    total, today = await _get_user_upload_stats(user_id)
    try:
        await m.delete()
    except:
        pass
    await _delete_last(c, m.chat.id)
    text = (f"📊 <b>Your Statistics</b>\n\n"
            f"👤 User ID: <code>{user_id}</code>\n"
            f"📤 Total: <code>{total}</code> | Today: <code>{today}</code>\n\n"
            f"📺 Season: <code>{settings['season']}</code>\n"
            f"🎬 Episode: <code>{settings['episode']}</code>\n"
            f"🔢 Total Episodes: <code>{settings['total_episode']}</code>\n"
            f"🎥 Progress: <code>{settings['video_count']}/{len(settings['selected_qualities'])}</code>\n"
            f"🎯 Channel: <code>{settings['target_chat_id']}</code>")
    sent = await c.send_message(m.chat.id, text, parse_mode=ParseMode.HTML, reply_markup=menu_markup())
    last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))

@bot.on_message(filters.private & filters.command('admin'))
async def handle_admin(c: Client, m: Message):
    if not ADMIN_IDS or m.from_user.id not in ADMIN_IDS:
        await m.reply('❌ You are not an admin')
        return
    try:
        await m.delete()
    except:
        pass
    await _delete_last(c, m.chat.id)
    sent = await c.send_message(m.chat.id, '👑 Admin Panel', parse_mode=ParseMode.HTML, reply_markup=admin_markup())
    last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))

# ==================== END OF PART 4 ====================

# ==================== PART 5: MESSAGE HANDLERS (TEXT, FORWARD, MEDIA) ====================

@bot.on_message(filters.private & (filters.text | filters.sticker) & ~filters.command(['start', 'help', 'stats', 'admin']))
async def handle_text_input(c: Client, m: Message):
    user_id = m.from_user.id
    if user_id not in waiting_for_input:
        return
    mode = waiting_for_input[user_id]
    try:
        await m.delete()
    except:
        pass
    await _delete_last(c, m.chat.id)
    settings = await get_user_settings(user_id)

    async with get_lock(user_id):
        if mode == 'caption':
            if not m.text:
                await c.send_message(m.chat.id, 'Send a valid caption text')
                return
            settings['base_caption'] = m.text
            await set_user_settings(user_id, settings)
            del waiting_for_input[user_id]
            sent = await c.send_message(m.chat.id, '✅ Caption updated', reply_markup=menu_markup())
            last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
            return
        if mode == 'season':
            if not m.text or not m.text.isdigit():
                await c.send_message(m.chat.id, 'Send a valid number')
                return
            settings['season'] = int(m.text)
            await set_user_settings(user_id, settings)
            del waiting_for_input[user_id]
            sent = await c.send_message(m.chat.id, f'✅ Season set to {settings["season"]}', reply_markup=menu_markup())
            last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
            return
        if mode == 'episode':
            if not m.text or not m.text.isdigit():
                await c.send_message(m.chat.id, 'Send a valid number')
                return
            settings['episode'] = int(m.text)
            settings['video_count'] = 0
            await set_user_settings(user_id, settings)
            del waiting_for_input[user_id]
            sent = await c.send_message(m.chat.id, f'✅ Episode set to {settings["episode"]} and progress reset', reply_markup=menu_markup())
            last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
            return
        if mode == 'total_episode':
            if not m.text or not m.text.isdigit():
                await c.send_message(m.chat.id, 'Send a valid number')
                return
            settings['total_episode'] = int(m.text)
            await set_user_settings(user_id, settings)
            del waiting_for_input[user_id]
            sent = await c.send_message(m.chat.id, f'✅ Total episodes set to {settings["total_episode"]}', reply_markup=menu_markup())
            last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
            return
        if mode == 'channel_id':
            text = m.text.strip()
            try:
                if text.startswith('@'):
                    chat = await c.get_chat(text)
                else:
                    chat = await c.get_chat(int(text))
                settings['target_chat_id'] = chat.id
                await set_user_settings(user_id, settings)
                await _save_channel_info(user_id, chat)
                del waiting_for_input[user_id]
                sent = await c.send_message(m.chat.id, f'✅ Channel set to {chat.title} ({chat.id})', reply_markup=menu_markup())
                last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
            except Exception as e:
                sent = await c.send_message(m.chat.id, f'❌ Failed to set channel: {e}')
                last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
            return
        if mode == 'admin_welcome_caption':
            data = waiting_for_input.get(f'{user_id}_welcome_data')
            if not data:
                del waiting_for_input[user_id]
                await c.send_message(m.chat.id, '⚠️ Session lost. Start over from /admin')
                return
            caption = m.text or ''
            ok = await _save_welcome(data['message_type'], data['file_id'], caption)
            if ok:
                del waiting_for_input[user_id]
                del waiting_for_input[f'{user_id}_welcome_data']
                sent = await c.send_message(m.chat.id, '✅ Welcome saved', reply_markup=admin_markup())
                last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
            else:
                await c.send_message(m.chat.id, '❌ Failed to save welcome')
            return

@bot.on_message(filters.private & filters.forwarded)
async def handle_forward(c: Client, m: Message):
    user_id = m.from_user.id
    if waiting_for_input.get(user_id) != 'forward_channel':
        return
    try:
        await m.delete()
    except:
        pass
    await _delete_last(c, m.chat.id)
    if not m.forward_from_chat:
        sent = await c.send_message(m.chat.id, '❌ Please forward a message from a channel or group')
        last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    chat = m.forward_from_chat
    settings = await get_user_settings(user_id)
    settings['target_chat_id'] = chat.id
    await set_user_settings(user_id, settings)
    await _save_channel_info(user_id, chat)
    del waiting_for_input[user_id]
    sent = await c.send_message(m.chat.id, f'✅ Channel set: {chat.title} ({chat.id})', reply_markup=menu_markup())
    last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))

@bot.on_message(filters.private & (filters.photo | filters.video | filters.animation))
async def handle_media_admin(c: Client, m: Message):
    user_id = m.from_user.id
    if waiting_for_input.get(user_id) != 'admin_welcome':
        return
    if user_id not in ADMIN_IDS:
        return
    try:
        await m.delete()
    except:
        pass
    await _delete_last(c, m.chat.id)
    file_id = None
    msg_type = None
    if m.photo:
        file_id = m.photo.file_id
        msg_type = 'photo'
    elif m.video:
        file_id = m.video.file_id
        msg_type = 'video'
    elif m.animation:
        file_id = m.animation.file_id
        msg_type = 'animation'
    if not file_id:
        sent = await c.send_message(m.chat.id, '❌ Unsupported media')
        last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    waiting_for_input[f'{user_id}_welcome_data'] = {'message_type': msg_type, 'file_id': file_id}
    waiting_for_input[user_id] = 'admin_welcome_caption'
    sent = await c.send_message(m.chat.id, '✅ Media received. Now send caption (HTML ok).')
    last_bot_msgs[m.chat.id] = getattr(sent, 'message_id', getattr(sent, 'id', None))

@bot.on_message(filters.private & filters.video & ~filters.forwarded)
async def handle_video_upload(c: Client, m: Message):
    user_id = m.from_user.id
    if user_id in waiting_for_input:
        return
    lock = get_lock(user_id)
    async with lock:
        try:
            settings = await get_user_settings(user_id)
            target = settings.get('target_chat_id')
            if not target:
                await m.reply('⚠️ No target set. Use menu to set channel.')
                return
            quals = settings.get('selected_qualities', [])
            if not quals:
                await m.reply('⚠️ No qualities selected. Configure in menu.')
                return
            idx = settings.get('video_count', 0) % len(quals)
            q = quals[idx]
            caption = render_caption(settings.get('base_caption', DEFAULT_CAPTION), settings, q)
            await c.copy_message(chat_id=target, from_chat_id=m.chat.id, message_id=m.message_id, caption=caption, parse_mode=ParseMode.HTML)
            await log_upload_event(user_id, {'quality': q, 'season': settings['season'], 'episode': settings['episode']})
            settings['video_count'] = settings.get('video_count', 0) + 1
            if settings['video_count'] >= len(quals):
                settings['episode'] = settings.get('episode', 1) + 1
                settings['video_count'] = 0
                await c.send_message(m.chat.id, f'✅ Episode {settings["episode"]-1} complete. Next Episode: {settings["episode"]}', parse_mode=ParseMode.HTML)
            else:
                await c.send_message(m.chat.id, f'✅ Uploaded {q}. Progress: {settings["video_count"]}/{len(quals)}', parse_mode=ParseMode.HTML)
            await set_user_settings(user_id, settings)
        except Exception as e:
            logger.exception('Upload error')
            await m.reply(f'❌ Upload failed: {e}')

# ==================== END OF PART 5 ====================

# ==================== PART 6: CALLBACK QUERY HANDLER ====================

@bot.on_callback_query()
async def handle_callback(c: Client, cq: CallbackQuery):
    data = cq.data
    user_id = cq.from_user.id
    chat_id = cq.message.chat.id
    settings = await get_user_settings(user_id)
    await cq.answer()
    await _delete_last(c, chat_id)

    # Admin callbacks
    if data == 'admin_set_welcome' and user_id in ADMIN_IDS:
        waiting_for_input[user_id] = 'admin_welcome'
        sent = await cq.message.reply('Send a photo/video/animation for welcome (admins only).')
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'admin_preview_welcome' and user_id in ADMIN_IDS:
        w = await _get_welcome()
        if not w:
            sent = await cq.message.reply('No welcome configured')
            last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
            return
        cap = (w.get('caption') or '').format(first_name='Test', user_id=0)
        try:
            if w.get('message_type') == 'photo':
                await c.send_photo(chat_id, w['file_id'], caption=f'👁️ Preview\n{cap}', parse_mode=ParseMode.HTML)
            elif w.get('message_type') == 'video':
                await c.send_video(chat_id, w['file_id'], caption=f'👁️ Preview\n{cap}', parse_mode=ParseMode.HTML)
            elif w.get('message_type') == 'animation':
                await c.send_animation(chat_id, w['file_id'], caption=f'👁️ Preview\n{cap}', parse_mode=ParseMode.HTML)
        except Exception as e:
            await c.send_message(chat_id, f'Preview failed: {e}')
        sent = await c.send_message(chat_id, 'Admin menu', reply_markup=admin_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'admin_global_stats' and user_id in ADMIN_IDS:
        total = await _get_all_users_count()
        sent = await cq.message.reply(f'Global users: {total} | Storage: {"Postgres" if (USE_ASYNCPG or USE_PSYCOG) else "JSON"}')
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return

    # User callbacks
    if data == 'preview':
        target = settings.get('target_chat_id')
        target_disp = f'<code>{target}</code>' if target else '❌ Not set'
        next_q = settings['selected_qualities'][settings['video_count'] % len(settings['selected_qualities'])] if settings['selected_qualities'] else 'N/A'
        preview = render_caption(settings.get('base_caption', DEFAULT_CAPTION), settings, next_q)
        sent = await cq.message.reply(f'🔍 Caption Preview:\n{preview}\n\nChannel: {target_disp}', parse_mode=ParseMode.HTML, reply_markup=menu_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'set_caption':
        waiting_for_input[user_id] = 'caption'
        sent = await cq.message.reply('Send new caption template (placeholders: {season},{episode},{total_episode},{quality})', reply_markup=menu_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'set_season':
        waiting_for_input[user_id] = 'season'
        sent = await cq.message.reply('Send season number', reply_markup=menu_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'set_episode':
        waiting_for_input[user_id] = 'episode'
        sent = await cq.message.reply('Send episode number (will reset progress)', reply_markup=menu_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'set_total_episode':
        waiting_for_input[user_id] = 'total_episode'
        sent = await cq.message.reply('Send total episodes count', reply_markup=menu_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'quality_menu':
        sent = await cq.message.reply('Toggle qualities', reply_markup=quality_markup(settings.get('selected_qualities', [])))
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data and data.startswith('toggle_quality_'):
        q = data.split('toggle_quality_')[-1]
        async with get_lock(user_id):
            sel = settings.get('selected_qualities', [])
            if q in sel:
                sel.remove(q)
            else:
                sel.append(q)
                sel.sort(key=lambda x: ALL_QUALITIES.index(x) if x in ALL_QUALITIES else 999)
            settings['selected_qualities'] = sel
            await set_user_settings(user_id, settings)
        try:
            await cq.message.edit_text('Toggle qualities', reply_markup=quality_markup(settings.get('selected_qualities', [])))
        except Exception:
            pass
        return
    
    if data == 'set_channel':
        sent = await cq.message.reply('Choose method', reply_markup=channel_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'forward_channel':
        waiting_for_input[user_id] = 'forward_channel'
        sent = await cq.message.reply('Forward a message from your target channel')
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'send_channel_id':
        waiting_for_input[user_id] = 'channel_id'
        sent = await cq.message.reply('Send the channel username (@name) or ID (-100...)')
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'stats':
        total, today = await _get_user_upload_stats(user_id)
        sent = await cq.message.reply(f'Your uploads: total {total} | today {today}', reply_markup=menu_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'reset':
        async with get_lock(user_id):
            settings['episode'] = 1
            settings['video_count'] = 0
            await set_user_settings(user_id, settings)
        sent = await cq.message.reply('Progress reset', reply_markup=menu_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return
    
    if data == 'back_to_main' or data == 'cancel':
        if user_id in waiting_for_input:
            del waiting_for_input[user_id]
        if f'{user_id}_welcome_data' in waiting_for_input:
            del waiting_for_input[f'{user_id}_welcome_data']
        try:
            await cq.message.delete()
        except:
            pass
        sent = await c.send_message(chat_id, 'Main menu', reply_markup=menu_markup())
        last_bot_msgs[chat_id] = getattr(sent, 'message_id', getattr(sent, 'id', None))
        return

# ==================== END OF PART 6 ====================

# ==================== PART 7: HELPER FUNCTIONS ====================

async def _save_channel_info(user_id, chat):
    if USE_ASYNCPG and _pg_pool:
        async with _pg_pool.acquire() as conn:
            try:
                await conn.execute('CREATE TABLE IF NOT EXISTS channel_info (user_id BIGINT, chat_id BIGINT, username TEXT, title TEXT, type TEXT, PRIMARY KEY(user_id, chat_id))')
                await conn.execute('INSERT INTO channel_info (user_id, chat_id, username, title, type) VALUES ($1,$2,$3,$4,$5) ON CONFLICT (user_id, chat_id) DO UPDATE SET username=EXCLUDED.username, title=EXCLUDED.title, type=EXCLUDED.type', user_id, chat.id, getattr(chat, 'username', None), getattr(chat, 'title', None), str(getattr(chat, 'type', '')))
            except Exception:
                pass
    elif USE_PSYCOG and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("CREATE TABLE IF NOT EXISTS channel_info (user_id BIGINT, chat_id BIGINT, username TEXT, title TEXT, type TEXT, PRIMARY KEY(user_id, chat_id))")
                    await cur.execute("INSERT INTO channel_info (user_id, chat_id, username, title, type) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (user_id, chat_id) DO UPDATE SET username=EXCLUDED.username, title=EXCLUDED.title, type=EXCLUDED.type", (user_id, chat.id, getattr(chat, 'username', None), getattr(chat, 'title', None), str(getattr(chat, 'type', ''))))
                    await conn.commit()
                except Exception:
                    pass
    else:
        k = str(user_id)
        u = fallback['users'].get(k, {})
        u['channel_info'] = {'chat_id': getattr(chat, 'id', None), 'username': getattr(chat, 'username', None), 'title': getattr(chat, 'title', None)}
        fallback['users'][k] = u
        await save_fallback()

async def _get_user_upload_stats(user_id):
    if USE_ASYNCPG and _pg_pool:
        async with _pg_pool.acquire() as conn:
            total = await conn.fetchval('SELECT COUNT(*) FROM uploads WHERE user_id=$1', user_id)
            today = await conn.fetchval("SELECT COUNT(*) FROM uploads WHERE user_id=$1 AND DATE(ts) = CURRENT_DATE", user_id)
            return int(total or 0), int(today or 0)
    if USE_PSYCOG and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute('SELECT COUNT(*) FROM uploads WHERE user_id=%s', (user_id,))
                total = (await cur.fetchone())[0]
                await cur.execute('SELECT COUNT(*) FROM uploads WHERE user_id=%s AND DATE(ts)=CURRENT_DATE', (user_id,))
                today = (await cur.fetchone())[0]
                return int(total or 0), int(today or 0)
    total = sum(1 for u in fallback['uploads'] if u.get('user_id') == user_id)
    today = sum(1 for u in fallback['uploads'] if u.get('user_id') == user_id and u.get('ts', '').startswith(datetime.now(timezone.utc).date().isoformat()))
    return total, today

async def _get_all_users_count():
    if USE_ASYNCPG and _pg_pool:
        async with _pg_pool.acquire() as conn:
            return int(await conn.fetchval('SELECT COUNT(*) FROM users') or 0)
    if USE_PSYCOG and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute('SELECT COUNT(*) FROM users')
                return int((await cur.fetchone())[0] or 0)
    return len(fallback['users'])

async def _save_welcome(message_type, file_id, caption):
    if USE_ASYNCPG and _pg_pool:
        async with _pg_pool.acquire() as conn:
            try:
                await conn.execute('CREATE TABLE IF NOT EXISTS welcome_settings (id SERIAL PRIMARY KEY, message_type TEXT, file_id TEXT, caption TEXT)')
                await conn.execute('DELETE FROM welcome_settings')
                await conn.execute('INSERT INTO welcome_settings (message_type, file_id, caption) VALUES ($1,$2,$3)', message_type, file_id, caption)
                return True
            except Exception:
                return False
    if USE_PSYCOG and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute('CREATE TABLE IF NOT EXISTS welcome_settings (id SERIAL PRIMARY KEY, message_type TEXT, file_id TEXT, caption TEXT)')
                    await cur.execute('DELETE FROM welcome_settings')
                    await cur.execute('INSERT INTO welcome_settings (message_type, file_id, caption) VALUES (%s,%s,%s)', (message_type, file_id, caption))
                    await conn.commit()
                    return True
                except Exception:
                    return False
    fallback['welcome'] = {'message_type': message_type, 'file_id': file_id, 'caption': caption}
    await save_fallback()
    return True

async def _get_welcome():
    if USE_ASYNCPG and _pg_pool:
        async with _pg_pool.acquire() as conn:
            try:
                row = await conn.fetchrow('SELECT * FROM welcome_settings ORDER BY id DESC LIMIT 1')
                return dict(row) if row else None
            except Exception:
                return None
    if USE_PSYCOG and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute('SELECT * FROM welcome_settings ORDER BY id DESC LIMIT 1')
                    r = await cur.fetchone()
                    if not r:
                        return None
                    cols = [d[0] for d in cur.description]
                    return dict(zip(cols, r))
                except Exception:
                    return None
    return fallback.get('welcome')

async def _delete_last(client, chat_id):
    try:
        if chat_id in last_bot_msgs:
            await client.delete_messages(chat_id, last_bot_msgs[chat_id])
            del last_bot_msgs[chat_id]
    except Exception:
        pass

# ==================== END OF PART 7 ====================

# ==================== PART 8: WEBHOOK & STARTUP/SHUTDOWN (FIXED) ====================

# Webhook & health endpoints
async def health(request):
    return web.Response(text='OK', status=200)

async def root(request):
    return web.Response(text='Bot Running', status=200)

async def self_ping_loop():
    if not SELF_PING_URL:
        return
    async with ClientSession() as sess:
        while True:
            try:
                await sess.get(SELF_PING_URL)
                logger.info('Self-ping successful')
            except Exception:
                logger.exception('Self-ping failed')
            await asyncio.sleep(300)

# Main function to run bot and web server together
async def main():
    """Run both web app and bot together with long polling"""
    # idle is already imported at the top in PART 1
    
    ping_task = None
    runner = None
    
    try:
        # Initialize database
        await init_db()
        
        # Start the bot with long polling
        await bot.start()
        logger.info('✅ Bot started successfully with long polling mode')
        logger.info(f'✅ Bot username: @{bot.me.username}')
        logger.info(f'✅ Bot ID: {bot.me.id}')
        
        # Start web server for health checks
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
        await site.start()
        logger.info(f'✅ Web server started on {WEBHOOK_HOST}:{WEBHOOK_PORT}')
        
        # Start self-ping task
        ping_task = asyncio.create_task(self_ping_loop())
        logger.info('✅ Self-ping task started')
        
        # Keep bot running (this will block until stopped)
        logger.info('🚀 Bot is now running and listening for updates...')
        logger.info('📱 Send /start to your bot to test!')
        await idle()
        
    except KeyboardInterrupt:
        logger.info('⚠️ Received stop signal')
    except Exception as e:
        logger.exception(f'❌ Error in main: {e}')
    finally:
        # Cleanup
        logger.info('🔄 Cleaning up...')
        
        if ping_task:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass
        
        try:
            await bot.stop()
            logger.info('✅ Bot stopped')
        except Exception as e:
            logger.error(f'Error stopping bot: {e}')
        
        if runner:
            try:
                await runner.cleanup()
                logger.info('✅ Web server stopped')
            except Exception as e:
                logger.error(f'Error stopping web server: {e}')
        
        if USE_PSYCOG and _psycopg_pool:
            try:
                await _psycopg_pool.close()
                logger.info('✅ PostgreSQL pool closed (psycopg)')
            except Exception as e:
                logger.error(f'Error closing psycopg pool: {e}')
        
        if USE_ASYNCPG and _pg_pool:
            try:
                await _pg_pool.close()
                logger.info('✅ PostgreSQL pool closed (asyncpg)')
            except Exception as e:
                logger.error(f'Error closing asyncpg pool: {e}')

# Setup web routes (only health checks, no webhook processing)
web_app.add_routes([
    web.get('/health', health),
    web.get('/', root)
])

if __name__ == '__main__':
    logger.info('='*60)
    logger.info('🤖 Starting Telegram Bot with Long Polling Mode')
    logger.info('='*60)
    asyncio.run(main())

# ==================== END OF PART 8 ====================
