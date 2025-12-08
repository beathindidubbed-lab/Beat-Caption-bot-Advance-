# 📦 How to Assemble bot.py

## Quick Assembly Instructions

Your complete `bot.py` file is provided in **7 parts**. Simply copy them **in order** into one file.

### Order of Parts

```
bot.py = Part 1 + Part 2 + Part 3 + Part 4 + Part 5 + Part 6 + Part 7
```

### What Each Part Contains

**Part 1: Imports & Configuration**
- All imports
- Logging setup
- Environment variables
- Default settings
- Pyrogram client initialization

**Part 2: Database Functions**
- `init_db()` - Initialize PostgreSQL
- `get_user_settings()` - Load user data
- `save_user_settings()` - Save user data
- `log_upload()` - Track uploads
- `save_channel_info()` - Store channel data
- `get_user_upload_stats()` - Statistics
- `get_all_users_count()` - Total users
- `get_welcome_message()` - Custom welcome
- `save_welcome_message()` - Save welcome

**Part 3: UI Functions & Message Handlers**
- `delete_last_message()` - Clean UI
- `get_menu_markup()` - Main menu
- `get_admin_menu_markup()` - Admin menu
- `get_quality_markup()` - Quality selection
- `get_channel_set_markup()` - Channel setup
- `/start` command handler
- `/help` command handler
- `/stats` command handler
- `/admin` command handler

**Part 4: Text & Media Input Handlers**
- Text input processor (captions, numbers, etc.)
- Forwarded message handler (for channel setup)
- Media upload handler (for welcome messages)

**Part 5: Video Handler & Callback Part 1**
- Video upload and forwarding logic
- Callback query handler (first half)
- Admin callbacks
- User action callbacks (preview, set caption, etc.)

**Part 6: Callback Handler Part 2**
- Callback query handler (second half)
- Quality toggle
- Channel setup callbacks
- Statistics display
- Reset and cancel actions

**Part 7: Web Server & Main Function**
- Health check endpoint
- Self-ping mechanism
- Web server startup
- Keep-alive function
- Main execution function
- Entry point

---

## Step-by-Step Assembly

### Method 1: Copy All at Once (Recommended)

1. Create new file `bot.py`
2. Copy Part 1 completely
3. Paste Part 2 **directly below** Part 1 (no gap)
4. Paste Part 3 below Part 2
5. Paste Part 4 below Part 3
6. Paste Part 5 below Part 4
7. Paste Part 6 below Part 5
8. Paste Part 7 below Part 6
9. Save file

**Total Lines:** ~900-1000 lines

### Method 2: Manual Line-by-Line (If Needed)

If you need to verify each section:

```python
# ===== PART 1: Imports & Config =====
# Copy Part 1 here...

# ===== PART 2: Database Functions =====
# Copy Part 2 here...

# ===== PART 3: UI & Message Handlers =====
# Copy Part 3 here...

# ===== PART 4: Input Handlers =====
# Copy Part 4 here...

# ===== PART 5: Video & Callbacks Part 1 =====
# Copy Part 5 here...

# ===== PART 6: Callbacks Part 2 =====
# Copy Part 6 here...

# ===== PART 7: Web Server & Main =====
# Copy Part 7 here...
```

---

## Verification Checklist

After assembling, verify your `bot.py` has:

- [ ] All imports at the top
- [ ] `app = Client(...)` declaration
- [ ] All `async def` functions
- [ ] All `@app.on_message()` decorators
- [ ] All `@app.on_callback_query()` decorators
- [ ] `async def main():` function
- [ ] `if __name__ == "__main__":` at the end
- [ ] No duplicate code sections
- [ ] Proper indentation throughout

---

## Common Assembly Mistakes

### ❌ Missing Decorators

**Wrong:**
```python
async def start_handler(client, message):
    # Missing @app.on_message() decorator
```

**Correct:**
```python
@app.on_message(filters.private & filters.command("start"))
async def start_handler(client, message):
    # Has decorator
```

### ❌ Incomplete Callback Handler

The callback handler is split across **Part 5 and Part 6**. Make sure both parts are included:

**Part 5 ends with:**
```python
        last_bot_messages[chat_id] = sent.id
```

**Part 6 starts with:**
```python
    # CONTINUATION OF CALLBACK HANDLER
    
    elif data == "set_caption":
```

### ❌ Wrong Order

Parts must be in correct order. For example:
- Part 2 (database functions) must come **before** Part 3 (which uses them)
- Part 7 (main function) must come **last**

---

## File Size Check

Your assembled `bot.py` should be:
- **Size:** ~50-60 KB
- **Lines:** ~900-1000 lines
- **Characters:** ~50,000-60,000

If significantly different, you may have missed a part.

---

## Quick Test

After assembly, test syntax:

```bash
# Check for syntax errors
python -m py_compile bot.py

# If no output, syntax is correct
# If errors shown, check the line numbers
```

---

## IDE Tips

### VS Code
- Use "Format Document" (Shift+Alt+F) after assembly
- Check for red underlines (syntax errors)
- Use "Collapse All" to see structure

### PyCharm
- Right-click → "Reformat Code"
- Check for error highlights
- Use "Code" → "Inspect Code"

### Sublime Text
- Install Python linter
- Check for indentation consistency
- Use "Reindent Lines"

---

## Final Structure Overview

```
bot.py structure:

1. Imports and setup (Part 1)
   ├── Standard library imports
   ├── Third-party imports
   ├── Logging configuration
   ├── Environment variables
   └── Pyrogram client

2. Helper functions (Part 2)
   ├── User lock management
   └── Database operations

3. UI and handlers (Parts 3-6)
   ├── Menu keyboards
   ├── Command handlers (/start, /help, /stats, /admin)
   ├── Text input handlers
   ├── Media handlers
   ├── Video upload handler
   └── Callback query handler

4. Server and main (Part 7)
   ├── Web server (health check)
   ├── Self-ping mechanism
   ├── Keep-alive function
   ├── Main execution
   └── Entry point
```

---

## After Assembly

Once assembled correctly:

1. ✅ Save as `bot.py`
2. ✅ Add other files (requirements.txt, render.yaml, etc.)
3. ✅ Follow deployment guide
4. ✅ Push to GitHub
5. ✅ Deploy to Render

---

## Need Help?

If you encounter issues during assembly:

1. **Check line numbers** - Error messages show exact location
2. **Verify indentation** - Python is strict about indentation
3. **Look for duplicates** - Make sure no code is repeated
4. **Compare structure** - Use the overview above
5. **Test syntax** - Use `python -m py_compile bot.py`

---

**Assembly complete? Great! Proceed to DEPLOYMENT_GUIDE.md** 🚀
