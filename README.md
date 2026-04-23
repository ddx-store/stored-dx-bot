# Registration Bot

A Telegram bot that automatically registers accounts on any website using Playwright (headless Chromium). Send it a URL and email, and it will:

1. Navigate to the website and find the registration form
2. Auto-fill name, phone, email, password, and other fields
3. Submit the form and report success/failure
4. (Optional) Poll Gmail via IMAP for OTP verification emails

## Quick Start

### Prerequisites

- Python 3.11+
- System Chromium browser (installed via package manager)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Gmail account with IMAP enabled and an App Password (for OTP features)

### Setup

```bash
cd bot_system

# Install dependencies
pip install -r requirements.txt

# Copy and fill in your environment variables
cp .env.example .env
# Edit .env with your values

# Run the bot
python app/main.py
```

### Usage

Send commands to your bot on Telegram:

```
/create https://example.com user@example.com
```

The bot will:
- Open the site in a headless browser
- Find and fill the registration form
- Submit and report results step-by-step

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | No | Comma-separated user IDs (empty = anyone) |
| `FIXED_PASSWORD` | No | Password used for all registrations (default: `Hh123456789Hh`) |
| `GMAIL_USER` | For OTP | Gmail address for IMAP polling |
| `GMAIL_APP_PASSWORD` | For OTP | Gmail App Password (16 chars) |
| `GMAIL_OTP_LABEL` | No | Gmail label to search for OTP emails (default: `TO_BOT`) |
| `DB_PATH` | No | SQLite database path (default: `data/jobs.db`) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |
| `MEDIA_DOWNLOAD_DIR` | No | Directory for temporary media files (default: `/tmp/media_downloads`) |
| `MEDIA_MAX_FILE_SIZE_MB` | No | Max file size for Telegram uploads (default: `50`) |
| `MEDIA_MAX_RETRIES` | No | Download retry attempts (default: `3`) |
| `MEDIA_RATE_LIMIT` | No | Max downloads per user per window (default: `5`) |
| `MEDIA_RATE_WINDOW` | No | Rate limit window in seconds (default: `60`) |

## Deploy to Railway (Docker)

1. Push this repo to GitHub
2. Create a new project on [Railway](https://railway.app)
3. Connect your GitHub repo
4. Railway will auto-detect the `Dockerfile` and `railway.toml`
5. Add environment variables in the Railway dashboard
6. Deploy

**Important**: The Dockerfile installs system Chromium, which is required for Playwright. The `Procfile` is included as a fallback but Docker is the recommended deployment method.

**Storage note**: SQLite is ephemeral in container deployments. The database is recreated on each deploy. For persistent storage, attach a Railway volume mounted at `/app/data`.

## Project Structure

```
bot_system/
  app/
    bot/          # Telegram bot handlers and client
    core/         # Config, logging, utilities, enums
    gmail/        # IMAP Gmail client, OTP parser and matcher
    jobs/         # Job manager and async scheduler
    media/        # Media download engine (yt-dlp), URL parser, rate limiter
    services/     # Registration service, OTP service, notifications
    site/         # Playwright browser client, HTTP fallback client
    storage/      # SQLite database, models, repositories
    main.py       # Entry point
  requirements.txt
  .env.example
Dockerfile        # Railway/Docker deployment
Procfile          # Alternative deployment
railway.toml      # Railway configuration
```

## Architecture

- **Playwright Client**: Headless Chromium browser automation. Finds registration forms via heuristics (email fields, password fields, signup keywords). Fills fields intelligently using field name/type/placeholder matching. Handles multi-step forms, SPA navigation, and AJAX submissions.

- **Gmail IMAP Watcher**: Polls a Gmail label for incoming OTP emails. Extracts numeric codes and verification links using regex patterns. Matches OTPs to active jobs by recipient email and timestamp.

- **Job System**: SQLite-backed job queue with status tracking (pending, creating_account, waiting_for_otp, verifying_otp, completed, failed). Audit logging for all state transitions.

- **Telegram Interface**: Real-time progress updates sent to the user during registration. Supports `/create`, `/status`, `/jobs`, and `/help` commands.

- **Media Downloader**: Downloads video and audio from TikTok, Instagram, YouTube, Twitter/X, Facebook, SoundCloud, Pinterest, Reddit, and Threads using yt-dlp. Features automatic fallback strategies, per-user rate limiting, and file-size validation. Commands: `/dl <url>`, `/mediahelp`, `/mediastats`. Also auto-detects pasted media URLs.
