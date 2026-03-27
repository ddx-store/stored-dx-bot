# Registration Bot

## Overview

Python Telegram bot (@STOREDDXBOT) that auto-registers accounts on any website using Playwright + headless Chromium. Users pick a site from inline buttons or use `/create site.com email@example.com`. The bot fills registration forms, handles OTP, and reports progress via a single editable Telegram message.

## Stack

- **Language**: Python 3.11
- **Bot framework**: python-telegram-bot 21.7
- **Browser automation**: Playwright 1.58 + system Chromium (via Nix)
- **Email**: Gmail IMAP (App Password, no OAuth)
- **Database**: SQLite (WAL mode)

## Project Structure

```
bot_system/
  app/
    bot/
      commands.py          # /start, /create, inline keyboard, callback + text handlers
      handlers.py          # Handler registration (commands, callbacks, text)
      telegram_client.py   # send_message, edit_message, delete_message wrappers
    core/                  # Config, logging, enums, utils
    gmail/                 # IMAP client, OTP parser, matcher
    jobs/                  # Job manager, background scheduler
    services/
      notification_service.py  # Single-message progress (edit in-place)
      registration_service.py  # Orchestrates Playwright + OTP flow
    site/
      playwright_client.py     # Main browser automation engine
    storage/               # SQLite DB, models, repositories
    main.py                # Entry point
```

## How It Works

1. User presses /start and picks a site from inline buttons (ChatGPT, Google, etc.)
2. Bot asks for email only — user sends email text
3. Bot creates a job, sends a single progress message with step indicators
4. PlaywrightClient navigates to site, finds registration form, fills fields
5. Progress message updates in-place (edit, not new messages) showing step-by-step status
6. OTP: polls Gmail IMAP, types code into verification form
7. Final message shows all steps checked off with success/failure result

## UI Design

- **Inline keyboard** with preset sites (ChatGPT, Google, Outlook, GitHub, Discord, X) + "custom site" option
- **Single editable message** for progress — 6-step checklist updated via `edit_message`
- Steps: فتح الموقع → البحث عن التسجيل → تعبئة البيانات → إرسال النموذج → التحقق من البريد → إكمال الملف
- Icons: ⬜ pending, ⏳ in-progress, ✅ done, ❌ failed
- No Job IDs shown to user — clean professional look

## Key Configuration

All settings via environment variables:

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated allowed user IDs |
| `FIXED_PASSWORD` | Password for all registrations (default: Hh123456789Hh) |
| `GMAIL_USER` | Gmail address for IMAP OTP polling |
| `GMAIL_APP_PASSWORD` | Gmail App Password (16 chars) |
| `DB_PATH` | SQLite path (default: data/jobs.db) |

## Running

**Replit**: Workflow "Telegram Bot" runs `python3.11 bot_system/app/main.py`

## Important Notes

- JOB_TIMEOUT=350s, GLOBAL_TIMEOUT=300s to accommodate OTP polling
- ALL Telegram messages use plain text (no Markdown)
- `_click_confirm_dialog`: clicks OK/Done/Got It dialogs after registration
- `_try_url_smart`: accepts email-only forms (not just email+password)
- Spinbutton birthday fill uses keyboard input (type + Tab) for React compatibility
- `_DIRECT_AUTH_URLS` map for sites like ChatGPT that need direct auth URLs
- Preset sites configurable in `PRESET_SITES` list in commands.py
