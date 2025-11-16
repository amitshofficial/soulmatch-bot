# dating_bot.py
import os
import logging
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
)

# --- config ---
DB_PATH = os.getenv("DB_PATH", "dating_bot.db")
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN env var not set. Exiting.")
    exit(1)

# Conversation states
A_NAME, A_AGE, A_GENDER, A_BIO, A_PHOTO = range(5)

# --- logging ---
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("soulmatch")

# --- DB init ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            tg_id INTEGER UNIQUE,
            username TEXT,
            name TEXT,
            is_banned INTEGER DEFAULT 0
        )"""
        )
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY,
            user_id INTEGER UNIQUE,
            age INTEGER,
            gender TEXT,
            bio TEXT,
            photo_file_id TEXT,
            last_active DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
        )
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY,
            from_user INTEGER,
            to_user INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
        )
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY,
            a INTEGER,
            b INTEGER,
            active INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
        )
        await db.execute(
            """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY,
            reporter_id INTEGER,
            reported_id INTEGER,
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
        )
        await db.commit()
    logger.info("Database initialized at %s", DB_PATH)


# --- helpers ---
async def ensure_user(db_conn, tg_user):
    cur = await db_conn.execute("SELECT id FROM users WHERE tg_id = ?", (tg_user.id,))
    row = await cur.fetchone()
    if row:
        return row[0]
    # construct a readable name fallback
    first = getattr(tg_user, "first_name", "") or ""
    last = getattr(tg_user, "last_name", "") or ""
    full = f"{first} {last}".strip() or tg_user.username or ""
    res = await db_conn.execute(
        "INSERT INTO users (tg_id, username, name) VALUES (?, ?, ?)",
        (tg_user.id, tg_user.username, full),
    )
    await db_conn.commit()
    return res.lastrowid


# --- handlers ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to SoulMatch!\n\nCreate your profile with /create_profile and start finding matches ❤️\nCommands: /create_profile /find /myprofile /delete_account /report /help"
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/create_profile - Create or update your profile\n"
        "/find - Browse profiles\n"
        "/myprofile - View your profile\n"
        "/delete_account - Delete your account & profile\n"
        "/report <tg_id> <reason> - Report a user\n"
        "/help - Show commands"
    )


# Conversation handlers for profile creation
async def create_profile_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! What's your full name?")
    return A_NAME


async def profile_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Please enter a valid name (at least 2 characters).")
        return A_NAME
    ctx.user_data["name"] = name
    await update.message.reply_text("How old are you?")
    return A_AGE


async def profile_age(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("Please send a number for age.")
        return A_AGE
    age = int(text)
    if age < 18:
        await update.message.reply_text("You must be 18+ to use this bot.")
        return ConversationHandler.END
    ctx.user_data["age"] = age
    await update.message.reply_text("What's your gender? (Male/Female/Other)")
    return A_GENDER


async def profile_gender(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["gender"] = update.message.text.strip()
    await update.message.reply_text("Write a short bio about yourself (1-2 lines):")
    return A_BIO


async def profile_bio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["bio"] = update.message.text.strip()
    await update.message.reply_text("Send a profile photo or /skip to continue without one.")
    return A_PHOTO


async def profile_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file_id = photo.file_id
    ctx.user_data["photo"] = file_id

    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await ensure_user(db, update.effective_user)
        # update user's name in users table
        try:
            await db.execute("UPDATE users SET name = ? WHERE id = ?", (ctx.user_data.get("name"), user_id))
        except Exception:
            pass
        await db.execute(
            """
            INSERT OR REPLACE INTO profiles (user_id, age, gender, bio, photo_file_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                ctx.user_data.get("age"),
                ctx.user_data.get("gender"),
                ctx.user_data.get("bio"),
                ctx.user_data.get("photo"),
            ),
        )
        await db.commit()

    await update.message.reply_text("Profile saved! Use /find to browse others.")
    ctx.user_data.clear()
    return ConversationHandler.END


async def skip_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await ensure_user(db, update.effective_user)
        try:
            await db.execute("UPDATE users SET name = ? WHERE id = ?", (ctx.user_data.get("name"), user_id))
        except Exception:
            pass
        await db.execute(
            """
            INSERT OR REPLACE INTO profiles (user_id, age, gender, bio, photo_file_id)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (user_id, ctx.user_data.get("age"), ctx.user_data.get("gender"), ctx.user_data.get("bio")),
        )
        await db.commit()

    await update.message.reply_text("Profile saved without photo! Use /find.")
    ctx.user_data.clear()
    return ConversationHandler.END


async def myprofile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        cur_user = await db.execute("SELECT id, name, username FROM users WHERE tg_id = ?", (update.effective_user.id,))
        urow = await cur_user.fetchone()
        if not urow:
            await update.message.reply_text("No account found. Create one with /create_profile")
            return
        user_id, name, username = urow
        cur = await db.execute("SELECT age, gender, bio, photo_file_id FROM profiles WHERE user_id = ?", (user_id,))
        prow = await cur.fetchone()
        if not prow:
            await update.message.reply_text("You don't have a profile yet. Create with /create_profile")
            return
        age, gender, bio, photo_id = prow
        text = f"Name: {name}\nTelegram: @{username or 'user'}\nAge: {age}\nGender: {gender}\nBio: {bio}"
        if photo_id:
            await update.message.reply_photo(photo_id, caption=text)
        else:
            await update.message.reply_text(text)


async def delete_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM users WHERE tg_id = ?", (update.effective_user.id,))
        row = await cur.fetchone()
        if not row:
            await update.message.reply_text("No account found.")
            return
        user_id = row[0]
        await db.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM likes WHERE from_user = ? OR to_user = ?", (user_id, user_id))
        await db.execute("DELETE FROM matches WHERE a = ? OR b = ?", (user_id, user_id))
        await db.execute("DELETE FROM reports WHERE reporter_id = ? OR reported_id = ?", (user_id, user_id))
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
    await update.message.reply_text("Your account and data have been deleted.")


# Find handler (embed candidate id into callback_data)
async def find_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        user_id = await ensure_user(db, update.effective_user)
        cur = await db.execute(
            """
            SELECT p.user_id, p.age, p.gender, p.bio, p.photo_file_id, u.username, u.name
            FROM profiles p 
            JOIN users u ON p.user_id = u.id
            WHERE p.user_id != ?
            AND p.user_id NOT IN (SELECT to_user FROM likes WHERE from_user = ?)
            LIMIT 1
            """,
            (user_id, user_id),
        )
        row = await cur.fetchone()

        if not row:
            await update.message.reply_text("No profiles available right now. Try again later.")
            return

        to_user_id, age, gender, bio, photo_id, username, name = row

        text = f"{name} (@{username or 'user'})\nAge: {age}\nGender: {gender}\nBio: {bio}"
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("❤️ Like", callback_data=f"like:{to_user_id}"),
                    InlineKeyboardButton("⏭ Skip", callback_data=f"skip:{to_user_id}"),
                ]
            ]
        )

        if photo_id:
            await update.message.reply_photo(photo_id, caption=text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text, reply_markup=keyboard)


# Callback handler - parse callback_data like "like:123" or "skip:123"
async def callback_query_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user = update.effective_user

    try:
        action, target_str = data.split(":", 1)
        target_id = int(target_str)
    except Exception:
        await query.edit_message_text("Sorry, action not recognized. Please try /find again.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        from_user = await ensure_user(db, user)

        if action == "skip":
            await query.edit_message_text("Skipped! Use /find to see other profiles.")
            return

        if action == "like":
            cur_check = await db.execute("SELECT 1 FROM likes WHERE from_user = ? AND to_user = ?", (from_user, target_id))
            already_like = await cur_check.fetchone()
            if not already_like:
                await db.execute("INSERT INTO likes (from_user, to_user) VALUES (?, ?)", (from_user, target_id))
                await db.commit()

            cur = await db.execute("SELECT 1 FROM likes WHERE from_user = ? AND to_user = ?", (target_id, from_user))
            mutual = await cur.fetchone()

            if mutual:
                cur2 = await db.execute(
                    "SELECT 1 FROM matches WHERE (a = ? AND b = ?) OR (a = ? AND b = ?)",
                    (from_user, target_id, target_id, from_user),
                )
                already = await cur2.fetchone()
                if not already:
                    await db.execute("INSERT INTO matches (a,b) VALUES (?,?)", (from_user, target_id))
                    await db.commit()
                await query.edit_message_text("🎉 It's a MATCH! You can now chat anonymously via the bot.")
                # notify the other user
                cur3 = await db.execute("SELECT tg_id FROM users WHERE id = ?", (target_id,))
                row = await cur3.fetchone()
                if row:
                    try:
                        await ctx.bot.send_message(row[0], "You've got a new match! Start chatting via the bot.")
                    except Exception as e:
                        logger.warning("Could not notify matched user: %s", e)
            else:
                await query.edit_message_text("Liked! Waiting for a mutual like.")
            return

        await query.edit_message_text("Unknown action. Use /find to try again.")


# Relay messages between matched users (simple relay)
async def relay_messages(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if update.message.text is None:
        await update.message.reply_text("Only text messages are relayed in this prototype.")
        return

    sender_tg = update.effective_user
    async with aiosqlite.connect(DB_PATH) as db:
        sender_id = await ensure_user(db, sender_tg)
        cur = await db.execute("SELECT a,b FROM matches WHERE (a = ? OR b = ?) AND active = 1", (sender_id, sender_id))
        match = await cur.fetchone()
        if not match:
            await update.message.reply_text("You don't have an active match. Use /find to match with someone.")
            return
        a, b = match
        other = b if a == sender_id else a
        cur2 = await db.execute("SELECT tg_id FROM users WHERE id = ?", (other,))
        row = await cur2.fetchone()
        if not row:
            await update.message.reply_text("Could not find your match's contact.")
            return
        other_tg = row[0]
        sender_label = f"Anonymous#{sender_id}"
        try:
            await ctx.bot.send_message(chat_id=other_tg, text=f"{sender_label}:\n{update.message.text}")
            await update.message.reply_text("Sent to your match.")
        except Exception as e:
            logger.exception("Relay failed: %s", e)
            await update.message.reply_text("Failed to send message. The other user might have blocked the bot or hasn't started it.")


# Simple report command
async def report_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /report <tg_id> <reason>")
        return
    try:
        target_tg = int(args[0])
    except ValueError:
        await update.message.reply_text("Provide a numeric Telegram ID.")
        return
    reason = " ".join(args[1:])
    async with aiosqlite.connect(DB_PATH) as db:
        reporter_id = await ensure_user(db, update.effective_user)
        cur = await db.execute("SELECT id FROM users WHERE tg_id = ?", (target_tg,))
        row = await cur.fetchone()
        if not row:
            await update.message.reply_text("User not found.")
            return
        reported_id = row[0]
        await db.execute("INSERT INTO reports (reporter_id, reported_id, reason) VALUES (?, ?, ?)", (reporter_id, reported_id, reason))
        await db.commit()
    await update.message.reply_text("Report received. Admin will review it.")


# --- setup and run (synchronous) ---
def build_and_run():
    import asyncio as _asyncio

    # initialize DB
    _asyncio.get_event_loop().run_until_complete(init_db())

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("create_profile", create_profile_start)],
        states={
            A_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_name)],
            A_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_age)],
            A_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_gender)],
            A_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_bio)],
            A_PHOTO: [MessageHandler(filters.PHOTO, profile_photo), CommandHandler("skip", skip_photo)],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(conv)
    app.add_handler(CommandHandler("find", find_handler))
    app.add_handler(CommandHandler("myprofile", myprofile))
    app.add_handler(CommandHandler("delete_account", delete_account))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(CommandHandler("report", report_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), relay_messages))

    logger.info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    build_and_run()
