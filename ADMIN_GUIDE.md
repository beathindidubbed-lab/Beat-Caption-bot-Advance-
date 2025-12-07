# 👑 Admin Guide - Welcome Message & Commands

## 🎯 New Features Added

### 1. Custom Welcome Message with Media
Admins can now set a custom welcome message with photo/video/GIF that will be shown to all users when they use `/start`.

### 2. Comprehensive Help Command
New `/help` command shows detailed information about all bot features and commands.

## 🔧 Setting Up Admin Access

### Step 1: Get Your Telegram User ID

1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It will reply with your user ID (e.g., `123456789`)

### Step 2: Add Your ID to Admin List

Edit `bot.py` and find this line (around line 385):

```python
ADMIN_IDS = [user_id]  # Add your admin user IDs
```

Replace with your actual user ID(s):

```python
ADMIN_IDS = [123456789, 987654321]  # Your admin user ID(s)
```

You can add multiple admin IDs separated by commas.

### Step 3: Redeploy

After editing, commit and push changes to trigger Render redeployment.

## 📝 Setting Custom Welcome Message

### Method 1: Using Admin Panel

1. Send `/admin` to the bot
2. Click "📝 Set Welcome Message"
3. Send a photo, video, or GIF with a caption
4. Done! All users will see this when they use `/start`

### Method 2: Direct Command

1. Send `/admin` command
2. Follow the prompts

### Welcome Message Features

#### Available Placeholders

Use these in your welcome caption:

- `{first_name}` - User's first name
- `{user_id}` - User's Telegram ID

#### Example Welcome Messages

**Example 1: Photo with Text**
```
Send a nice anime-themed photo with caption:

Welcome {first_name}! 🎬

🤖 Your Personal Anime Bot
ID: {user_id}

Get started by setting your channel!
```

**Example 2: Animated GIF**
```
Send an anime GIF with caption:

👋 Hi {first_name}!

✨ Ready to automate your anime uploads?
🆔 Your ID: {user_id}

Use the buttons below to configure!
```

**Example 3: Video**
```
Send a short intro video with caption:

🎥 Welcome to Anime Caption Bot!

Hello {first_name}! 
User ID: {user_id}

Let's get you set up! 🚀
```

## 👁️ Preview Welcome Message

### Check Current Welcome

1. Send `/admin` to the bot
2. Click "👁️ Preview Welcome"
3. Bot shows the current welcome message

This lets you see exactly what users will see when they use `/start`.

## 📊 Admin Commands & Features

### Available Admin Commands

```bash
/admin              # Open admin panel
/help               # Show comprehensive help (all users)
/stats              # Personal statistics (all users)
```

### Admin Panel Options

When you send `/admin`, you get these options:

```
📝 Set Welcome Message    # Set custom welcome with media
👁️ Preview Welcome        # See current welcome message
📊 Global Stats           # View system-wide statistics
⬅️ Back to User Menu      # Return to normal menu
```

### Global Statistics

The admin panel shows:

- 👥 **Total Users**: Number of users using the bot
- 🤖 **Bot Status**: Running/Stopped
- 🗄️ **Database**: Connection status
- 📊 **System Health**: Overall status

## 📚 Help Command Details

### What `/help` Shows

The `/help` command displays:

1. **Basic Commands**
   - /start, /help, /stats, /admin

2. **Menu Features** (Detailed explanation of each button)
   - Preview Caption
   - Set Caption
   - Set Season/Episode/Total Episode
   - Quality Settings
   - Set Target Channel
   - My Statistics
   - Reset Episode

3. **Video Upload Process**
   - Step-by-step guide

4. **Tips & Best Practices**
   - How to use the bot effectively

5. **Privacy Information**
   - Data isolation details

6. **Troubleshooting**
   - Common issues and solutions

### Help Command Access

- ✅ Available to **all users** (not admin-only)
- ✅ Can be used anytime
- ✅ Shows personalized information
- ✅ No rate limits

## 🎨 Welcome Message Best Practices

### Design Tips

1. **Keep It Concise**
   - Don't overwhelm users with too much text
   - Use emojis for visual appeal
   - Break text into sections

2. **Use Placeholders**
   - Personalize with `{first_name}`
   - Show user ID if needed for support

3. **Clear Call-to-Action**
   - Tell users what to do first
   - Guide them to set up channel
   - Mention the /help command

4. **Visual Appeal**
   - Use anime-themed images/GIFs
   - Choose high-quality media
   - Match your channel's branding

### Content Suggestions

**For Anime Channels:**
```
🎬 Welcome {first_name}!

Your personal anime upload assistant
User ID: {user_id}

Quick Setup:
1️⃣ Set your channel
2️⃣ Configure caption
3️⃣ Start uploading!

Type /help for detailed guide
```

**For Professional Use:**
```
Hello {first_name}! 👋

Professional Video Management Bot
Account ID: {user_id}

Features:
✓ Multi-quality support
✓ Auto-captioning
✓ Episode tracking

Get started below! ⬇️
```

**For Fun/Casual:**
```
Yo {first_name}! 🔥

Let's automate those uploads! 🚀
Your ID: {user_id}

Hit those buttons and let's go! 
Need help? Use /help anytime!
```

## 🔄 Changing Welcome Message

### To Update

1. Send `/admin`
2. Click "📝 Set Welcome Message"
3. Send new media with caption
4. Old welcome is replaced automatically

### To Remove Custom Welcome

Currently, custom welcome can only be replaced, not removed. To use default:

1. Contact developer to add "Reset to Default" feature
2. Or manually delete from database

## 🗄️ Database Storage

### Welcome Message Storage

Welcome messages are stored in the `welcome_settings` table:

```sql
- id: Auto-increment ID
- message_type: 'photo', 'video', or 'animation'
- file_id: Telegram file ID
- caption: Welcome text with placeholders
- created_at: When it was set
- updated_at: Last update time
```

### Data Persistence

- ✅ Survives bot restarts
- ✅ Stored in PostgreSQL
- ✅ No JSON fallback (admin feature requires DB)
- ✅ Only latest welcome message is active

## 📱 Mobile Admin Usage

### Using Admin Panel on Phone

1. Open Telegram on mobile
2. Find your bot
3. Send `/admin`
4. Tap menu buttons
5. To set welcome:
   - Tap "📝 Set Welcome Message"
   - Choose photo/video from gallery
   - Add caption
   - Send

Works perfectly on mobile! 📱

## 🔒 Security Considerations

### Admin Access Control

- ✅ Only users in `ADMIN_IDS` list can use admin features
- ✅ Non-admins get "Permission denied" message
- ✅ Admin features don't affect user data
- ✅ Admins can only see aggregate stats, not individual user data

### Best Practices

1. **Protect Admin IDs**
   - Don't share your user ID publicly
   - Keep the ADMIN_IDS list in environment variables (future enhancement)
   - Use only trusted people as admins

2. **Welcome Message Content**
   - Don't include sensitive information
   - Keep content appropriate
   - Test before publishing to all users

3. **Media Selection**
   - Use copyright-free images
   - Keep file sizes reasonable
   - Test that media loads quickly

## 🐛 Troubleshooting

### Welcome Message Not Showing

**Problem:** Custom welcome doesn't appear

**Solutions:**
- ✅ Check if database is connected
- ✅ Verify welcome was saved (use Preview)
- ✅ Try setting again
- ✅ Check Render logs for errors

### Admin Command Not Working

**Problem:** `/admin` says no permission

**Solutions:**
- ✅ Verify your user ID is in ADMIN_IDS list
- ✅ Restart bot after adding ID
- ✅ Check if code was properly deployed
- ✅ Use @userinfobot to confirm your ID

### Help Command Issues

**Problem:** `/help` doesn't respond

**Solutions:**
- ✅ Check bot is running
- ✅ Verify command filter isn't blocking
- ✅ Try /start first
- ✅ Check Render logs

### Media Not Uploading

**Problem:** Can't set photo/video for welcome

**Solutions:**
- ✅ Send media WITH caption
- ✅ Use supported formats (photo, video, GIF)
- ✅ Check file size isn't too large
- ✅ Try different media file

## 📊 Monitoring Admin Actions

### Check Admin Activity

View Render logs to see:
- When welcome messages are changed
- Who accessed admin panel
- Error messages if any
- Database operations

### Log Messages

Look for these in logs:
```
✅ Welcome message updated!
✅ Admin panel accessed by user_id: 123456
❌ Error saving welcome message: ...
```

## 🎯 Future Admin Enhancements

Possible features for future versions:

- [ ] Multiple admin levels (super admin, moderator)
- [ ] Broadcast messages to all users
- [ ] User management (ban/unban)
- [ ] Detailed analytics dashboard
- [ ] Welcome message A/B testing
- [ ] Scheduled welcome message changes
- [ ] Welcome message templates
- [ ] Rich media carousel
- [ ] User feedback collection

## 📝 Summary

### For Admins

1. ✅ Set your user ID in ADMIN_IDS
2. ✅ Use `/admin` to access admin panel
3. ✅ Set custom welcome with media
4. ✅ Preview before publishing
5. ✅ Monitor global stats

### For All Users

1. ✅ Use `/help` for detailed guide
2. ✅ Use `/start` to see welcome
3. ✅ Use `/stats` for personal info
4. ✅ Enjoy the bot features!

---

**Need help with admin features?** Open an issue on GitHub or contact the bot developer.