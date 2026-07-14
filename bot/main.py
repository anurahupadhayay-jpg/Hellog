"""
SaaS Telegram Bot - YouTube Uploader
Main entry point with all Pyrogram handlers.

This bot allows users to upload Google Drive videos to their own YouTube channels
with monetization, queue management, and admin controls.
"""

import os
import logging
import asyncio
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from pyrogram.enums import ParseMode

from bot.config import (
    API_ID, API_HASH, BOT_TOKEN, MAIN_ADMIN_ID,
    validate_config, THUMBNAILS_DIR
)
from bot.database import db
from bot.oauth_handler import oauth_handler
from bot.drive_downloader import downloader
from bot.youtube_uploader import uploader, VIDEO_CATEGORIES
from bot.upload_queue import upload_queue
from bot.monetization import monetization
from bot.admin import admin_manager

# ── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("data/bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── Pyrogram Client ──────────────────────────────────────────────────────────
app = Client(
    "youtube_uploader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.MARKDOWN
)

# ── User State Management ────────────────────────────────────────────────────
# Store temporary user states during multi-step conversations
user_states: dict = {}
# Store pending upload metadata
pending_uploads: dict = {}
# Store progress messages for updates
progress_messages: dict = {}


class UserState:
    """User conversation states."""
    IDLE = "idle"
    WAITING_AUTH_CODE = "waiting_auth_code"
    WAITING_TITLE = "waiting_title"
    WAITING_DESCRIPTION = "waiting_description"
    WAITING_TAGS = "waiting_tags"
    WAITING_PRIVACY = "waiting_privacy"
    WAITING_CATEGORY = "waiting_category"
    WAITING_THUMBNAIL = "waiting_thumbnail"
    WAITING_CONFIRM = "waiting_confirm"


# ── Helper Functions ─────────────────────────────────────────────────────────

def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Get main menu keyboard."""
    buttons = [
        [InlineKeyboardButton("📤 Upload Video", callback_data="menu_upload")],
        [InlineKeyboardButton("💰 Balance & Recharge", callback_data="menu_balance")],
        [InlineKeyboardButton("📊 My Stats", callback_data="menu_stats")],
        [InlineKeyboardButton("❓ Help", callback_data="menu_help")]
    ]

    # Add admin button if applicable
    if admin_manager.is_admin(user_id):
        buttons.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="menu_admin")])

    return InlineKeyboardMarkup(buttons)


def get_privacy_keyboard() -> InlineKeyboardMarkup:
    """Get privacy status selection keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 Public", callback_data="privacy_public"),
         InlineKeyboardButton("🔒 Private", callback_data="privacy_private")],
        [InlineKeyboardButton("🔗 Unlisted", callback_data="privacy_unlisted")],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_to_tags")]
    ])


def get_category_keyboard() -> InlineKeyboardMarkup:
    """Get category selection keyboard."""
    buttons = []
    row = []
    for cat_id, cat_name in list(VIDEO_CATEGORIES.items())[:20]:
        row.append(InlineKeyboardButton(cat_name, callback_data=f"cat_{cat_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_privacy")])
    return InlineKeyboardMarkup(buttons)


def get_confirm_keyboard() -> InlineKeyboardMarkup:
    """Get confirmation keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Upload", callback_data="confirm_upload")],
        [InlineKeyboardButton("🔄 Start Over", callback_data="menu_upload")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_upload")]
    ])


def get_admin_keyboard() -> InlineKeyboardMarkup:
    """Get admin panel keyboard."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👤 User Lookup", callback_data="admin_user_lookup")],
        [InlineKeyboardButton("📋 Queue Status", callback_data="admin_queue")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="menu_main")]
    ])


# ── Start & Help Commands ───────────────────────────────────────────────────

@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    """Handle /start command - register user and show welcome."""
    user = message.from_user
    user_id = user.id

    # Register user in database
    db.add_user(user_id, user.username, user.first_name, user.last_name)

    # Initialize user state
    user_states[user_id] = {"state": UserState.IDLE}

    welcome_text = (
        f"👋 **Welcome, {user.first_name}!**\n\n"
        f"I'm a **YouTube Uploader Bot**. I can help you upload videos "
        f"from **Google Drive** directly to **your own YouTube channel**.\n\n"
        f"🎁 **Free Trial:** You get {monetization.free_minutes} minutes free!\n\n"
        f"💰 **Pricing:** ₹{monetization.cost_per_hour} per hour of usage\n\n"
        f"📋 **How it works:**\n"
        f"1. Login with your Google account\n"
        f"2. Send me a Google Drive video link\n"
        f"3. Set title, description, tags, privacy\n"
        f"4. I upload it to YOUR YouTube channel!\n\n"
        f"Use the buttons below to get started!"
    )

    await message.reply_text(
        welcome_text,
        reply_markup=get_main_keyboard(user_id)
    )


@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    """Handle /help command."""
    help_text = (
        "📖 **Bot Help**\n\n"
        "**Getting Started:**\n"
        "  `/start` - Start the bot\n"
        "  `/login` - Login with Google (required)\n"
        "  `/logout` - Remove Google authentication\n\n"
        "**Uploading:**\n"
        "  Send a Google Drive link to start upload\n"
        "  `/upload` - Start upload wizard\n\n"
        "**Account:**\n"
        "  `/balance` - Check time balance\n"
        "  `/recharge` - Recharge your account\n"
        "  `/history` - View upload history\n"
        "  `/stats` - Your statistics\n\n"
        "**Support:**\n"
        "  `/support` - Contact support\n\n"
        "💡 **Tip:** Make sure your Google Drive link is publicly accessible!"
    )
    await message.reply_text(help_text)


# ── Authentication Commands ─────────────────────────────────────────────────

@app.on_message(filters.command("login"))
async def login_handler(client: Client, message: Message):
    """Handle /login - generate OAuth URL."""
    user_id = message.from_user.id

    # Check if already authenticated
    if db.has_oauth_token(user_id):
        await message.reply_text(
            "✅ **Already logged in!**\n\n"
            "Your YouTube account is connected.\n"
            "Use /logout if you want to switch accounts.",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    try:
        auth_url = oauth_handler.get_authorization_url(user_id)

        user_states[user_id] = {
            "state": UserState.WAITING_AUTH_CODE,
            "auth_url": auth_url
        }

        login_text = (
            "🔐 **YouTube Login Required**\n\n"
            "To upload videos to your YouTube channel, "
            "you need to authorize this bot.\n\n"
            "**Steps:**\n"
            "1. Click the button below to open the auth page\n"
            "2. Sign in with your Google account\n"
            "3. Copy the authorization code\n"
            "4. Send the code back to me\n\n"
            "⚠️ **Note:** The bot can only upload to channels "
            "you own and have granted access to."
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔑 Open Auth Page", url=auth_url)],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_main")]
        ])

        await message.reply_text(login_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Login error for user {user_id}: {e}")
        await message.reply_text(
            "❌ **Login Error**\n\n"
            f"Failed to generate auth URL: {str(e)}\n"
            "Please try again later or contact support."
        )


@app.on_message(filters.command("logout"))
async def logout_handler(client: Client, message: Message):
    """Handle /logout - revoke credentials."""
    user_id = message.from_user.id

    if not db.has_oauth_token(user_id):
        await message.reply_text(
            "ℹ️ **Not logged in**\n\n"
            "You don't have any connected YouTube account.",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    success = await oauth_handler.revoke_credentials(user_id)
    if success:
        await message.reply_text(
            "✅ **Logged out successfully!**\n\n"
            "Your YouTube account has been disconnected.\n"
            "Use /login to connect a new account.",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await message.reply_text(
            "❌ **Logout failed**\n\n"
            "Please try again or contact support."
        )


# ── Auth Code Handler ───────────────────────────────────────────────────────

@app.on_message(filters.text & filters.private)
async def text_handler(client: Client, message: Message):
    """Handle text messages based on user state."""
    user_id = message.from_user.id
    text = message.text.strip()

    # Get user state
    state_info = user_states.get(user_id, {})
    current_state = state_info.get("state", UserState.IDLE)

    # ── Auth Code ──────────────────────────────────────────────────
    if current_state == UserState.WAITING_AUTH_CODE:
        # Treat as auth code
        auth_code = text
        status_msg = await message.reply_text("🔄 **Authenticating...** Please wait.")

        success = await oauth_handler.exchange_code(user_id, auth_code)

        await status_msg.delete()

        if success:
            token_info = db.get_oauth_token(user_id)
            channel_info = token_info.get("email", "Unknown") if token_info else "Unknown"

            await message.reply_text(
                "✅ **Authentication Successful!**\n\n"
                f"📺 **Channel:** {channel_info}\n\n"
                "You can now upload videos to your YouTube channel!\n"
                "Send me a Google Drive video link to get started.",
                reply_markup=get_main_keyboard(user_id)
            )
            user_states[user_id] = {"state": UserState.IDLE}
        else:
            await message.reply_text(
                "❌ **Authentication Failed**\n\n"
                "The code you provided is invalid or expired.\n"
                "Please use /login to try again.",
                reply_markup=get_main_keyboard(user_id)
            )
        return

    # ── Title Input ────────────────────────────────────────────────
    if current_state == UserState.WAITING_TITLE:
        pending_uploads[user_id]["title"] = text
        user_states[user_id] = {"state": UserState.WAITING_DESCRIPTION}

        await message.reply_text(
            "📝 **Step 2/6: Description**\n\n"
            "Enter a description for your video:\n"
            "(or send /skip to skip)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Skip", callback_data="skip_description")]
            ])
        )
        return

    # ── Description Input ──────────────────────────────────────────
    if current_state == UserState.WAITING_DESCRIPTION:
        pending_uploads[user_id]["description"] = text
        user_states[user_id] = {"state": UserState.WAITING_TAGS}

        await message.reply_text(
            "🏷 **Step 3/6: Tags**\n\n"
            "Enter tags separated by commas:\n"
            "Example: `vlog, travel, india`\n\n"
            "(or send /skip to skip)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Skip", callback_data="skip_tags")]
            ])
        )
        return

    # ── Tags Input ─────────────────────────────────────────────────
    if current_state == UserState.WAITING_TAGS:
        pending_uploads[user_id]["tags"] = text
        user_states[user_id] = {"state": UserState.WAITING_PRIVACY}

        await message.reply_text(
            "🔒 **Step 4/6: Privacy Setting**\n\n"
            "Choose who can see your video:",
            reply_markup=get_privacy_keyboard()
        )
        return

    # ── Category Input ─────────────────────────────────────────────
    if current_state == UserState.WAITING_CATEGORY:
        # Check if it's a valid category ID or name
        cat_id = None
        if text in VIDEO_CATEGORIES:
            cat_id = text
        else:
            # Try to find by name
            for cid, cname in VIDEO_CATEGORIES.items():
                if text.lower() in cname.lower():
                    cat_id = cid
                    break

        if not cat_id:
            await message.reply_text(
                "❌ Invalid category. Please select from the list above "
                "or enter a valid category ID.",
                reply_markup=get_category_keyboard()
            )
            return

        pending_uploads[user_id]["category_id"] = cat_id
        user_states[user_id] = {"state": UserState.WAITING_THUMBNAIL}

        await message.reply_text(
            "🖼 **Step 6/6: Thumbnail**\n\n"
            "Send a thumbnail image for your video, "
            "or click Skip to use YouTube's auto-generated thumbnail.\n\n"
            "📐 **Recommended:** 1280x720 (16:9 ratio)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Skip", callback_data="skip_thumbnail")]
            ])
        )
        return

    # ── Google Drive Link (Idle State) ─────────────────────────────
    if current_state == UserState.IDLE:
        # Check if it looks like a Google Drive link
        if "drive.google.com" in text or "drive.google.com" in text:
            await handle_drive_link(client, message, text)
            return
        else:
            # Unknown message in idle state
            await message.reply_text(
                "ℹ️ Send me a **Google Drive video link** to upload it to YouTube, "
                "or use the buttons below.",
                reply_markup=get_main_keyboard(user_id)
            )
            return


# ── Google Drive Link Handler ───────────────────────────────────────────────

async def handle_drive_link(client: Client, message: Message, drive_link: str):
    """Process a Google Drive link from a user."""
    user_id = message.from_user.id

    # Check authentication
    if not db.has_oauth_token(user_id):
        await message.reply_text(
            "🔐 **Login Required**\n\n"
            "Please login with your Google account first.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔑 Login", callback_data="menu_login")]
            ])
        )
        return

    # Check balance
    remaining = monetization.get_remaining_minutes(user_id)
    if remaining <= 0:
        await message.reply_text(
            "⏰ **Time Balance Depleted**\n\n"
            "You don't have enough time balance to upload videos.\n"
            "Please recharge your account.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Recharge", callback_data="menu_recharge")]
            ])
        )
        return

    # Show warning if low balance
    warning = ""
    if monetization.should_warn(user_id):
        warning = monetization.get_warning_message(user_id) + "\n\n"

    # Start upload wizard
    pending_uploads[user_id] = {
        "drive_link": drive_link,
        "title": None,
        "description": None,
        "tags": None,
        "privacy_status": "private",
        "category_id": "22",
        "thumbnail_path": None
    }

    user_states[user_id] = {"state": UserState.WAITING_TITLE}

    await message.reply_text(
        f"{warning}"
        f"📹 **Google Drive Link Received!**\n\n"
        f"Let's set up your video upload.\n\n"
        f"📝 **Step 1/6: Title**\n\n"
        f"Enter a title for your video:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_upload")]
        ])
    )


# ── Photo Handler (Thumbnail) ───────────────────────────────────────────────

@app.on_message(filters.photo & filters.private)
async def photo_handler(client: Client, message: Message):
    """Handle thumbnail photo upload."""
    user_id = message.from_user.id
    state_info = user_states.get(user_id, {})

    if state_info.get("state") == UserState.WAITING_THUMBNAIL:
        # Download the photo
        thumb_dir = Path(THUMBNAILS_DIR) / str(user_id)
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = thumb_dir / f"thumb_{message.photo.file_id}.jpg"

        status_msg = await message.reply_text("🔄 **Downloading thumbnail...**")
        await message.download(file_name=str(thumb_path))
        await status_msg.delete()

        pending_uploads[user_id]["thumbnail_path"] = str(thumb_path)

        # Show confirmation
        await show_upload_summary(client, message, user_id)
    else:
        await message.reply_text(
            "ℹ️ I wasn't expecting a photo.\n"
            "Send me a Google Drive link to upload a video!"
        )


# ── Callback Query Handlers ─────────────────────────────────────────────────

@app.on_callback_query()
async def callback_handler(client: Client, callback: CallbackQuery):
    """Handle inline button callbacks."""
    user_id = callback.from_user.id
    data = callback.data

    await callback.answer()

    # ── Main Menu ──────────────────────────────────────────────────
    if data == "menu_main":
        user_states[user_id] = {"state": UserState.IDLE}
        await callback.message.edit_text(
            "🏠 **Main Menu**\n\nWhat would you like to do?",
            reply_markup=get_main_keyboard(user_id)
        )

    # ── Upload Menu ────────────────────────────────────────────────
    elif data == "menu_upload":
        await callback.message.edit_text(
            "📤 **Upload Video**\n\n"
            "Send me a **Google Drive video link** to get started!\n\n"
            "💡 **Tip:** Make sure the link is publicly accessible.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]
            ])
        )

    # ── Login ──────────────────────────────────────────────────────
    elif data == "menu_login":
        await login_handler(client, callback.message)

    # ── Balance ────────────────────────────────────────────────────
    elif data == "menu_balance":
        balance_msg = monetization.get_balance_message(user_id)
        await callback.message.edit_text(
            balance_msg,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Recharge", callback_data="menu_recharge")],
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]
            ])
        )

    # ── Recharge ───────────────────────────────────────────────────
    elif data == "menu_recharge":
        recharge_msg = monetization.get_recharge_options()
        await callback.message.edit_text(
            recharge_msg,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Contact Admin", url=f"tg://user?id={MAIN_ADMIN_ID}")],
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_balance")]
            ])
        )

    # ── Stats ──────────────────────────────────────────────────────
    elif data == "menu_stats":
        stats = monetization.get_user_stats(user_id)
        balance = stats["balance"]

        history_text = ""
        if stats["recent_history"]:
            history_text = "\n📋 **Recent Uploads:**\n"
            for h in stats["recent_history"][:5]:
                status_emoji = "✅" if h["status"] == "completed" else "❌" if h["status"] == "failed" else "⏳"
                history_text += f"  {status_emoji} {h.get('title', 'Untitled')[:30]}\n"

        await callback.message.edit_text(
            f"📊 **Your Statistics**\n\n"
            f"⏱ **Balance:** {balance['remaining_minutes']:.1f} minutes\n"
            f"📹 **Total Uploads:** {stats['total_uploads']}\n"
            f"✅ **Successful:** {stats['successful_uploads']}\n"
            f"❌ **Failed:** {stats['failed_uploads']}"
            f"{history_text}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Full History", callback_data="menu_history")],
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]
            ])
        )

    # ── History ────────────────────────────────────────────────────
    elif data == "menu_history":
        history = db.get_upload_history(user_id, limit=20)
        if not history:
            await callback.message.edit_text(
                "📋 **Upload History**\n\nNo uploads yet!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ Back", callback_data="menu_stats")]
                ])
            )
            return

        lines = ["📋 **Upload History**\n"]
        for h in history:
            status_emoji = {
                "completed": "✅",
                "failed": "❌",
                "pending": "⏳",
                "downloading": "⬇️",
                "uploading": "⬆️"
            }.get(h["status"], "❓")

            title = h.get("title", "Untitled")[:25]
            vid = h.get("youtube_video_id", "N/A")
            lines.append(f"{status_emoji} **{title}**")
            if vid and vid != "N/A":
                lines.append(f"   🆔 `{vid}`")
            lines.append("")

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_stats")]
            ])
        )

    # ── Help ───────────────────────────────────────────────────────
    elif data == "menu_help":
        await callback.message.edit_text(
            "📖 **Help**\n\n"
            "**How to upload a video:**\n"
            "1. Use `/login` to connect your YouTube\n"
            "2. Send a Google Drive video link\n"
            "3. Fill in video details (title, desc, etc.)\n"
            "4. Confirm and wait for upload!\n\n"
            "**Commands:**\n"
            "  `/start` - Start the bot\n"
            "  `/login` - Connect YouTube\n"
            "  `/logout` - Disconnect YouTube\n"
            "  `/balance` - Check time balance\n"
            "  `/recharge` - Recharge account\n"
            "  `/history` - Upload history\n"
            "  `/stats` - Your statistics\n"
            "  `/support` - Contact support\n\n"
            "**Need more help?** Contact @admin",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]
            ])
        )

    # ── Privacy Selection ──────────────────────────────────────────
    elif data.startswith("privacy_"):
        privacy = data.replace("privacy_", "")
        pending_uploads[user_id]["privacy_status"] = privacy
        user_states[user_id] = {"state": UserState.WAITING_CATEGORY}

        privacy_names = {"public": "🌍 Public", "private": "🔒 Private", "unlisted": "🔗 Unlisted"}

        await callback.message.edit_text(
            f"🔒 **Privacy:** {privacy_names.get(privacy, privacy)}\n\n"
            f"📂 **Step 5/6: Category**\n\n"
            f"Select a category for your video:",
            reply_markup=get_category_keyboard()
        )

    # ── Category Selection ─────────────────────────────────────────
    elif data.startswith("cat_"):
        cat_id = data.replace("cat_", "")
        pending_uploads[user_id]["category_id"] = cat_id
        user_states[user_id] = {"state": UserState.WAITING_THUMBNAIL}

        cat_name = VIDEO_CATEGORIES.get(cat_id, "Unknown")

        await callback.message.edit_text(
            f"📂 **Category:** {cat_name}\n\n"
            f"🖼 **Step 6/6: Thumbnail**\n\n"
            f"Send a thumbnail image, or click Skip:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Skip", callback_data="skip_thumbnail")]
            ])
        )

    # ── Skip Buttons ───────────────────────────────────────────────
    elif data == "skip_description":
        pending_uploads[user_id]["description"] = ""
        user_states[user_id] = {"state": UserState.WAITING_TAGS}
        await callback.message.edit_text(
            "🏷 **Step 3/6: Tags**\n\n"
            "Enter tags separated by commas:\n"
            "Example: `vlog, travel, india`\n\n"
            "(or send /skip to skip)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Skip", callback_data="skip_tags")]
            ])
        )

    elif data == "skip_tags":
        pending_uploads[user_id]["tags"] = ""
        user_states[user_id] = {"state": UserState.WAITING_PRIVACY}
        await callback.message.edit_text(
            "🔒 **Step 4/6: Privacy Setting**\n\n"
            "Choose who can see your video:",
            reply_markup=get_privacy_keyboard()
        )

    elif data == "skip_thumbnail":
        pending_uploads[user_id]["thumbnail_path"] = None
        await show_upload_summary(client, callback.message, user_id, edit=True)

    # ── Back Buttons ───────────────────────────────────────────────
    elif data == "back_to_tags":
        user_states[user_id] = {"state": UserState.WAITING_TAGS}
        await callback.message.edit_text(
            "🏷 **Step 3/6: Tags**\n\n"
            "Enter tags separated by commas:\n"
            "Example: `vlog, travel, india`\n\n"
            "(or send /skip to skip)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ Skip", callback_data="skip_tags")]
            ])
        )

    elif data == "back_to_privacy":
        user_states[user_id] = {"state": UserState.WAITING_PRIVACY}
        await callback.message.edit_text(
            "🔒 **Step 4/6: Privacy Setting**\n\n"
            "Choose who can see your video:",
            reply_markup=get_privacy_keyboard()
        )

    # ── Cancel Upload ──────────────────────────────────────────────
    elif data == "cancel_upload":
        user_states[user_id] = {"state": UserState.IDLE}
        pending_uploads.pop(user_id, None)
        await callback.message.edit_text(
            "❌ **Upload cancelled.**\n\n"
            "Send a new Google Drive link to start over.",
            reply_markup=get_main_keyboard(user_id)
        )

    # ── Confirm Upload ─────────────────────────────────────────────
    elif data == "confirm_upload":
        await process_upload_confirmation(client, callback)

    # ── Admin Panel ────────────────────────────────────────────────
    elif data == "menu_admin":
        if not admin_manager.is_admin(user_id):
            await callback.answer("Unauthorized!", show_alert=True)
            return
        await callback.message.edit_text(
            "🔐 **Admin Panel**\n\n"
            "Select an option:",
            reply_markup=get_admin_keyboard()
        )

    # ── Admin Stats ────────────────────────────────────────────────
    elif data == "admin_stats":
        if not admin_manager.is_admin(user_id):
            return
        stats_msg = admin_manager.get_stats_message()
        await callback.message.edit_text(
            stats_msg,
            reply_markup=get_admin_keyboard()
        )

    # ── Admin Broadcast ────────────────────────────────────────────
    elif data == "admin_broadcast":
        if not admin_manager.is_admin(user_id):
            return
        await callback.message.edit_text(
            "📢 **Broadcast**\n\n"
            "Reply to any message with `/broadcast` to send it to all users.\n\n"
            "Or use `/broadcast_text [your message]` to send text directly.",
            reply_markup=get_admin_keyboard()
        )

    # ── Admin User Lookup ──────────────────────────────────────────
    elif data == "admin_user_lookup":
        if not admin_manager.is_admin(user_id):
            return
        await callback.message.edit_text(
            "👤 **User Lookup**\n\n"
            "Send `/user_info [user_id]` to get user details.",
            reply_markup=get_admin_keyboard()
        )

    # ── Admin Queue ────────────────────────────────────────────────
    elif data == "admin_queue":
        if not admin_manager.is_admin(user_id):
            return
        queue_len = upload_queue.get_queue_length()
        current = upload_queue.get_current_job()

        current_text = "None"
        if current:
            current_text = f"User {current['user_id']} - {current.get('title', 'N/A')[:30]}"

        await callback.message.edit_text(
            f"📋 **Queue Status**\n\n"
            f"📊 **Queue Length:** {queue_len}\n"
            f"⏳ **Currently Processing:** {current_text}\n"
            f"🔄 **Processing:** {'Yes' if upload_queue.is_processing else 'No'}",
            reply_markup=get_admin_keyboard()
        )


# ── Upload Summary & Confirmation ───────────────────────────────────────────

async def show_upload_summary(client: Client, message, user_id: int, edit: bool = False):
    """Show upload summary and ask for confirmation."""
    upload = pending_uploads.get(user_id, {})

    # Generate summary
    cat_name = VIDEO_CATEGORIES.get(upload.get("category_id", "22"), "Unknown")
    privacy_emoji = {
        "public": "🌍",
        "private": "🔒",
        "unlisted": "🔗"
    }.get(upload.get("privacy_status", "private"), "🔒")

    summary = (
        f"📋 **Upload Summary**\n\n"
        f"🔗 **Drive Link:** {upload.get('drive_link', 'N/A')[:50]}...\n"
        f"📝 **Title:** {upload.get('title', 'N/A')}\n"
        f"📄 **Description:** {upload.get('description', 'None')[:50]}...\n"
        f"🏷 **Tags:** {upload.get('tags', 'None') or 'None'}\n"
        f"{privacy_emoji} **Privacy:** {upload.get('privacy_status', 'private').title()}\n"
        f"📂 **Category:** {cat_name}\n"
        f"🖼 **Thumbnail:** {'Custom' if upload.get('thumbnail_path') else 'Auto'}\n\n"
        f"✅ Confirm to start upload?"
    )

    user_states[user_id] = {"state": UserState.WAITING_CONFIRM}

    if edit:
        await message.edit_text(summary, reply_markup=get_confirm_keyboard())
    else:
        await message.reply_text(summary, reply_markup=get_confirm_keyboard())


async def process_upload_confirmation(client: Client, callback: CallbackQuery):
    """Process the upload after user confirmation."""
    user_id = callback.from_user.id
    upload = pending_uploads.get(user_id)

    if not upload:
        await callback.message.edit_text(
            "❌ **Upload data expired.**\nPlease start over.",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    # Check balance one more time
    remaining = monetization.get_remaining_minutes(user_id)
    if remaining <= 0:
        await callback.message.edit_text(
            "⏰ **Insufficient Balance**\n\n"
            "You don't have enough time to upload.\n"
            "Please recharge your account.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💰 Recharge", callback_data="menu_recharge")]
            ])
        )
        return

    # Show queue status
    queue_len = upload_queue.get_queue_length()
    queue_text = f"\n📊 **Queue Position:** {queue_len + 1}" if queue_len > 0 else ""

    status_message = await callback.message.edit_text(
        f"📤 **Upload Queued**{queue_text}\n\n"
        f"🎬 **Title:** {upload.get('title')}\n"
        f"⏳ **Status:** Waiting in queue...\n\n"
        f"I'll update you on the progress!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_upload")]
        ])
    )

    # Store the progress message for updates
    progress_messages[user_id] = status_message

    # Define progress callback
    async def on_progress(update: dict):
        """Handle progress updates from the queue."""
        try:
            stage = update.get("stage", "")
            msg_text = update.get("message", "")

            if stage in ("downloading", "uploading"):
                await status_message.edit_text(
                    msg_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_upload")]
                    ])
                )
            elif stage == "completed":
                await status_message.edit_text(
                    msg_text,
                    reply_markup=get_main_keyboard(user_id)
                )
            elif stage == "failed":
                await status_message.edit_text(
                    msg_text,
                    reply_markup=get_main_keyboard(user_id)
                )
        except Exception as e:
            logger.error(f"Progress update error: {e}")

    # Add to queue
    queue_id = await upload_queue.add_job(
        user_id=user_id,
        drive_link=upload["drive_link"],
        title=upload["title"],
        description=upload.get("description"),
        tags=upload.get("tags"),
        privacy_status=upload.get("privacy_status", "private"),
        category_id=upload.get("category_id", "22"),
        thumbnail_path=upload.get("thumbnail_path"),
        progress_callback=on_progress
    )

    if queue_id < 0:
        await status_message.edit_text(
            "❌ **Failed to queue upload.**\n"
            "Please check your balance and try again.",
            reply_markup=get_main_keyboard(user_id)
        )

    # Clear pending upload
    user_states[user_id] = {"state": UserState.IDLE}
    pending_uploads.pop(user_id, None)


# ── Admin Commands ──────────────────────────────────────────────────────────

@app.on_message(filters.command("admin_login"))
async def admin_login_handler(client: Client, message: Message):
    """Handle /admin_login [username] [password] - Emergency admin access."""
    user_id = message.from_user.id
    args = message.text.split()

    if len(args) < 3:
        await message.reply_text(
            "🔐 **Admin Login**\n\n"
            "Usage: `/admin_login [username] [password]`\n\n"
            "⚠️ This command is for emergency admin access only."
        )
        return

    username = args[1]
    password = " ".join(args[2:])  # Password might contain spaces

    if admin_manager.authenticate(username, password):
        # Create temporary admin session
        success = admin_manager.create_temporary_session(
            admin_user_id=user_id,
            granted_by_user_id=MAIN_ADMIN_ID,
            duration_hours=24
        )

        if success:
            await message.reply_text(
                "✅ **Admin Access Granted!**\n\n"
                "You now have temporary admin rights for 24 hours.\n"
                "Use /admin for the admin panel.",
                reply_markup=get_admin_keyboard()
            )
            # Delete the message containing credentials for security
            try:
                await message.delete()
            except Exception:
                pass
        else:
            await message.reply_text("❌ Failed to create admin session.")
    else:
        await message.reply_text(
            "❌ **Invalid Credentials**\n\n"
            "Access denied. This incident has been logged."
        )
        logger.warning(f"Failed admin login attempt by user {user_id}")


@app.on_message(filters.command("admin_logout"))
async def admin_logout_handler(client: Client, message: Message):
    """Handle /admin_logout - Revoke temporary admin access."""
    user_id = message.from_user.id

    if not admin_manager.is_admin(user_id):
        await message.reply_text("You are not an admin.")
        return

    # Only revoke temporary sessions, not main admin
    if user_id != MAIN_ADMIN_ID:
        admin_manager.revoke_session(user_id)
        await message.reply_text(
            "✅ **Admin access revoked.**\n\n"
            "You are no longer an admin.",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await message.reply_text("Main admin cannot revoke their own access.")


@app.on_message(filters.command("broadcast"))
async def broadcast_handler(client: Client, message: Message):
    """Handle /broadcast - Send replied message to all users."""
    user_id = message.from_user.id

    if not admin_manager.is_admin(user_id):
        await message.reply_text("⛔ Unauthorized.")
        return

    # Check if this is a reply to another message
    if not message.reply_to_message:
        await message.reply_text(
            "📢 **Broadcast**\n\n"
            "Reply to a message with `/broadcast` to send it to all users."
        )
        return

    target_message = message.reply_to_message

    status_msg = await message.reply_text("📢 **Broadcasting...** Please wait.")

    # Extract text or caption
    broadcast_text = target_message.text or target_message.caption or ""
    reply_markup = target_message.reply_markup

    result = await admin_manager.broadcast_message(
        client, broadcast_text, user_id, reply_markup
    )

    await status_msg.edit_text(
        f"✅ **Broadcast Complete!**\n\n"
        f"📤 **Sent:** {result.get('sent', 0)}\n"
        f"❌ **Failed:** {result.get('failed', 0)}"
    )


@app.on_message(filters.command("broadcast_text"))
async def broadcast_text_handler(client: Client, message: Message):
    """Handle /broadcast_text [message] - Send text to all users."""
    user_id = message.from_user.id

    if not admin_manager.is_admin(user_id):
        await message.reply_text("⛔ Unauthorized.")
        return

    args = message.text.split(" ", 1)
    if len(args) < 2:
        await message.reply_text("Usage: `/broadcast_text [your message]`")
        return

    broadcast_text = args[1]

    status_msg = await message.reply_text("📢 **Broadcasting...** Please wait.")

    result = await admin_manager.broadcast_message(
        client, broadcast_text, user_id
    )

    await status_msg.edit_text(
        f"✅ **Broadcast Complete!**\n\n"
        f"📤 **Sent:** {result.get('sent', 0)}\n"
        f"❌ **Failed:** {result.get('failed', 0)}"
    )


@app.on_message(filters.command("user_info"))
async def user_info_handler(client: Client, message: Message):
    """Handle /user_info [user_id] - Get user details."""
    user_id = message.from_user.id

    if not admin_manager.is_admin(user_id):
        await message.reply_text("⛔ Unauthorized.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("Usage: `/user_info [user_id]`")
        return

    try:
        target_user_id = int(args[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID.")
        return

    user_info = admin_manager.get_user_info(target_user_id)
    if not user_info:
        await message.reply_text("❌ User not found.")
        return

    user = user_info["user"]
    balance = user_info["balance"]

    await message.reply_text(
        f"👤 **User Information**\n\n"
        f"🆔 **ID:** `{user['user_id']}`\n"
        f"👤 **Username:** @{user.get('username', 'N/A')}\n"
        f"📝 **Name:** {user.get('first_name', '')} {user.get('last_name', '')}\n"
        f"🚫 **Banned:** {'Yes' if user.get('is_banned') else 'No'}\n"
        f"🔐 **Admin:** {'Yes' if user.get('is_admin') else 'No'}\n\n"
        f"⏱ **Balance:** {balance['remaining_minutes']:.1f} minutes\n"
        f"📊 **Total:** {balance['total_minutes']:.1f} minutes\n"
        f"📈 **Used:** {balance['used_minutes']:.1f} minutes"
    )


@app.on_message(filters.command("ban"))
async def ban_handler(client: Client, message: Message):
    """Handle /ban [user_id] - Ban a user."""
    user_id = message.from_user.id

    if not admin_manager.is_admin(user_id):
        await message.reply_text("⛔ Unauthorized.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("Usage: `/ban [user_id]`")
        return

    try:
        target_user_id = int(args[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID.")
        return

    success = admin_manager.ban_user(target_user_id)
    if success:
        await message.reply_text(f"🚫 **User {target_user_id} has been banned.**")
    else:
        await message.reply_text("❌ Failed to ban user.")


@app.on_message(filters.command("unban"))
async def unban_handler(client: Client, message: Message):
    """Handle /unban [user_id] - Unban a user."""
    user_id = message.from_user.id

    if not admin_manager.is_admin(user_id):
        await message.reply_text("⛔ Unauthorized.")
        return

    args = message.text.split()
    if len(args) < 2:
        await message.reply_text("Usage: `/unban [user_id]`")
        return

    try:
        target_user_id = int(args[1])
    except ValueError:
        await message.reply_text("❌ Invalid user ID.")
        return

    success = admin_manager.unban_user(target_user_id)
    if success:
        await message.reply_text(f"✅ **User {target_user_id} has been unbanned.**")
    else:
        await message.reply_text("❌ Failed to unban user.")


@app.on_message(filters.command("add_time"))
async def add_time_handler(client: Client, message: Message):
    """Handle /add_time [user_id] [minutes] - Add time to user."""
    user_id = message.from_user.id

    if not admin_manager.is_admin(user_id):
        await message.reply_text("⛔ Unauthorized.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.reply_text("Usage: `/add_time [user_id] [minutes]`")
        return

    try:
        target_user_id = int(args[1])
        minutes = float(args[2])
    except ValueError:
        await message.reply_text("❌ Invalid arguments.")
        return

    success = admin_manager.add_time_to_user(target_user_id, minutes)
    if success:
        new_balance = monetization.get_remaining_minutes(target_user_id)
        await message.reply_text(
            f"✅ **Time Added!**\n\n"
            f"👤 **User:** {target_user_id}\n"
            f"⏱ **Added:** {minutes} minutes\n"
            f"📊 **New Balance:** {new_balance:.1f} minutes"
        )
    else:
        await message.reply_text("❌ Failed to add time.")


@app.on_message(filters.command("admin"))
async def admin_panel_handler(client: Client, message: Message):
    """Handle /admin - Show admin panel."""
    user_id = message.from_user.id

    if not admin_manager.is_admin(user_id):
        await message.reply_text("⛔ **Access Denied**\n\nYou are not an admin.")
        return

    await message.reply_text(
        "🔐 **Admin Panel**\n\n"
        "Select an option:",
        reply_markup=get_admin_keyboard()
    )


# ── Other Commands ──────────────────────────────────────────────────────────

@app.on_message(filters.command("balance"))
async def balance_handler(client: Client, message: Message):
    """Handle /balance - Show time balance."""
    user_id = message.from_user.id
    balance_msg = monetization.get_balance_message(user_id)
    await message.reply_text(balance_msg, reply_markup=get_main_keyboard(user_id))


@app.on_message(filters.command("recharge"))
async def recharge_handler(client: Client, message: Message):
    """Handle /recharge - Show recharge options."""
    user_id = message.from_user.id
    recharge_msg = monetization.get_recharge_options()
    await message.reply_text(
        recharge_msg,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Contact Admin", url=f"tg://user?id={MAIN_ADMIN_ID}")],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu_main")]
        ])
    )


@app.on_message(filters.command("history"))
async def history_handler(client: Client, message: Message):
    """Handle /history - Show upload history."""
    user_id = message.from_user.id
    history = db.get_upload_history(user_id, limit=20)

    if not history:
        await message.reply_text(
            "📋 **Upload History**\n\nNo uploads yet!",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    lines = ["📋 **Upload History**\n"]
    for h in history:
        status_emoji = {
            "completed": "✅",
            "failed": "❌",
            "pending": "⏳",
            "downloading": "⬇️",
            "uploading": "⬆️"
        }.get(h["status"], "❓")

        title = h.get("title", "Untitled")[:30]
        vid = h.get("youtube_video_id", "")
        lines.append(f"{status_emoji} **{title}**")
        if vid:
            lines.append(f"   🔗 https://youtube.com/watch?v={vid}")
        lines.append("")

    await message.reply_text(
        "\n".join(lines),
        reply_markup=get_main_keyboard(user_id)
    )


@app.on_message(filters.command("support"))
async def support_handler(client: Client, message: Message):
    """Handle /support - Contact support."""
    await message.reply_text(
        "📞 **Support**\n\n"
        "Need help? Contact the admin directly:\n"
        f"👤 **Admin:** @admin\n"
        f"🆔 **ID:** `{MAIN_ADMIN_ID}`\n\n"
        "For common issues, try /help first!"
    )


# ── Error Handler ───────────────────────────────────────────────────────────

@app.on_message()
async def error_handler(client: Client, message: Message):
    """Catch-all handler for unhandled messages."""
    user_id = message.from_user.id
    await message.reply_text(
        "ℹ️ I didn't understand that.\n\n"
        "Send me a **Google Drive video link** to upload it to YouTube, "
        "or use the buttons below.",
        reply_markup=get_main_keyboard(user_id)
    )


# ── Startup & Shutdown ──────────────────────────────────────────────────────

async def on_startup():
    """Run on bot startup."""
    logger.info("Bot starting up...")
    validate_config()

    # Ensure data directories exist
    from bot.config import DATA_DIR, DOWNLOADS_DIR, THUMBNAILS_DIR
    for dir_path in [DATA_DIR, DOWNLOADS_DIR, THUMBNAILS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Start the upload queue processor
    await upload_queue.start()
    logger.info("Upload queue processor started")

    logger.info("Bot is ready!")


async def on_shutdown():
    """Run on bot shutdown."""
    logger.info("Bot shutting down...")
    await upload_queue.stop()
    logger.info("Bot stopped.")


# ── Main Entry Point ─────────────────────────────────────────────────────────

def main():
    """Main entry point to start the bot."""
    try:
        validate_config()
        logger.info("Starting SaaS YouTube Uploader Bot...")

        # Run startup
        loop = asyncio.get_event_loop()
        loop.run_until_complete(on_startup())

        # Start Pyrogram client
        app.run()

    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.error(f"Bot error: {e}")
    finally:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(on_shutdown())


if __name__ == "__main__":
    main()
