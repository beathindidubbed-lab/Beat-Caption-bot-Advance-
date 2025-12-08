# 🚀 Complete Deployment Guide

## 📋 Prerequisites

Before starting, gather these credentials:

1. **Telegram Bot Token** - Get from [@BotFather](https://t.me/botfather)
2. **API ID & Hash** - Get from [my.telegram.org](https://my.telegram.org)
3. **Your Telegram User ID** - Get from [@userinfobot](https://t.me/userinfobot)
4. **GitHub Account** - For code deployment
5. **Render Account** - For hosting (free tier)

---

## 🔑 Step 1: Get Your Credentials

### 1.1 Get Bot Token

1. Message [@BotFather](https://t.me/botfather) on Telegram
2. Send `/newbot`
3. Follow instructions to create bot
4. Save your bot token: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`

### 1.2 Get API Credentials

1. Visit [my.telegram.org](https://my.telegram.org)
2. Login with your phone number
3. Go to "API Development Tools"
4. Create an app (any name/description)
5. Save your `API_ID` and `API_HASH`

### 1.3 Get Your User ID

1. Message [@userinfobot](https://t.me/userinfobot)
2. It will reply with your user ID
3. Save this number (e.g., `123456789`)

---

## 📁 Step 2: Prepare Your Code

### 2.1 Create Project Folder

```bash
mkdir telegram-anime-bot
cd telegram-anime-bot
```

### 2.2 Create All Files

Create these 7 files with the provided code:

**File Structure:**
```
telegram-anime-bot/
├── bot.py                  (Combine Parts 1-7)
├── requirements.txt
├── render.yaml
├── .gitignore
├── .env.example
└── README.md
```

### 2.3 Combine bot.py Parts

Create `bot.py` by combining all 7 parts in order:
- Part 1: Imports & Config
- Part 2: Database Functions
- Part 3: UI Functions & Message Handlers
- Part 4: Text & Media Input Handlers
- Part 5: Video Handler & Callback Part 1
- Part 6: Callback Handler Part 2
- Part 7: Web Server & Main Function

**Simply copy each part into one file in order!**

---

## 🌐 Step 3: Deploy to GitHub

### 3.1 Initialize Git Repository

```bash
git init
git add .
git commit -m "Initial commit: Telegram Anime Caption Bot"
```

### 3.2 Create GitHub Repository

1. Go to [GitHub](https://github.com)
2. Click "New Repository"
3. Name: `telegram-anime-bot`
4. Make it **Private** (recommended)
5. Don't initialize with README
6. Click "Create Repository"

### 3.3 Push to GitHub

```bash
git remote add origin https://github.com/YOUR_USERNAME/telegram-anime-bot.git
git branch -M main
git push -u origin main
```

---

## ☁️ Step 4: Deploy to Render

### 4.1 Create Render Account

1. Go to [render.com](https://render.com)
2. Sign up with GitHub
3. Verify your email

### 4.2 Create New Web Service

1. Click "New +" → "Web Service"
2. Click "Build and deploy from a Git repository"
3. Click "Next"
4. Connect your GitHub account if not already
5. Find and select your `telegram-anime-bot` repository
6. Click "Connect"

### 4.3 Configure Web Service

**Basic Settings:**
- **Name:** `telegram-anime-bot` (or your choice)
- **Region:** Choose closest to you
- **Branch:** `main`
- **Root Directory:** Leave empty
- **Runtime:** `Python 3`
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `python bot.py`

**Instance Type:**
- Select **Free** ($0/month)

### 4.4 Add Environment Variables

Click "Advanced" and add these environment variables:

| Key | Value | Example |
|-----|-------|---------|
| `API_ID` | Your API ID | `12345678` |
| `API_HASH` | Your API Hash | `abcdef1234567890` |
| `BOT_TOKEN` | Your Bot Token | `123456:ABC-DEF...` |
| `ADMIN_IDS` | Your User ID | `123456789` |
| `PORT` | Port number | `10000` |
| `RENDER_EXTERNAL_URL` | (Will add after deploy) | - |

**Leave `DATABASE_URL` empty for now - Render will auto-fill it**

### 4.5 Add PostgreSQL Database

1. Still in Render dashboard, click "New +" → "PostgreSQL"
2. **Name:** `telegram-bot-db`
3. **Database:** `telegram_bot`
4. **User:** Auto-generated
5. **Region:** Same as web service
6. **Plan:** Free
7. Click "Create Database"

### 4.6 Connect Database to Web Service

1. Go back to your web service
2. Click "Environment"
3. Find `DATABASE_URL`
4. Click "Link" and select your PostgreSQL database
5. Render will automatically fill the connection string

### 4.7 Complete Deployment

1. Click "Create Web Service"
2. Wait 2-3 minutes for deployment
3. Your app URL will be: `https://your-app-name.onrender.com`

### 4.8 Add RENDER_EXTERNAL_URL

1. Copy your Render app URL
2. Go to "Environment" tab
3. Find `RENDER_EXTERNAL_URL` variable
4. Add your URL: `https://your-app-name.onrender.com`
5. Click "Save Changes"
6. Bot will auto-redeploy

---

## ✅ Step 5: Verify Deployment

### 5.1 Check Logs

1. In Render dashboard, click "Logs"
2. Look for these messages:

```
✅ PostgreSQL database initialized successfully
✅ Pyrogram client started successfully
📋 Total handlers registered: 10
✅ Handlers successfully registered
✅ ALL SYSTEMS OPERATIONAL
```

**If you see "Zero handlers"**, contact me for help.

### 5.2 Test Health Endpoint

Visit: `https://your-app-name.onrender.com/health`

Should show: `Bot Running ✅ | Handlers: 10`

### 5.3 Test Bot on Telegram

1. Find your bot on Telegram
2. Send `/start`
3. You should get welcome message with menu buttons
4. Try clicking buttons to test functionality

---

## 🎯 Step 6: Configure Your Bot

### 6.1 Set Your Target Channel

**Method 1: Forward Message (Easiest)**
1. Click "🎯 Set Target Channel"
2. Click "📤 Forward Message"
3. Forward any message from your channel
4. Bot automatically saves channel ID

**Method 2: Send Channel ID**
1. Click "🎯 Set Target Channel"
2. Click "🔗 Send Username/ID"
3. Send: `@yourchannel` or `-1001234567890`

**⚠️ Important:** Bot must be **admin** in your channel!

### 6.2 Configure Caption Template

1. Click "✏️ Set Caption"
2. Send your custom caption with placeholders:

```html
<b>My Anime Series</b> - <i>@MyChannel</i>
Season {season} - Episode {episode} ({total_episode}) - {quality}
<blockquote>Enjoy watching!</blockquote>
```

### 6.3 Select Video Qualities

1. Click "🎥 Quality Settings"
2. Toggle qualities on/off
3. Bot will cycle through selected qualities

### 6.4 Start Uploading

Simply send video files to the bot!

---

## 🔧 Troubleshooting

### Bot Not Responding

**Problem:** Bot doesn't respond to /start

**Solutions:**
1. Check Render logs for "handlers registered"
2. Verify all environment variables are set
3. Make sure bot is not stopped
4. Try redeploying

### "Zero Handlers" Error

**Problem:** Logs show "Zero handlers registered"

**Solutions:**
1. Verify bot.py has all 7 parts combined correctly
2. Check for syntax errors in bot.py
3. Make sure decorators (`@app.on_message`) are present
4. Redeploy and check logs again

### Videos Not Forwarding

**Problem:** Videos sent but not forwarded to channel

**Solutions:**
1. Make sure bot is **admin** in target channel
2. Verify target channel is set correctly
3. Check at least one quality is selected
4. Try `/stats` to see your settings

### Database Connection Failed

**Problem:** "Database initialization failed" in logs

**Solutions:**
1. Check if PostgreSQL database is created
2. Verify DATABASE_URL is linked correctly
3. Bot will fallback to JSON files automatically
4. Multi-user still works with JSON fallback

### Bot Goes Offline After 15 Minutes

**Problem:** Bot stops responding after inactivity

**Solutions:**
1. Verify `RENDER_EXTERNAL_URL` is set correctly
2. Check self-ping is running in logs
3. Make sure URL has no trailing slash
4. Health endpoint should be accessible

---

## 👑 Admin Features Setup

### Enable Admin Panel

1. You already added your `ADMIN_IDS` in environment variables
2. Send `/admin` to bot
3. You should see admin panel

### Set Custom Welcome Message

1. Send `/admin`
2. Click "📝 Set Welcome Message"
3. Send photo/video/GIF with caption
4. Send final caption text
5. All new users will see this welcome

### View Global Statistics

1. Send `/admin`
2. Click "📊 Global Stats"
3. See total users and system status

---

## 📊 Usage Tips

### For Best Results

1. **Set channel first** before uploading videos
2. **Preview caption** before uploading
3. **Select qualities** that you actually use
4. **Check stats** regularly to monitor uploads
5. **Reset episode** when starting new episode

### Caption Placeholders

Use these in your caption template:

- `{season}` → 01
- `{episode}` → 10
- `{total_episode}` → 125
- `{quality}` → 1080p

### Video Upload Process

1. Bot receives video
2. Applies caption with current episode info
3. Forwards to your channel
4. Cycles to next quality
5. Auto-increments episode when all qualities done

---

## 🔄 Updating the Bot

### To Update Code

```bash
# Make changes to bot.py
git add .
git commit -m "Update: description of changes"
git push origin main
```

Render will automatically redeploy!

### To Add New Environment Variable

1. Go to Render dashboard
2. Click "Environment"
3. Add new variable
4. Click "Save Changes"
5. Bot will auto-redeploy

---

## 💾 Backup & Restore

### Backup Database

From Render dashboard:
1. Go to PostgreSQL database
2. Click "Connections"
3. Use connection string with `pg_dump` command

### Export User Data

Currently stored in PostgreSQL. To export:
```bash
# Connect to database and export tables
pg_dump -h <host> -U <user> -d <database> > backup.sql
```

---

## 🆘 Getting Help

### Check Documentation

- README.md - Full feature documentation
- ADMIN_GUIDE.md - Admin features guide
- CHANGELOG.md - Version history

### Debug Checklist

- [ ] All environment variables set correctly
- [ ] Bot token is valid
- [ ] API_ID and API_HASH are correct
- [ ] Database is connected
- [ ] Handlers are registered (check logs)
- [ ] Bot is admin in target channel
- [ ] RENDER_EXTERNAL_URL is set correctly

### Common Issues

**404 on root URL:** Normal - only `/health` endpoint exists

**Bot stopped:** Free tier sleeps after 15 min - self-ping keeps it alive

**Can't find channel:** Bot must be admin first

**Caption not updating:** Make sure to save after editing

---

## 🎉 Success Checklist

After deployment, verify:

- [x] Bot responds to `/start`
- [x] Menu buttons work
- [x] Can set target channel
- [x] Can modify caption template
- [x] Can select qualities
- [x] Videos forward correctly
- [x] Episode auto-increments
- [x] Statistics are tracked
- [x] Admin panel accessible (if admin)
- [x] Health endpoint returns 200
- [x] No errors in Render logs

---

## 🚀 You're All Set!

Your bot is now:
- ✅ Running 24/7 on Render
- ✅ Multi-user capable
- ✅ Database-backed
- ✅ Auto-incrementing episodes
- ✅ Quality cycling
- ✅ Fully featured

**Enjoy your automated anime upload bot!** 🎬🤖

---

**Need more help?** Check the logs, review documentation, or open a GitHub issue.
