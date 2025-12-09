#!/usr/bin/env python3
"""
bot.py — Pyrogram webhook bot (single-file, Render-ready)

Features:
- Webhook mode using aiohttp (route: /webhook)
- Optional PostgreSQL via asyncpg (if installed) or psycopg (psycopg3 + psycopg_pool)
- JSON file fallback storage (data.json)
- Per-user settings, caption templates, episode/quality cycling
- Upload flow (copy_message used as placeholder; replace with encoding logic if desired)
- Admin /status and basic admin_stats command (when ADMIN_IDS set)
- Health endpoint and optional self-ping
"""

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
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

# ---- Logging ----
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---- Config from env (set these in Render) ----
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # e.g. https://<your-app>.onrender.com/webhook
DATA_FILE = Path(os.getenv("DATA_FILE", "data.json"))
DATABASE_URL = os.getenv("DATABASE_URL")
SELF_PING_URL = os.getenv("SELF_PING_URL")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

if not BOT_TOKEN or not API_HASH or API_ID == 0:
    logger.warning("BOT_TOKEN, API_ID or API_HASH missing - set environment variables")

# ---- App objects ----
app = web.Application()
pyro = Client("uploader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---- DB globals ----
_pg_pool = None
_psycopg_pool = None
use_asyncpg = False
use_psycopg = False

# ---- In-memory structures and fallback ----
user_locks = {}
fallback = {"users": {}, "uploads": [], "global": {"total_uploads": 0}}
last_bot_messages = {}

# ---- Defaults ----
ALL_QUALITIES = ["480p", "720p", "1080p", "4K", "2160p"]
DEFAULT_CAPTION = " 𝗦𝗘𝗔𝗦𝗢𝗡 {season} || Episode {episode} ({quality})\\n{total_episode_text}"

# ---- Helper: load/save fallback ----
def load_fallback():
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            # only update keys we expect
            fallback["users"].update(data.get("users", {}))
            fallback["uploads"].extend(data.get("uploads", []))
            fallback["global"].update(data.get("global", {}))
            logger.info("Loaded JSON fallback storage")
        except Exception:
            logger.exception("Failed to read fallback file; starting with clean fallback")


def save_fallback_sync():
    try:
        DATA_FILE.write_text(json.dumps(fallback, default=str, indent=2), encoding="utf-8")
    except Exception:
        logger.exception("Failed to save fallback JSON")


async def save_fallback():
    # run sync writer in thread to be safe
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, save_fallback_sync)


# ---- DB init ----
async def init_db():
    global _pg_pool, _psycopg_pool, use_asyncpg, use_psycopg

    # Prefer asyncpg if installed and DATABASE_URL provided
    if DATABASE_URL and asyncpg is not None:
        try:
            _pg_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
            use_asyncpg = True
            logger.info("Connected to PostgreSQL via asyncpg")
            async with _pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        settings JSONB
                    )
                """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS uploads (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        ts TIMESTAMP WITH TIME ZONE,
                        data JSONB
                    )
                """
                )
            return
        except Exception:
            logger.exception("asyncpg init failed, will try other fallbacks")

    # Next prefer psycopg (if installed)
    if DATABASE_URL and psycopg is not None and AsyncConnectionPool is not None:
        try:
            _psycopg_pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=10)
            await _psycopg_pool.open()
            use_psycopg = True
            logger.info("Connected to PostgreSQL via psycopg")
            async with _psycopg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS users (
                            user_id BIGINT PRIMARY KEY,
                            settings JSONB
                        )
                    """
                    )
                    await cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS uploads (
                            id SERIAL PRIMARY KEY,
                            user_id BIGINT,
                            ts TIMESTAMP WITH TIME ZONE,
                            data JSONB
                        )
                    """
                    )
                    await conn.commit()
            return
        except Exception:
            logger.exception("psycopg init failed, will fallback to JSON")

    # fallback: load JSON
    load_fallback()
    logger.info("Using JSON fallback storage")


# ---- User settings helpers ----
async def get_user_settings(user_id: int) -> dict:
    # asyncpg path
    if use_asyncpg and _pg_pool:
        async with _pg_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT settings FROM users WHERE user_id=$1", user_id)
            if row and row["settings"]:
                return dict(row["settings"])
            d = default_user_settings(user_id)
            await conn.execute(
                "INSERT INTO users (user_id, settings) VALUES ($1, $2) ON CONFLICT DO NOTHING", user_id, d
            )
            return d

    # psycopg path
    if use_psycopg and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT settings FROM users WHERE user_id = %s", (user_id,))
                row = await cur.fetchone()
                if row and row[0]:
                    return row[0]
                d = default_user_settings(user_id)
                await cur.execute(
                    "INSERT INTO users (user_id, settings) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (user_id, json.dumps(d)),
                )
                await conn.commit()
                return d

    # JSON fallback
    key = str(user_id)
    if key in fallback["users"]:
        return fallback["users"][key]
    d = default_user_settings(user_id)
    fallback["users"][key] = d
    await save_fallback()
    return d


async def set_user_settings(user_id: int, settings: dict):
    # asyncpg
    if use_asyncpg and _pg_pool:
        async with _pg_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO users (user_id, settings) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET settings = $2",
                user_id,
                settings,
            )
        return

    # psycopg
    if use_psycopg and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO users (user_id, settings) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET settings = %s",
                    (user_id, json.dumps(settings), json.dumps(settings)),
                )
                await conn.commit()
        return

    # fallback
    fallback["users"][str(user_id)] = settings
    await save_fallback()


async def log_upload_event(user_id: int, data: dict):
    # asyncpg
    if use_asyncpg and _pg_pool:
        async with _pg_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO uploads (user_id, ts, data) VALUES ($1, $2, $3)",
                user_id,
                datetime.now(timezone.utc),
                data,
            )
            try:
                # safely increment in JSONB (single-quoted inner strings)
                await conn.execute(
                    "UPDATE users SET settings = settings || jsonb_build_object('global', jsonb_build_object('total_uploads', (COALESCE((settings->'global'->>'total_uploads')::int,0)+1))) WHERE user_id = $1",
                    user_id,
                )
            except Exception:
                logger.exception("Failed to bump total_uploads (asyncpg)")
        return

    # psycopg
    if use_psycopg and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO uploads (user_id, ts, data) VALUES (%s, %s, %s)",
                    (user_id, datetime.now(timezone.utc), json.dumps(data)),
                )
                try:
                    await cur.execute(
                        "UPDATE users SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{global,total_uploads}', to_jsonb((COALESCE((settings->'global'->>'total_uploads')::int,0)+1))) WHERE user_id = %s",
                        (user_id,),
                    )
                    await conn.commit()
                except Exception:
                    logger.exception("Failed to bump total_uploads (psycopg)")
        return

    # fallback
    fallback["uploads"].append({"user_id": user_id, "ts": datetime.now(timezone.utc).isoformat(), "data": data})
    fallback["global"]["total_uploads"] = fallback["global"].get("total_uploads", 0) + 1
    await save_fallback()


# ---- Utilities ----
def default_user_settings(user_id=None):
    return {
        "user_id": user_id,
        "season": 1,
        "episode": 1,
        "total_episode": 0,
        "video_count": 0,
        "selected_qualities": ["480p", "720p", "1080p"],
        "base_caption": DEFAULT_CAPTION,
        "target_chat_id": None,
    }


def get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


def render_caption(template: str, settings: dict, quality: str) -> str:
    total_episode_text = f"Total Episodes: {settings.get('total_episode')}" if settings.get("total_episode") else ""
    return template.format(
        season=f"{settings.get('season',1):02}",
        episode=f"{settings.get('episode',1):02}",
        total_episode_text=total_episode_text,
        quality=quality,
    )


def get_menu_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Preview", callback_data="preview")]])


# ---- Handlers ----
@pyro.on_message(filters.private & filters.command("start"))
async def cmd_start(client: Client, message: Message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "User"
    settings = await get_user_settings(user_id)

    # attempt to delete user message for cleanliness (best-effort)
    try:
        await message.delete()
    except Exception:
        pass

    welcome_text = f"👋 <b>Welcome {first_name}!</b>\nSend a video to start uploading."
    sent = await client.send_message(message.chat.id, welcome_text, parse_mode="html", reply_markup=get_menu_markup())
    last_bot_messages[message.chat.id] = sent.message_id if hasattr(sent, "message_id") else getattr(sent, "id", None)


@pyro.on_message(filters.private & filters.command("status"))
async def cmd_status(client: Client, message: Message):
    user_id = message.from_user.id
    settings = await get_user_settings(user_id)
    text = (
        f"Season: {settings['season']}\n"
        f"Episode: {settings['episode']}/{settings['total_episode'] or '??'}\n"
        f"Qualities: {', '.join(settings['selected_qualities'])}\n"
        f"Channel: {settings['target_chat_id']}"
    )
    await message.reply_text(text)


@pyro.on_message(filters.private & (filters.video | filters.document))
async def video_handler(client: Client, message: Message):
    user_id = message.from_user.id
    async with get_user_lock(user_id):
        settings = await get_user_settings(user_id)
        target = settings.get("target_chat_id")
        if not target:
            await message.reply_text("Set a target channel first (forward a message from your channel to /setup or send the channel username/id).")
            return
        quals = settings.get("selected_qualities", [])
        if not quals:
            await message.reply_text("No qualities selected")
            return

        # pick quality based on video_count
        idx = settings.get("video_count", 0) % len(quals)
        q = quals[idx]
        caption = render_caption(settings.get("base_caption", DEFAULT_CAPTION), settings, q)

        try:
            # For now we copy the message (preserves file). Replace with encoding/upload logic if needed.
            await client.copy_message(chat_id=target, from_chat_id=message.chat.id, message_id=message.message_id, caption=caption)
            await log_upload_event(user_id, {"quality": q, "season": settings["season"], "episode": settings["episode"]})

            settings["video_count"] = settings.get("video_count", 0) + 1
            if settings["video_count"] >= len(quals):
                settings["episode"] = settings.get("episode", 1) + 1
                settings["video_count"] = 0

            await set_user_settings(user_id, settings)
            await message.reply_text(f"Uploaded {q}. Progress: {settings['video_count']}/{len(quals)}")
        except Exception as e:
            logger.exception("Forward failed: %s", e)
            await message.reply_text(f"Failed to forward: {e}")


# Admin stats (basic)
@pyro.on_message(filters.private & filters.command("admin_stats"))
async def cmd_admin_stats(client: Client, message: Message):
    # only allow if ADMIN_IDS set; otherwise return a helpful message
    if not ADMIN_IDS:
        await message.reply_text("Admin IDs not configured on the server.")
        return
    if message.from_user.id not in ADMIN_IDS:
        await message.reply_text("You are not an admin.")
        return

    if use_asyncpg and _pg_pool:
        async with _pg_pool.acquire() as conn:
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
            total_uploads = await conn.fetchval("SELECT COUNT(*) FROM uploads")
    elif use_psycopg and _psycopg_pool:
        async with _psycopg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM users")
                total_users = (await cur.fetchone())[0]
                await cur.execute("SELECT COUNT(*) FROM uploads")
                total_uploads = (await cur.fetchone())[0]
    else:
        total_users = len(fallback["users"])
        total_uploads = len(fallback["uploads"])

    await message.reply_text(f"Total users: {total_users}\nTotal uploads: {total_uploads}")


# ---- Webhook endpoint for Telegram ----
async def handle_telegram_webhook(request):
    # Telegram sends application/json updates as POST
    try:
        raw = await request.read()
        if not raw:
            return web.Response(status=400, text="No body")
        # Let Pyrogram parse/process raw update bytes
        try:
            await pyro.process_raw_update(raw)
        except AttributeError:
            # Fallback: older pyrogram versions might accept json
            try:
                body = await request.json()
                await pyro.process_update(body)
            except Exception:
                logger.exception("Failed to process update via fallback")
        return web.Response(status=200, text="OK")
    except Exception:
        logger.exception("Error in webhook handler")
        return web.Response(status=500, text="Error")


# ---- Health and self-ping ----
async def health(request):
    return web.Response(text="ok", status=200)


async def self_ping_loop():
    if not SELF_PING_URL:
        return
    async with ClientSession() as sess:
        while True:
            try:
                await sess.get(SELF_PING_URL)
            except Exception:
                logger.exception("Self-ping failed")
            await asyncio.sleep(300)


# ---- Startup / Shutdown ----
async def on_startup(a):
    # initialize DB or fallback
    await init_db()
    # start pyrogram client
    try:
        await pyro.start()
    except Exception:
        logger.exception("Failed to start pyrogram")

    # set webhook if provided
    if WEBHOOK_URL:
        try:
            # Ensure Telegram will POST to the WEBHOOK_URL
            await pyro.set_webhook(WEBHOOK_URL)
            logger.info("Set Telegram webhook to %s", WEBHOOK_URL)
        except Exception:
            logger.exception("Failed to set webhook")

    # start self-ping task
    a["self_ping"] = asyncio.create_task(self_ping_loop())


async def on_shutdown(a):
    if a.get("self_ping"):
        a["self_ping"].cancel()
    try:
        await pyro.stop()
    except Exception:
        logger.exception("Error stopping pyrogram")
    # close DB pools if used
    if use_psycopg and _psycopg_pool:
        try:
            await _psycopg_pool.close()
        except Exception:
            pass
    if use_asyncpg and _pg_pool:
        try:
            await _pg_pool.close()
        except Exception:
            pass


# Register HTTP routes
app.add_routes(
    [
        web.get("/health", health),
        web.post("/webhook", handle_telegram_webhook),
    ]
)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)


# ---- Run server ----
if __name__ == "__main__":
    logger.info("Starting web app on %s:%s", WEBHOOK_HOST, WEBHOOK_PORT)
    web.run_app(app, host=WEBHOOK_HOST, port=WEBHOOK_PORT)
