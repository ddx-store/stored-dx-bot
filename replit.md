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
      commands.py          # /start, /create, /pay, /cancel, /accounts, inline keyboard, callback + text handlers
      handlers.py          # Handler registration (commands, callbacks, text)
      telegram_client.py   # send_message, edit_message, delete_message wrappers
    core/                  # Config, logging, enums, utils
    gmail/                 # IMAP client, OTP parser, matcher
    jobs/                  # Job manager, background scheduler (with cancel + rate limiting)
    services/
      notification_service.py  # Single-message progress (edit in-place) + retry buttons
      registration_service.py  # Orchestrates Playwright + OTP flow + saves accounts
      payment_service.py       # Orchestrates payment flow + saves accounts
    site/
      playwright_client.py     # Main browser automation for registration
      payment_client.py        # Browser automation for payment/subscription
    storage/               # SQLite DB, models (SavedAccount), repositories (SavedAccountRepository, CleanupRepository)
    main.py                # Entry point + cleanup thread
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
8. On success: account saved automatically (site, email, password)

### 2. Auto-Payment
1. User presses /pay or "💳 تفعيل حساب" button from main menu
2. Selects a subscription site (ChatGPT Plus, Canva Pro, ProtonVPN, Pixlr, Replit)
3. Bot asks for email → password → card details (step by step)
4. Card format: number, MM/YY, CVV, holder name (each on separate line)
5. Bot logs into the site, navigates to upgrade/pricing page
6. Fills payment form (supports Stripe iframes + direct card forms)
7. Confirms payment and reports result
8. On success: account saved automatically

### 3. Saved Accounts
- All successful registrations and payments are saved with credentials
- Accessible via /accounts command or "📋 حساباتي" button in main menu
- Shows site, email, password, plan name, date

### 4. Job Cancellation
- /cancel command cancels all active jobs for the user
- Cancelled jobs stop gracefully and notify the user
- Scheduler tracks cancellation state per job

### 5. Rate Limiting
- MAX_CONCURRENT_JOBS (default: 2) per user
- Prevents spamming multiple jobs simultaneously
- User gets clear message when at limit

### 6. Retry on Failure
- Failed operations show a "🔄 إعادة المحاولة" button
- Clicking retry pre-fills the site and asks for email again
- Cancelled operations don't show retry button

### 7. Auto-Cleanup
- Background thread runs every 6 hours
- Deletes completed/failed/cancelled jobs older than CLEANUP_DAYS (default: 30)
- Cleans jobs, payment_jobs, audit_logs, results tables

### 8. Admin Notifications
- Set ADMIN_CHAT_ID to receive alerts on job failures
- Includes job ID, site, email, and error message

## UI Design

- **Home screen**: 3 main buttons (📝 إنشاء حساب, 💳 تفعيل حساب, 📋 حساباتي)
- Each button leads to site selection, then step-by-step data collection
- **Single editable message** for progress — 6-step checklist updated via `edit_message`
- Registration steps: فتح الموقع → البحث عن التسجيل → تعبئة البيانات → إرسال النموذج → التحقق من البريد → إكمال الملف
- Payment steps: فتح الموقع → تسجيل الدخول → صفحة الاشتراك → تعبئة البطاقة → تأكيد الدفع → التحقق من النتيجة
- Icons: ▫️ pending, ◐◓◑◒ in-progress (animated), ✅ done, ❌ failed
- Navigation: back buttons on every screen, clean callback_data prefixes (reg:, pay:, menu:, back:, retry_reg:, retry_pay:)
- On failure: retry button + back to menu button
- On success: back to menu button

## Commands

| Command | Purpose |
|---|---|
| `/start` | Main menu with registration + payment + accounts |
| `/pay` | Payment menu with subscription sites |
| `/create site.com email` | Direct registration |
| `/help` | Usage instructions |
| `/status JOB_ID` | Check job status |
| `/jobs` | List recent operations |
| `/cancel` | Cancel all active operations |
| `/accounts` | View saved accounts |

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
| `MAX_CONCURRENT_JOBS` | Max simultaneous jobs per user (default: 2) |
| `ADMIN_CHAT_ID` | Telegram chat ID for admin alerts (default: 0 = disabled) |
| `CLEANUP_DAYS` | Days to keep old job records (default: 30) |

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
- Successful operations auto-save to `saved_accounts` table
- Cleanup runs every 6 hours in a daemon thread
