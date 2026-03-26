# Telegram Registration Bot — Production System

Automated account creation and OTP verification for **your own website only**.

## What it does

1. Receives `/create email password` from Telegram
2. Calls your site's registration API
3. Waits for the OTP email in a dedicated Gmail label
4. Extracts the OTP code (or activation link)
5. Submits it back to your site
6. Confirms success or failure via Telegram

---

## Quick start

### 1. Install Python dependencies

```bash
cd bot_system
pip install -r requirements.txt
```

### 2. Set up Gmail API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable the **Gmail API**
3. Create an **OAuth 2.0 Client ID** (type: Desktop app)
4. Download the JSON file and save as `credentials.json` (or set `GMAIL_CREDENTIALS_FILE`)
5. Create a Gmail label named `OTP` (or whatever you set in `GMAIL_OTP_LABEL`)
6. Set up a filter: emails from your site → apply the OTP label automatically

On first run, the bot will open a browser for Gmail authorisation and save `token.json`.

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your values
```

Key variables:

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated Telegram user IDs |
| `GMAIL_CREDENTIALS_FILE` | Path to Google OAuth credentials JSON |
| `GMAIL_OTP_LABEL` | Gmail label name receiving OTP emails |
| `SITE_API_BASE_URL` | Your site's API base URL |
| `SITE_API_KEY` | Bearer token or API key for your site |
| `SITE_INTEGRATION_MODE` | `api` (default) or `playwright` |

### 4. Plug in your site's API endpoints

Edit `app/site/api_client.py`. Every method has a clearly marked `PLUG IN YOUR ENDPOINT HERE` block:

- `create_account` → `POST /register`
- `request_otp` → `POST /resend-otp` (or no-op)
- `submit_otp` → `POST /verify-otp`
- `finalize_account` → optional post-verification step
- `get_account_status` → `GET /account/status`

Change only the `endpoint` variable and `payload` dict in each method.

### 5. Run the bot

```bash
cd bot_system
# Load env vars first
export $(cat .env | grep -v '^#' | xargs)
python -m app.main
```

Or on Replit: set secrets in the Secrets panel and start a workflow with:
```
python bot_system/app/main.py
```

---

## Telegram commands

| Command | Description |
|---|---|
| `/create email password` | Create a single account |
| `/batch_create` + multi-line | Create multiple accounts |
| `/status JOB_ID` | Check job status |
| `/retry JOB_ID password` | Retry a failed job |
| `/jobs` | List recent jobs |
| `/help` | Show help |

---

## Project structure

```
bot_system/
  app/
    bot/          # Telegram handlers and command logic
    core/         # Config, enums, logger, utils
    gmail/        # Gmail API client, OTP watcher, parser, matcher
    jobs/         # Job manager and background scheduler
    services/     # Registration orchestration, notification
    site/         # Site integration layer (API + Playwright stub)
    storage/      # SQLite DB init, models, repositories
    main.py       # Entry point
  tests/          # pytest unit tests
  requirements.txt
  .env.example
```

---

## Running tests

```bash
cd bot_system
pytest tests/ -v
```

No external credentials are needed for the unit tests — they mock all I/O.

---

## OTP email matching logic

To prevent mixing OTP codes between concurrent jobs the matcher checks:
- Recipient address matches the job email exactly
- Email arrived **after** the job was created (with a small lookback tolerance)
- Sender matches `OTP_ALLOWED_SENDERS` if configured
- Subject matches `OTP_SUBJECT_PATTERN` if configured
- Message has not already been used by another job

---

## Playwright fallback (browser automation)

If HTTP API access is unavailable:

```bash
pip install playwright
playwright install chromium
SITE_INTEGRATION_MODE=playwright python -m app.main
```

Then fill in the selectors in `app/site/playwright_client.py`.

---

## Security

- Only your Telegram user IDs (set in `TELEGRAM_ALLOWED_USER_IDS`) can use the bot
- The system only targets the single site configured in `SITE_API_BASE_URL`
- Credentials are stored only in environment variables / Replit Secrets
- Gmail access uses OAuth2 with the minimum required scope (`gmail.modify`)

---

## Retry and error handling

| Error type | Behaviour |
|---|---|
| Network timeout | Retried up to `HTTP_MAX_RETRIES` with exponential backoff |
| Gmail API failure | Logged, polling continues until timeout |
| OTP timeout | Job marked failed with reason |
| Invalid OTP | Retried up to `OTP_MAX_ATTEMPTS` |
| Duplicate account | Job marked failed with clear message |
| Unhandled exception | Caught at job runner level, job marked failed |
