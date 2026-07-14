"""
Configuration module for the SaaS Telegram YouTube Uploader Bot.
All sensitive settings should be loaded from environment variables.
"""

import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = BASE_DIR / "downloads"
THUMBNAILS_DIR = BASE_DIR / "thumbnails"
CREDENTIALS_DIR = BASE_DIR / "credentials"

# Ensure directories exist
for dir_path in [DATA_DIR, DOWNLOADS_DIR, THUMBNAILS_DIR, CREDENTIALS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# ── Telegram Bot Configuration ───────────────────────────────────────────────
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))  # From my.telegram.org
API_HASH = os.getenv("TELEGRAM_API_HASH", "")       # From my.telegram.org
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")     # From @BotFather

# ── Admin Configuration ──────────────────────────────────────────────────────
MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID", "7682705436"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "secure_password_change_me")

# ── Google OAuth 2.0 Configuration ──────────────────────────────────────────
# Path to client_secrets.json downloaded from Google Cloud Console
CLIENT_SECRETS_PATH = os.getenv(
    "CLIENT_SECRETS_PATH",
    str(CREDENTIALS_DIR / "client_secrets.json")
)
# OAuth redirect URI (for flow completion)
OAUTH_REDIRECT_URI = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8080/oauth2callback")
# Scopes required for YouTube upload
YOUTUBE_UPLOAD_SCOPE = ["https://www.googleapis.com/auth/youtube.upload"]
YOUTUBE_READONLY_SCOPE = ["https://www.googleapis.com/auth/youtube.readonly"]
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
                  "https://www.googleapis.com/auth/youtube.readonly"]

# ── Monetization Configuration ───────────────────────────────────────────────
# Cost per hour in Rupees
COST_PER_HOUR = float(os.getenv("COST_PER_HOUR", "1.0"))
# Default free minutes for new users (0 = no free time)
DEFAULT_FREE_MINUTES = float(os.getenv("DEFAULT_FREE_MINUTES", "30"))
# Warning threshold in minutes
WARNING_THRESHOLD_MINUTES = float(os.getenv("WARNING_THRESHOLD_MINUTES", "10"))

# ── Upload Configuration ─────────────────────────────────────────────────────
# Maximum file size allowed (in bytes) - 2GB default
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(2 * 1024 * 1024 * 1024)))
# Chunk size for resumable uploads (256KB recommended)
UPLOAD_CHUNK_SIZE = int(os.getenv("UPLOAD_CHUNK_SIZE", str(256 * 1024)))
# Maximum retry attempts for failed uploads
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
# Queue processing interval in seconds
QUEUE_PROCESS_INTERVAL = int(os.getenv("QUEUE_PROCESS_INTERVAL", "5"))

# ── Database Configuration ───────────────────────────────────────────────────
DATABASE_PATH = str(DATA_DIR / "bot_database.db")

# ── Logging Configuration ────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = str(DATA_DIR / "bot.log")

# ── Feature Flags ────────────────────────────────────────────────────────────
ENABLE_FREE_TRIAL = os.getenv("ENABLE_FREE_TRIAL", "true").lower() == "true"
ENABLE_ADMIN_BROADCAST = os.getenv("ENABLE_ADMIN_BROADCAST", "true").lower() == "true"


# ── Validation ───────────────────────────────────────────────────────────────
def validate_config():
    """Validate that all required configuration values are set."""
    required = {
        "TELEGRAM_API_ID": API_ID,
        "TELEGRAM_API_HASH": API_HASH,
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Please set them in your .env file or environment."
        )
    return True
