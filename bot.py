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

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ==============================
# 🔧 CONFIGURATION
# ==============================
API_ID = int(os.getenv("API_ID", ""))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else ""
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "123456789").split(",") if x.strip()]

ALL_QUALITIES = ["480p", "720p", "1080p", "4K", "2160p"]
DEFAULT_CAPTION = (
    "<b>Anime</b> - <i>@Your_Channel</i>\n"
    "Season {season} - Episode {episode} ({total_episode}) - {quality}\n"
    "<blockquote>Don't miss this episode!</blockquote>"
)

db_pool = None

app = Client(
    "auto_caption_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=4,
    in_memory=True
)

waiting_for_input = {}
last_bot_messages = {}
user_locks = {}
web_app = web.Application()

def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


# ==============================
# 🗄️ DATABASE INITIALIZATION
# ==============================
async def init_db():
    global db_pool
    if not DATABASE_URL:
        logger.warning("⚠️ No DATABASE_URL, using JSON fallback")
        return

    try:
        db_pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=10, open=False)
        await db_pool.open()

        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_settings (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        first_name TEXT,
                        season INTEGER DEFAULT 1,
                        episode INTEGER DEFAULT 1,
                        total_episode INTEGER DEFAULT 1,
                        video_count INTEGER DEFAULT 0,
                        selected_qualities TEXT DEFAULT '480p,720p,1080p',
                        base_caption TEXT DEFAULT %s,
                        target_chat_id BIGINT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """, (DEFAULT_CAPTION,))
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS welcome_settings (
                        id SERIAL PRIMARY KEY,
                        message_type TEXT,
                        file_id TEXT,
                        caption TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS upload_history (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        season INTEGER,
                        episode INTEGER,
                        total_episode INTEGER,
                        quality TEXT,
                        file_id TEXT,
                        caption TEXT,
                        target_chat_id BIGINT,
                        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS channel_info (
                        user_id BIGINT,
                        chat_id BIGINT,
                        username TEXT,
                        title TEXT,
                        type TEXT,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(user_id, chat_id)
                    )
                """)
                await conn.commit()

        logger.info("✅ Database initialized successfully")
    except Exception as e:
        logger.error(f"❌ Database init failed: {e}")


# ==============================
# 📦 SETTINGS LOAD/SAVE
# ==============================
async def get_user_settings(user_id, username=None, first_name=None):
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT * FROM user_settings WHERE user_id=%s", (user_id,))
                    row = await cur.fetchone()
                    if row:
                        colnames = [d[0] for d in cur.description]
                        data = dict(zip(colnames, row))
                        data["selected_qualities"] = data["selected_qualities"].split(",")
                        return data

                    await cur.execute("""
                        INSERT INTO user_settings (user_id, username, first_name, base_caption)
                        VALUES (%s, %s, %s, %s)
                    """, (user_id, username, first_name, DEFAULT_CAPTION))
                    await conn.commit()
        except Exception as e:
            logger.error(f"DB error: {e}")

    # fallback
    return {
        "user_id": user_id,
        "season": 1,
        "episode": 1,
        "total_episode": 1,
        "video_count": 0,
        "selected_qualities": ["480p", "720p", "1080p"],
        "base_caption": DEFAULT_CAPTION,
        "target_chat_id": None,
    }


async def save_user_settings(settings):
    if db_pool:
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        UPDATE user_settings
                        SET season=%s, episode=%s, total_episode=%s, video_count=%s,
                            selected_qualities=%s, base_caption=%s, target_chat_id=%s,
                            updated_at=CURRENT_TIMESTAMP
                        WHERE user_id=%s
                    """, (
                        settings["season"],
                        settings["episode"],
                        settings["total_episode"],
                        settings["video_count"],
                        ",".join(settings["selected_qualities"]),
                        settings["base_caption"],
                        settings["target_chat_id"],
                        settings["user_id"],
                    ))
                await conn.commit()
        except Exception as e:
            logger.error(f"Error saving user settings: {e}")
# ==============================
# 📊 SUPPORTING FUNCTIONS
# ==============================
async def log_upload(user_id, season, episode, total_episode, quality, file_id, caption, target_chat_id):
    if not db_pool:
        return
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO upload_history
                    (user_id, season, episode, total_episode, quality, file_id, caption, target_chat_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (user_id, season, episode, total_episode, quality, file_id, caption, target_chat_id))
            await conn.commit()
    except Exception as e:
        logger.error(f"Error logging upload: {e}")


async def get_user_upload_stats(user_id):
    if not db_pool:
        return (0, 0)
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM upload_history WHERE user_id=%s", (user_id,))
                total = (await cur.fetchone())[0]
                await cur.execute("""
                    SELECT COUNT(*) FROM upload_history
                    WHERE user_id=%s AND DATE(uploaded_at)=CURRENT_DATE
                """, (user_id,))
                today = (await cur.fetchone())[0]
        return (total, today)
    except Exception as e:
        logger.error(f"Error stats: {e}")
        return (0, 0)


async def get_all_users_count():
    if not db_pool:
        return 0
    try:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM user_settings")
                count = (await cur.fetchone())[0]
        return count
    except Exception as e:
        logger.error(f"Count error: {e}")
        return 0


def get_menu_markup():
    return InlineKeyboardMarkup([
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
    ])


# ==============================
# 📩 COMMAND HANDLERS
# ==============================
@app.on_message(filters.private & filters.command("start"))
async def start(client, message):
    uid = message.from_user.id
    uname = message.from_user.username
    fname = message.from_user.first_name
    settings = await get_user_settings(uid, uname, fname)
    await message.delete()
    welcome = (
        f"👋 <b>Welcome {fname}!</b>\n\n"
        "🤖 <b>Your Anime Caption Bot</b>\n\n"
        "• Auto-caption videos\n"
        "• Multi-quality upload\n"
        "• Episode tracking\n\n"
        "Use buttons below to begin."
    )
    sent = await client.send_message(message.chat.id, welcome, parse_mode=ParseMode.HTML, reply_markup=get_menu_markup())
    last_bot_messages[message.chat.id] = sent.id


@app.on_message(filters.private & filters.command("help"))
async def help_cmd(client, message):
    await message.delete()
    text = (
        "📚 <b>Help Menu</b>\n\n"
        "/start - Show main menu\n"
        "/help - This message\n"
        "/stats - Show upload stats\n"
        "/admin - Admin panel (if allowed)\n\n"
        "Each user has their own saved settings."
    )
    await message.reply(text, parse_mode=ParseMode.HTML)


@app.on_message(filters.private & filters.command("stats"))
async def stats_cmd(client, message):
    await message.delete()
    uid = message.from_user.id
    total, today = await get_user_upload_stats(uid)
    settings = await get_user_settings(uid)
    txt = (
        f"📊 <b>Stats</b>\n\n"
        f"Season: {settings['season']}\n"
        f"Episode: {settings['episode']}\n"
        f"Uploads: {total} total / {today} today\n"
        f"Channel: {'✅ Set' if settings['target_chat_id'] else '❌ Not set'}"
    )
    await message.reply(txt, parse_mode=ParseMode.HTML)


@app.on_message(filters.private & filters.command("admin"))
async def admin_cmd(client, message):
    await message.delete()
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        await message.reply("❌ You are not an admin.", parse_mode=ParseMode.HTML)
        return
    count = await get_all_users_count()
    txt = (
        f"👑 <b>Admin Panel</b>\n\n"
        f"Total users: <code>{count}</code>\n"
        f"Bot running fine ✅"
    )
    await message.reply(txt, parse_mode=ParseMode.HTML)


# ==============================
# 🔘 CALLBACK BUTTON HANDLERS
# ==============================
@app.on_callback_query()
async def buttons(client, cb: CallbackQuery):
    uid = cb.from_user.id
    data = cb.data
    settings = await get_user_settings(uid)
    try:
        await cb.answer()
    except: 
        pass

    if data == "preview":
        quality = settings["selected_qualities"][settings["video_count"] % len(settings["selected_qualities"])]
        text = settings["base_caption"].format(
            season=settings["season"], episode=settings["episode"],
            total_episode=settings["total_episode"], quality=quality
        )
        await cb.message.reply(f"📝 <b>Preview:</b>\n\n{text}", parse_mode=ParseMode.HTML)
    elif data == "set_caption":
        waiting_for_input[uid] = "caption"
        await cb.message.reply("✏️ Send your new caption template.", parse_mode=ParseMode.HTML)
    elif data == "set_season":
        waiting_for_input[uid] = "season"
        await cb.message.reply("📺 Send new season number.", parse_mode=ParseMode.HTML)
    elif data == "set_episode":
        waiting_for_input[uid] = "episode"
        await cb.message.reply("🎬 Send new episode number.", parse_mode=ParseMode.HTML)
    elif data == "set_total_episode":
        waiting_for_input[uid] = "total_episode"
        await cb.message.reply("🔢 Send total episode count.", parse_mode=ParseMode.HTML)
    elif data == "reset":
        settings["episode"] = 1
        settings["video_count"] = 0
        await save_user_settings(settings)
        await cb.message.reply("✅ Episode counter reset.", parse_mode=ParseMode.HTML)


# ==============================
# 📨 USER INPUT RESPONSES
# ==============================
@app.on_message(filters.private & filters.text & ~filters.command(["start", "help", "stats", "admin"]))
async def text_inputs(client, message):
    uid = message.from_user.id
    if uid not in waiting_for_input:
        return
    key = waiting_for_input[uid]
    text = message.text.strip()
    settings = await get_user_settings(uid)
    if key == "caption":
        settings["base_caption"] = text
        await save_user_settings(settings)
        await message.reply("✅ Caption updated!", parse_mode=ParseMode.HTML)
    elif key in ["season", "episode", "total_episode"] and text.isdigit():
        settings[key] = int(text)
        await save_user_settings(settings)
        await message.reply(f"✅ {key.capitalize()} updated!", parse_mode=ParseMode.HTML)
    else:
        await message.reply("❌ Invalid input.", parse_mode=ParseMode.HTML)
    del waiting_for_input[uid]


# ==============================
# 🎬 VIDEO AUTO FORWARD
# ==============================
@app.on_message(filters.private & filters.video)
async def forward_video(client, message):
    uid = message.from_user.id
    if uid in waiting_for_input:
        return
    settings = await get_user_settings(uid)
    if not settings["target_chat_id"]:
        await message.reply("❌ No channel set. Use /start to configure.")
        return
    qualities = settings["selected_qualities"] or ["720p"]
    q = qualities[settings["video_count"] % len(qualities)]
    caption = settings["base_caption"].format(
        season=settings["season"],
        episode=settings["episode"],
        total_episode=settings["total_episode"],
        quality=q,
    )
    try:
        await client.send_video(settings["target_chat_id"], message.video.file_id, caption=caption, parse_mode=ParseMode.HTML)
        await log_upload(uid, settings["season"], settings["episode"], settings["total_episode"], q, message.video.file_id, caption, settings["target_chat_id"])
        settings["video_count"] += 1
        if settings["video_count"] >= len(qualities):
            settings["episode"] += 1
            settings["video_count"] = 0
        await save_user_settings(settings)
        await message.reply(f"✅ Uploaded to channel (Quality: {q})", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await message.reply("❌ Upload failed. Ensure bot is admin in the channel.")
# ==============================
# 🌐 WEBHOOK HANDLER (FIXED)
# ==============================
async def telegram_webhook(request):
    """Handle incoming webhook updates from Telegram"""
    try:
        update_dict = await request.json()
        update_id = update_dict.get("update_id", "unknown")
        logger.info(f"📨 Webhook received update ID: {update_id}")

        # Run asynchronously (non-blocking for Render)
        asyncio.create_task(process_update_manually(update_dict))
        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        return web.Response(status=200, text="OK")


async def process_update_manually(update_dict):
    """✅ Use Pyrogram’s internal processor instead of _parse()"""
    try:
        logger.info("📡 Dispatching update to Pyrogram processor…")
        await app.process_update(update_dict)
        logger.info("✅ Update processed successfully")
    except Exception as e:
        logger.error(f"❌ Error processing update manually: {e}", exc_info=True)


# ==============================
# 🩺 HEALTH CHECK & STATUS
# ==============================
async def health_check(request):
    total_users = await get_all_users_count()
    return web.Response(
        text=f"✅ Bot running fine | Users: {total_users}", content_type="text/plain"
    )


async def stats_endpoint(request):
    total_users = await get_all_users_count()
    return web.json_response(
        {
            "status": "running",
            "total_users": total_users,
            "timestamp": datetime.utcnow().isoformat(),
            "webhook": WEBHOOK_URL if WEBHOOK_URL else "polling",
        }
    )


# ==============================
# 🧰 WEBHOOK SETUP
# ==============================
async def setup_webhook():
    if not WEBHOOK_URL:
        logger.warning("⚠️ No WEBHOOK_URL provided, using polling mode.")
        return False
    try:
        import httpx
        telegram_api = f"https://api.telegram.org/bot{BOT_TOKEN}"

        async with httpx.AsyncClient() as client:
            await client.post(f"{telegram_api}/deleteWebhook", json={"drop_pending_updates": True})
            r = await client.post(
                f"{telegram_api}/setWebhook",
                json={
                    "url": WEBHOOK_URL,
                    "drop_pending_updates": True,
                    "allowed_updates": ["message", "callback_query"],
                },
            )
            res = r.json()
            if res.get("ok"):
                logger.info(f"✅ Webhook set successfully: {WEBHOOK_URL}")
                return True
            logger.error(f"❌ Failed to set webhook: {res}")
    except Exception as e:
        logger.error(f"Webhook setup error: {e}", exc_info=True)
    return False


# ==============================
# 🌍 WEB SERVER STARTUP
# ==============================
async def start_web_server():
    if WEBHOOK_URL:
        web_app.router.add_post(f"/webhook/{BOT_TOKEN}", telegram_webhook)
        logger.info(f"🔗 Webhook route registered: /webhook/{BOT_TOKEN}")
    web_app.router.add_get("/health", health_check)
    web_app.router.add_get("/stats", stats_endpoint)
    web_app.router.add_get("/", health_check)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"✅ Web server running on port {PORT}")


# ==============================
# 🔁 SELF-PING (for Render)
# ==============================
async def self_ping():
    await asyncio.sleep(60)
    while True:
        await asyncio.sleep(600)
        if RENDER_EXTERNAL_URL:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"{RENDER_EXTERNAL_URL}/health") as r:
                        logger.info(f"🔄 Self-ping: {r.status}")
            except Exception as e:
                logger.warning(f"Self-ping failed: {e}")


# ==============================
# 🚀 MAIN STARTUP
# ==============================
async def main():
    logger.info("🗄️ Initializing database…")
    await init_db()
    logger.info("🚀 Starting bot client…")

    await app.start()
    me = await app.get_me()
    logger.info(f"✅ Logged in as @{me.username} ({me.id})")

    if WEBHOOK_URL:
        ok = await setup_webhook()
        if ok:
            logger.info("🌐 Running in WEBHOOK mode.")
        else:
            logger.warning("⚠️ Webhook setup failed. Falling back to polling mode.")
    else:
        logger.info("📡 Running in POLLING mode (no webhook set).")

    await start_web_server()
    asyncio.create_task(self_ping())

    logger.info("✅ All systems operational.")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
