# Registration & Payment Bot

## Overview

Python Telegram bot (@STOREDDXBOT) that auto-registers accounts and auto-pays for subscriptions on any website using Playwright + headless Chromium. Users pick a site from inline buttons or use commands. The bot fills forms, handles OTP, processes payments, and reports progress via a single editable Telegram message.

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
      commands.py          # /start, /create, /pay, inline keyboard, callback + text handlers
      handlers.py          # Handler registration (commands, callbacks, text)
      telegram_client.py   # send_message, edit_message, delete_message wrappers
    core/                  # Config, logging, enums, utils
    gmail/                 # IMAP client, OTP parser, matcher
    jobs/                  # Job manager, background scheduler
    services/
      notification_service.py  # Single-message progress (edit in-place)
      registration_service.py  # Orchestrates Playwright + OTP flow
      payment_service.py       # Orchestrates payment flow
    site/
      playwright_client.py     # Main browser automation for registration
      payment_client.py        # Browser automation for payment/subscription
    storage/               # SQLite DB, models, repositories
    main.py                # Entry point
```

## Features

### 1. Auto-Registration
1. User presses /start and picks a site from inline buttons (ChatGPT, Google, etc.)
2. Bot asks for email only — user sends email text
3. Bot creates a job, sends a single progress message with step indicators
4. PlaywrightClient navigates to site, finds registration form, fills fields
5. Progress message updates in-place showing step-by-step status
6. OTP: polls Gmail IMAP, types code into verification form
7. Final message shows all steps checked off with success/failure result

### 2. Auto-Payment (NEW)
1. User presses /pay or "💳 الدفع التلقائي" button from main menu
2. Selects a subscription site (ChatGPT Plus, Canva Pro, ProtonVPN, Pixlr, Replit)
3. Bot asks for email → password → card details (step by step)
4. Card format: number, MM/YY, CVV, holder name (each on separate line)
5. Bot logs into the site, navigates to upgrade/pricing page
6. Fills payment form (supports Stripe iframes + direct card forms)
7. Confirms payment and reports result

## UI Design

- **Inline keyboard** with preset sites + "custom site" + "💳 الدفع التلقائي" button
- **Single editable message** for progress — 6-step checklist updated via `edit_message`
- Registration steps: فتح الموقع → البحث عن التسجيل → تعبئة البيانات → إرسال النموذج → التحقق من البريد → إكمال الملف
- Payment steps: فتح الموقع → تسجيل الدخول → صفحة الاشتراك → تعبئة البطاقة → تأكيد الدفع → التحقق من النتيجة
- Icons: ▫️ pending, ◐◓◑◒ in-progress (animated), ✅ done, ❌ failed

## Commands

| Command | Purpose |
|---|---|
| `/start` | Main menu with registration + payment options |
| `/pay` | Payment menu with subscription sites |
| `/create site.com email` | Direct registration |
| `/help` | Usage instructions |
| `/status JOB_ID` | Check job status |
| `/jobs` | List recent operations |

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
- PAYMENT_TIMEOUT=300s for payment operations
- ALL Telegram messages use plain text (no Markdown)
- Card data is NOT saved — user sends it fresh each time
- Payment client handles Stripe iframes (js.stripe.com) + direct card forms
- Preset payment sites: ChatGPT, Canva, ProtonVPN, Pixlr, Replit
- Payment sites configurable in `PAYMENT_SITES` list in commands.py
- Registration sites configurable in `PRESET_SITES` list in commands.py
