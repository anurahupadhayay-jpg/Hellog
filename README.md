# SaaS Telegram Bot - YouTube Uploader

A production-ready, modular Telegram bot built with **Pyrogram** that allows users to upload videos from **Google Drive** directly to their **own YouTube channel** using **OAuth 2.0 authentication**.

## Features

- **Google Drive to YouTube Upload** - Seamlessly transfer videos from Drive to YouTube
- **OAuth 2.0 Authentication** - Users authenticate with their own Google/YouTube account
- **SaaS Monetization** - Time-based billing (1 Rupee per hour)
- **Upload Queue System** - Sequential processing prevents server overload
- **Real-time Progress Tracking** - Live progress bars for download & upload with speed and ETA
- **Custom Metadata** - Title, description, tags, privacy settings, category, and custom thumbnails
- **Resumable Uploads** - Auto-retry with exponential backoff for network errors
- **Admin Controls** - Emergency admin login, broadcast messaging, user management
- **Auto-cleanup** - Immediate file deletion after upload to save storage

## Architecture

```
telegram_youtube_bot/
├── bot/
│   ├── __init__.py          # Package init
│   ├── config.py            # Configuration & environment variables
│   ├── database.py          # SQLite database operations
│   ├── oauth_handler.py     # YouTube OAuth 2.0 flow
│   ├── drive_downloader.py  # Google Drive download with progress
│   ├── youtube_uploader.py  # YouTube upload with resumable support
│   ├── upload_queue.py      # FIFO queue system
│   ├── monetization.py      # Timer & billing system
│   ├── admin.py             # Admin controls & broadcast
│   └── main.py              # Main entry point & handlers
├── data/                    # SQLite database & logs
├── downloads/               # Temporary video downloads
├── thumbnails/              # Thumbnail storage
├── credentials/             # Google OAuth credentials
├── requirements.txt         # Python dependencies
├── .env.example             # Environment template
├── run.py                   # Launcher script
└── README.md                # This file
```

## Setup Instructions

### 1. Prerequisites

- Python 3.9+
- A Telegram account
- A Google Cloud project with YouTube Data API v3 enabled

### 2. Clone and Install

```bash
# Clone the repository
git clone <your-repo-url>
cd telegram_youtube_bot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your credentials
nano .env
```

### 4. Get Telegram API Credentials

1. Go to https://my.telegram.org and create an application
2. Copy the `API_ID` and `API_HASH`
3. Message @BotFather on Telegram to create a new bot
4. Copy the bot token

### 5. Set Up Google OAuth 2.0

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable the **YouTube Data API v3**
4. Go to **Credentials** > **Create Credentials** > **OAuth client ID**
5. Application type: **Desktop App**
6. Download the JSON and save as `credentials/client_secrets.json`
7. Add these OAuth scopes:
   - `https://www.googleapis.com/auth/youtube.upload`
   - `https://www.googleapis.com/auth/youtube.readonly`

### 6. Run the Bot

```bash
python run.py
```

## Bot Commands

### User Commands
| Command | Description |
|---------|-------------|
| `/start` | Start the bot and register |
| `/login` | Connect your YouTube account |
| `/logout` | Disconnect YouTube account |
| `/upload` | Start upload wizard |
| `/balance` | Check time balance |
| `/recharge` | View recharge options |
| `/history` | View upload history |
| `/stats` | View your statistics |
| `/support` | Contact admin |
| `/help` | Show help message |

### Admin Commands
| Command | Description |
|---------|-------------|
| `/admin_login [username] [password]` | Emergency admin access |
| `/admin_logout` | Revoke admin access |
| `/admin` | Admin panel |
| `/broadcast` (reply to message) | Broadcast to all users |
| `/broadcast_text [message]` | Send text to all users |
| `/user_info [user_id]` | Get user details |
| `/ban [user_id]` | Ban a user |
| `/unban [user_id]` | Unban a user |
| `/add_time [user_id] [minutes]` | Add time to user |
| `/stats` | Bot statistics |
| `/queue_status` | Check upload queue |

## How It Works

### For Users

1. **Start** the bot with `/start`
2. **Login** with Google using `/login` (one-time setup)
3. Send a **Google Drive video link**
4. Fill in **video details** (title, description, tags, privacy, category)
5. Optionally send a **custom thumbnail**
6. **Confirm** and the bot queues your upload
7. Track **real-time progress** of download and upload
8. Video appears on **YOUR YouTube channel**!

### For Admins

1. Set `MAIN_ADMIN_ID` in `.env` to your Telegram user ID
2. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` for emergency access
3. Use `/admin` for the admin panel
4. Use `/broadcast` (reply to any message) to broadcast
5. Manage users with `/user_info`, `/ban`, `/unban`, `/add_time`

## Monetization Model

- **Rate:** 1 Rupee per hour (60 minutes)
- **Free Trial:** 30 minutes for new users (configurable)
- **Time Deduction:** Based on file size (~100MB per minute base rate)
- **Warning:** Users warned when < 10 minutes remaining
- **Block:** Uploads blocked when balance = 0

## Database Schema

### Tables
- **users** - User registration info
- **oauth_tokens** - Encrypted OAuth credentials per user
- **time_balances** - Remaining time balance per user
- **upload_history** - Upload job history
- **recharges** - Payment/recharge history
- **admin_sessions** - Temporary admin sessions
- **upload_queue** - Pending upload jobs

## Security Considerations

1. **OAuth tokens** are stored as JSON in SQLite (encrypt in production)
2. **Admin credentials** should be strong and rotated regularly
3. **File uploads** are immediately cleaned up after processing
4. **Rate limiting** should be added for production use
5. **HTTPS** is required for OAuth redirect in production

## Advanced Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_API_ID` | - | Telegram API ID |
| `TELEGRAM_API_HASH` | - | Telegram API Hash |
| `TELEGRAM_BOT_TOKEN` | - | Bot token from @BotFather |
| `MAIN_ADMIN_ID` | 7682705436 | Admin Telegram user ID |
| `ADMIN_USERNAME` | admin | Emergency login username |
| `ADMIN_PASSWORD` | - | Emergency login password |
| `COST_PER_HOUR` | 1.0 | Price in Rupees per hour |
| `DEFAULT_FREE_MINUTES` | 30 | Free trial minutes |
| `WARNING_THRESHOLD_MINUTES` | 10 | Low balance warning threshold |
| `MAX_FILE_SIZE` | 2GB | Maximum file size in bytes |
| `UPLOAD_CHUNK_SIZE` | 256KB | Upload chunk size |
| `MAX_RETRIES` | 3 | Max retry attempts |

## Troubleshooting

### Bot doesn't start
- Check that all environment variables are set
- Ensure `credentials/client_secrets.json` exists
- Check `data/bot.log` for errors

### OAuth errors
- Verify YouTube Data API v3 is enabled
- Check that redirect URI matches Google Cloud settings
- Ensure OAuth consent screen is configured

### Upload failures
- Check user's time balance
- Verify Google Drive link is publicly accessible
- Check `data/bot.log` for detailed error messages

## License

This project is provided as-is for educational and commercial use.

## Support

For support, contact the admin or open an issue on GitHub.
