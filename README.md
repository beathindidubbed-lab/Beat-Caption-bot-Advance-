# 🎬 Anime Caption Bot - Multi-User Telegram Auto Forwarder

A powerful **multi-user** Telegram bot for automatically captioning and forwarding anime episodes. Each user can set their own channel, captions, and episode tracking. Features PostgreSQL database integration, custom welcome messages with media, comprehensive help system, and runs 24/7 on Render's free tier.

## ✨ Key Features

### 👥 Multi-User Support
- 🔐 **Personal Settings** - Each user has their own configuration
- 🎯 **Individual Channels** - Every user can forward to their own channel
- 📊 **Separate Statistics** - Track your own upload history
- 🔒 **Isolated Progress** - Your episodes don't affect other users
- 🎨 **Custom Captions** - Each user can have unique caption templates

### 🎨 Customization Features
- 📸 **Custom Welcome Message** - Admins can set welcome message with photo/video/GIF
- 📚 **Comprehensive Help** - Detailed `/help` command with all features explained
- 🎭 **Personalized Greetings** - Use placeholders like {first_name} and {user_id}
- 👑 **Admin Panel** - Manage bot settings and view global statistics

### Core Features
- 📤 **Auto-forward videos** with custom captions
- 🎥 **Multi-quality support** (480p, 720p, 1080p, 4K, 2160p)
- 📺 **Season & Episode tracking** with auto-increment
- 🎯 **Dynamic target channel** setting (via forward or ID)
- 📊 **Personal upload statistics** (total & daily uploads)
- 💾 **PostgreSQL database** with JSON fallback
- 🔄 **Self-ping mechanism** for 24/7 uptime
- ✏️ **Custom caption templates** with placeholders

### Advanced Features
- 🗄️ **User-specific database tables** for data isolation
- 📈 **Individual upload history** logging
- 🎯 **Per-user channel info** storage
- 🔐 **Admin panel** for global statistics
- 🌐 **Web health check** and stats endpoints
- 🔒 **Thread-safe uploads** with per-user locks

## 📋 Requirements

- Python 3.9+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- Telegram API credentials (from [my.telegram.org](https://my.telegram.org))
- PostgreSQL database (optional, Render provides free tier)
- Render account (for deployment)

## 🚀 Quick Start

### 1. Get Your Credentials

#### Telegram Bot Token
1. Message [@BotFather](https://t.me/botfather)
2. Send `/newbot` and follow instructions
3. Save your bot token

#### API Credentials
1. Visit [my.telegram.org](https://my.telegram.org)
2. Login with your phone number
3. Go to "API Development Tools"
4. Create an app and save `API_ID` and `API_HASH`

### 2. Deploy to Render

#### Option A: One-Click Deploy (via GitHub)

1. **Fork/Clone this repository**
   ```bash
   git clone https://github.com/yourusername/anime-caption-bot.git
   cd anime-caption-bot
   ```

2. **Push to your GitHub**
   ```bash
   git add .
   git commit -m "Initial commit"
   git push origin main
   ```

3. **Deploy on Render**
   - Go to [render.com](https://render.com)
   - Click "New +" → "Blueprint"
   - Connect your GitHub repository
   - Render will auto-detect `render.yaml`

4. **Configure Environment Variables**
   
   In Render dashboard, set these variables:
   
   | Variable | Value | Example |
   |----------|-------|---------|
   | `API_ID` | Your Telegram API ID | `12345678` |
   | `API_HASH` | Your Telegram API Hash | `abcdef1234567890` |
   | `BOT_TOKEN` | Your Bot Token | `123456:ABC-DEF...` |
   | `TARGET_CHAT_ID` | Default channel ID | `-1001234567890` |
   | `RENDER_EXTERNAL_URL` | Your Render URL | `https://your-app.onrender.com` |
   | `DATABASE_URL` | Auto-filled by Render | (auto) |

5. **Deploy!**
   - Click "Create Web Service"
   - Wait for deployment (2-3 minutes)
   - Your bot is now live! 🎉

#### Option B: Manual Deployment

If you prefer manual setup:

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export API_ID="your_api_id"
export API_HASH="your_api_hash"
export BOT_TOKEN="your_bot_token"
export TARGET_CHAT_ID="-1001234567890"
export PORT="10000"
export RENDER_EXTERNAL_URL="https://your-app.onrender.com"
export DATABASE_URL="postgresql://..."

# Run the bot
python bot.py
```

## 🎮 How to Use

### For Regular Users

#### Initial Setup

1. **Start the bot**
   ```
   /start
   ```
   You'll get a personalized welcome message with your own menu.

2. **Set YOUR target channel** (Choose one method)
   
   **Method 1: Forward a message (Easiest)**
   - Click "🎯 Set Target Channel"
   - Click "📤 Forward Message"
   - Forward any message from YOUR target channel
   - Bot automatically detects and saves your channel
   
   **Method 2: Send ID/Username**
   - Click "🎯 Set Target Channel"
   - Click "🔗 Send Username/ID"
   - Send: `@yourchannel` or `-1001234567890`

   ⚠️ **Important**: Make sure the bot is an admin in YOUR channel!

3. **Configure YOUR caption template**
   - Click "✏️ Set Caption"
   - Send your custom caption with placeholders:
     ```
     <b>My Anime Series</b> - <i>@MyChannel</i>
     Season {season} - Episode {episode} ({total_episode}) - {quality}
     <blockquote>Enjoy watching!</blockquote>
     ```

4. **Select YOUR video qualities**
   - Click "🎥 Quality Settings"
   - Toggle qualities on/off (your selection won't affect other users)
   - Bot will cycle through YOUR selected qualities

#### Using Your Bot

**Send Videos**
- Simply send video files to the bot
- Bot automatically:
  - Adds YOUR caption with YOUR episode info
  - Forwards to YOUR channel only
  - Cycles through YOUR selected qualities
  - Increments YOUR episode counter
  - Logs to YOUR upload history

**Manage Your Episodes**
- 📺 **Set Season**: Change YOUR current season number
- 🎬 **Set Episode**: Change YOUR current episode number
- 🔢 **Set Total Episode**: Update YOUR total episode count
- 🔄 **Reset Episode**: Reset YOUR episode counter to 1

**View Your Information**
- 🔍 **Preview Caption**: See how YOUR caption will look
- 📊 **My Statistics**: View YOUR upload stats and settings
- `/stats` - Quick command to see YOUR statistics

### For Admins

**Admin Commands**
```
/admin - View global statistics (admin only)
```

Shows:
- Total number of users
- System status
- Overall bot health

To enable admin features, add your user ID to the `ADMIN_IDS` list in the code.

### Menu Options

```
🔍 Preview Caption      - Preview YOUR caption format
✏️ Set Caption         - Update YOUR caption template
📺 Set Season          - Change YOUR season number
🎬 Set Episode         - Change YOUR episode number
🔢 Set Total Episode   - Update YOUR total episode count
🎥 Quality Settings    - Select YOUR video qualities
🎯 Set Target Channel  - Change YOUR target channel
📊 My Statistics       - View YOUR upload stats
🔄 Reset Episode       - Reset YOUR episode to 1
❌ Cancel              - Cancel current operation
```

### Available Commands

```
/start  - Initialize YOUR bot settings and show menu
/stats  - View YOUR personal upload statistics
/admin  - View global bot statistics (admin only)
```

## 👥 Multi-User Examples

### Example Scenario

**User A (Anime Channel Owner)**
- Channel: @AnimeWorldHindi
- Season: 2, Episode: 15
- Qualities: 480p, 720p, 1080p
- Caption: "Anime World - Season {season} Ep {episode}"

**User B (Cartoon Channel Owner)**
- Channel: @CartoonDubsHindi  
- Season: 1, Episode: 5
- Qualities: 720p, 1080p
- Caption: "Cartoon Dubs - S{season}E{episode} - {quality}"

**User C (Movie Channel Owner)**
- Channel: @MoviesChannelHD
- Season: 1, Episode: 1
- Qualities: 1080p, 4K
- Caption: "Movies HD - Episode {total_episode}"

✅ All three users can use the bot **simultaneously**
✅ Their settings are **completely separate**
✅ Videos go to their **own channels only**
✅ **No interference** between users

## 📝 Caption Placeholders

Use these placeholders in your caption template:

| Placeholder | Description | Example Output |
|-------------|-------------|----------------|
| `{season}` | Season number (2 digits) | `01` |
| `{episode}` | Episode number (2 digits) | `05` |
| `{total_episode}` | Total episode count (2 digits) | `125` |
| `{quality}` | Current video quality | `1080p` |

**Example Caption:**
```html
<b>Attack on Titan</b> - <i>@AnimeWorld</i>
Season {season} - Episode {episode} ({total_episode}) - {quality}
<blockquote>🔥 Don't miss this epic episode!</blockquote>
```

**Output:**
```
Attack on Titan - @AnimeWorld
Season 01 - Episode 05 (125) - 1080p
🔥 Don't miss this epic episode!
```

## 🗄️ Database Schema

### Multi-User Database Structure

#### `user_settings`
Stores individual user configuration and progress.

```sql
- user_id (BIGINT PRIMARY KEY) - Telegram user ID
- username (TEXT) - Telegram username
- first_name (TEXT) - User's first name
- season (INTEGER) - User's current season
- episode (INTEGER) - User's current episode
- total_episode (INTEGER) - User's total episode count
- video_count (INTEGER) - Videos uploaded for current episode
- selected_qualities (TEXT) - User's selected qualities (comma-separated)
- base_caption (TEXT) - User's caption template
- target_chat_id (BIGINT) - User's target channel ID
- created_at (TIMESTAMP) - Account creation date
- updated_at (TIMESTAMP) - Last update timestamp
```

#### `upload_history`
Logs all video uploads per user.

```sql
- id (SERIAL PRIMARY KEY)
- user_id (BIGINT) - User who uploaded
- season (INTEGER) - Season number
- episode (INTEGER) - Episode number
- total_episode (INTEGER) - Total episode count
- quality (TEXT) - Video quality
- file_id (TEXT) - Telegram file ID
- caption (TEXT) - Applied caption
- target_chat_id (BIGINT) - Destination channel
- uploaded_at (TIMESTAMP) - Upload timestamp
```

#### `channel_info`
Stores channel information per user.

```sql
- user_id (BIGINT) - User who owns this channel config
- chat_id (BIGINT) - Channel ID
- username (TEXT) - Channel username
- title (TEXT) - Channel title
- type (TEXT) - Channel type
- added_at (TIMESTAMP) - When added
- PRIMARY KEY (user_id, chat_id) - Composite key
```

### Data Isolation

- ✅ Each user has completely separate settings
- ✅ Episode tracking is per-user
- ✅ Upload history is per-user
- ✅ One user's actions don't affect others
- ✅ Multiple users can use the bot simultaneously

## 🔧 Configuration

### Environment Variables

| Variable | Required | Description | Default |
|----------|----------|-------------|---------|
| `API_ID` | ✅ Yes | Telegram API ID | - |
| `API_HASH` | ✅ Yes | Telegram API Hash | - |
| `BOT_TOKEN` | ✅ Yes | Telegram Bot Token | - |
| `TARGET_CHAT_ID` | ✅ Yes | Default target channel ID | - |
| `PORT` | ❌ No | Web server port | `10000` |
| `RENDER_EXTERNAL_URL` | ⚠️ Recommended | Your Render app URL | - |
| `DATABASE_URL` | ❌ No | PostgreSQL connection string | JSON fallback |

### Quality Options

Available video qualities:
- 480p
- 720p
- 1080p
- 4K
- 2160p

Select multiple qualities for automatic cycling per episode.

## 🛠️ Troubleshooting

### Bot not forwarding videos

**Problem:** Videos not being forwarded to channel

**Solutions:**
1. ✅ Ensure bot is admin in YOUR target channel
2. ✅ Check YOUR `target_chat_id` is correct (use forward method)
3. ✅ Verify at least one quality is selected in YOUR settings
4. ✅ Check bot has permission to post in YOUR channel
5. ✅ Make sure you've set up YOUR own channel (use /start)

### "No target channel set" error

**Problem:** Bot says no channel is configured

**Solutions:**
1. ✅ You need to set YOUR own channel first
2. ✅ Use "🎯 Set Target Channel" from the menu
3. ✅ Each user must configure their own channel
4. ✅ Other users' channels don't apply to you

### Database connection failed

**Problem:** PostgreSQL connection error

**Solutions:**
1. ✅ Verify `DATABASE_URL` is set correctly in Render
2. ✅ Bot will automatically fallback to per-user JSON files
3. ✅ Check Render database is running
4. ✅ Restart the service
5. ✅ Multi-user functionality works with JSON fallback too

### Videos going to wrong channel

**Problem:** Videos appear in someone else's channel

**Solution:**
- ❌ This should **NEVER** happen! Each user has isolated settings.
- ✅ If this occurs, check that you're using the correct bot account
- ✅ Verify YOUR channel settings with /stats command
- ✅ Re-set your channel using "🎯 Set Target Channel"

### Self-ping not working

**Problem:** Bot goes offline after 15 minutes

**Solutions:**
1. ✅ Set `RENDER_EXTERNAL_URL` correctly in environment variables
2. ✅ Format: `https://your-app-name.onrender.com`
3. ✅ Don't include trailing slash
4. ✅ Check web server logs for ping status

### Bot not responding to specific user

**Problem:** Bot works for others but not for you

**Solutions:**
1. ✅ Send `/start` to initialize YOUR settings
2. ✅ Check if you're blocked by the bot (shouldn't happen)
3. ✅ Verify YOUR Telegram account is working
4. ✅ Try clearing chat and sending /start again

## 📊 Usage Statistics

### Personal Statistics
Access YOUR stats via bot menu or command:
- `/stats` - View YOUR upload statistics
- Click "📊 My Statistics" button in menu

**Your Available Stats:**
- 📤 YOUR total uploads
- 📅 YOUR today's uploads
- 📺 YOUR current season
- 🎬 YOUR current episode
- 🔢 YOUR total episodes
- 🎯 YOUR target channel ID & status
- 🎥 YOUR selected qualities

### Admin Statistics (Admin Only)
Access global stats:
- `/admin` - View system-wide statistics

**Admin Stats Include:**
- 👥 Total number of users
- 📊 System status
- 🤖 Bot health information

**Note:** Each user's data is completely private and isolated. Admins can only see aggregate counts, not individual user data.

## 🔒 Security & Privacy

### Data Isolation
- ✅ Each user has completely separate database records
- ✅ One user cannot access another user's settings
- ✅ Upload history is private per user
- ✅ Channel configurations are isolated
- ✅ No cross-user data sharing

### Best Practices

1. **Never commit credentials**
   ```bash
   # Add to .gitignore
   .env
   user_*_progress.json
   *.session
   *.session-journal
   ```

2. **Use environment variables**
   - Never hardcode API keys in code
   - Use Render's secret management
   - Regenerate tokens if exposed

3. **Bot Access Control**
   - Anyone can use the bot (multi-user design)
   - Each user can only control their own settings
   - Admin features require specific user IDs
   - Users can't interfere with each other

4. **Channel Security**
   - Bot needs admin rights in each user's channel
   - Users should verify bot permissions
   - Regularly check which channels bot has access to
   - Remove bot from channels no longer in use

5. **Database Security**
   - Use Render's managed PostgreSQL
   - Don't expose DATABASE_URL publicly
   - Enable SSL for database connections
   - Regular backups recommended

### Privacy Notes
- Bot stores: user_id, username, first_name, channel IDs
- No message content is stored (only file IDs)
- Captions are stored for upload history
- Users can request data deletion (implement manually if needed)

## 🆘 Support

### Common Commands

```bash
/start  - Initialize bot and show menu
/stats  - View upload statistics
```

### Getting Help

1. Check logs in Render dashboard
2. Review error messages in bot chat
3. Verify all environment variables
4. Test with health endpoint: `https://your-app.onrender.com/health`

### Render Free Tier Limits

- ✅ 750 hours/month runtime
- ✅ 512 MB RAM
- ✅ Spins down after 15 minutes inactivity (prevented by self-ping)
- ✅ Free PostgreSQL database included

## 📄 License

This project is open source and available under the MIT License.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## ⚠️ Disclaimer

This bot is for educational purposes. Ensure you have rights to distribute content through your Telegram channel. Follow Telegram's Terms of Service.

---

**Made with ❤️ for the anime community**

*For questions or issues, please open a GitHub issue.*