# Registration Bot

## Overview

Python Telegram bot (@STOREDDXBOT) that auto-registers accounts on any website using Playwright + headless Chromium. Accepts `/create site.com email@example.com`, navigates the site, fills registration forms, submits, and reports results via Telegram in real-time.

## Stack

- **Language**: Python 3.11
- **Bot framework**: python-telegram-bot 21.7
- **Browser automation**: Playwright 1.58 + system Chromium (via Nix)
- **Email**: Gmail IMAP (App Password, no OAuth)
- **Database**: SQLite (WAL mode)
- **Deployment**: Docker (Railway-ready), also runs on Replit

## Project Structure

```
bot_system/                    # The actual application
  app/
    bot/                       # Telegram handlers, commands, client
    core/                      # Config, logging, enums, utils
    gmail/                     # IMAP client, OTP parser, matcher
    jobs/                      # Job manager, background scheduler
    services/                  # Registration, notification, OTP services
    site/                      # PlaywrightClient (main), HttpSiteClient (fallback)
    storage/                   # SQLite DB, models, repositories
    main.py                    # Entry point (chdir to bot_system/, dotenv, polling)
  requirements.txt
  .env.example
Dockerfile                     # Railway/Docker deployment (Chromium + Python)
Procfile                       # Alternative Railway deployment
railway.toml                   # Railway config
README.md                      # GitHub-facing docs
```

**Replit scaffold (not part of the bot)**:
- `artifacts/`, `lib/`, `scripts/`, root `package.json`, `pnpm-workspace.yaml`, `tsconfig*.json`
- These are .gitignored and will not appear on GitHub

## How It Works

1. User sends `/create https://site.com user@email.com` to the bot
2. Bot creates a job, launches Playwright with headless Chromium
3. PlaywrightClient navigates to the site, finds registration form via heuristics
4. Auto-fills fields (name, phone, email, password) based on field attributes
5. Submits form, monitors API responses and page changes
6. Reports success/failure back to user with real-time progress updates
7. (Optional) If OTP needed: polls Gmail IMAP for verification codes

## Key Configuration

All settings via environment variables (see `bot_system/.env.example`):

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated allowed user IDs |
| `FIXED_PASSWORD` | Password for all registrations (default: Hh123456789Hh) |
| `GMAIL_USER` | Gmail address for IMAP OTP polling |
| `GMAIL_APP_PASSWORD` | Gmail App Password (16 chars) |
| `DB_PATH` | SQLite path (default: data/jobs.db, relative to bot_system/) |

## Running

**Replit**: Workflow "Telegram Bot" runs `python3.11 bot_system/app/main.py`
**Docker**: `docker build -t regbot . && docker run --env-file bot_system/.env regbot`
**Railway**: Push to GitHub, connect repo, Railway auto-detects Dockerfile

## Important Notes

- `main.py` does `os.chdir()` to `bot_system/` so all relative paths work from there
- ALL Telegram messages use plain text (no Markdown) — URLs with `_` break Markdown silently
- `drop_pending_updates=True` means commands sent during restart are lost — user must resend
- System Chromium found via `CHROMIUM_PATH` env var or `shutil.which("chromium")`
- PlaywrightClient timeouts: 8s nav + 50s internal + 60s job-level
- `_smart_submit` filters out OAuth buttons (Google, Microsoft, Apple, etc.) to click correct submit
- API URL matching uses `urlparse(url).path` only, not query params (avoids false positives)
- Multi-step forms: `_wait_for_inputs()` polls up to 8s for SPA-rendered inputs between steps
- Navigation: tries signup buttons first, then register links (avoids clicking login links first)
