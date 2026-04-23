# Media Downloader Bot

A production-ready Telegram bot that downloads videos and audio from the major
social-media platforms using [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) as
the primary engine, with automatic format fallbacks, clean error messages, and
admin/ops tooling.

## Supported platforms

TikTok, Instagram, YouTube, X/Twitter, Facebook, SoundCloud, Pinterest,
Reddit, Threads (best-effort), Vimeo, Dailymotion, Twitch, plus anything else
yt-dlp can extract (matched as `generic`).

Snapchat and a few DRM-protected platforms are rejected gracefully because
they can't be supported reliably.

## Features

- **Async** throughout (`python-telegram-bot` v21). yt-dlp runs on a thread
  pool so the event loop never blocks.
- **Fallback format tiers** per kind (video / audio). If a preferred format
  fails, the next one is tried automatically.
- **Platform-aware options** (mobile UA for TikTok, longer timeouts for
  Twitter/Facebook, etc.).
- **Clean user-facing errors** (private content, geo-blocked, 404, timeout,
  copyright takedown, too-large-for-Telegram, …).
- **Rate limiting** — per-user sliding window, global concurrency cap.
- **File-size safety** — refuses uploads above Telegram's 50 MB Bot API limit
  with a helpful message (configurable for self-hosted Bot API servers).
- **MP3 extraction** via ffmpeg (`/audio <url>` or the Audio button).
- **Admin tooling** — `/stats`, `/logs`, `/whoami`, restricted by user id.
- **SQLite stats** for observability.
- **Rotating log files** + structured stdout logs.
- **Deploy-ready** — `Dockerfile`, `Procfile`, `.env.example`.

## Quick start

```bash
cd media_bot
pip install -r requirements.txt        # also install ffmpeg on the host
cp .env.example .env                   # fill TELEGRAM_BOT_TOKEN
python bot.py
```

### Docker

```bash
cd media_bot
docker build -t media-bot .
docker run --rm --env-file .env media-bot
```

### Render / Railway / Replit / VPS

- Railway / Render: point the service at the `media_bot/Dockerfile` or use
  the included `Procfile` worker.
- Replit: set `TELEGRAM_BOT_TOKEN`, then `pip install -r media_bot/requirements.txt`
  and run `python media_bot/bot.py`. `ffmpeg` is available on Replit by default.
- VPS / systemd: install `ffmpeg` and `python 3.11+`, then run
  `python bot.py` under `systemd` or `tmux`.

## Environment variables

| Variable                    | Required | Default                     | Notes |
|-----------------------------|----------|-----------------------------|-------|
| `TELEGRAM_BOT_TOKEN`        | yes      | —                           | From @BotFather |
| `ADMIN_USER_IDS`            | no       | empty                       | CSV of Telegram user ids |
| `ALLOWED_USER_IDS`          | no       | empty (public)              | CSV; if set, only these ids can use the bot |
| `MAX_UPLOAD_MB`             | no       | `50`                        | Raise to ~2000 if you run a local Bot API server |
| `MAX_DOWNLOAD_MB`           | no       | `500`                       | Hard cap on download size |
| `RATE_LIMIT_COUNT`          | no       | `5`                         | Per-user events per window |
| `RATE_LIMIT_WINDOW_SECONDS` | no       | `60`                        | Sliding window length |
| `MAX_CONCURRENT_DOWNLOADS`  | no       | `4`                         | Across all users |
| `DOWNLOAD_DIR`              | no       | `/tmp/media_bot_downloads`  | Temp dir for downloads |
| `DB_PATH`                   | no       | `data/media_bot.db`         | SQLite stats db |
| `COOKIES_FILE`              | no       | —                           | yt-dlp cookie file for logged-in sites |
| `PROXY`                     | no       | —                           | e.g. `socks5://user:pass@host:1080` |
| `LOG_LEVEL`                 | no       | `INFO`                      | `DEBUG`, `INFO`, `WARNING`, … |

## Usage

Send any supported link. The bot replies with a Video / Audio keyboard. You
can also use the direct commands:

```
/video  https://youtu.be/dQw4w9WgXcQ
/audio  https://soundcloud.com/artist/track
```

## Admin commands

Listed for users whose id is in `ADMIN_USER_IDS`:

- `/stats` — totals, success rate, per-platform counts, top users
- `/logs` — last 15 errors with URL and failure reason
- `/whoami` — sanity-check your id/role

## Architecture

```
media_bot/
├── bot.py                  # entrypoint, wires the application
├── Dockerfile              # production image (includes ffmpeg)
├── Procfile                # fallback for Heroku/Railway-style deploys
├── requirements.txt
├── .env.example
└── mdbot/
    ├── config.py           # typed Config dataclass loaded from env
    ├── logging_setup.py    # rotating file + stdout logs
    ├── platforms.py        # URL detection + per-platform yt-dlp options
    ├── downloader.py       # async yt-dlp wrapper with fallback tiers
    ├── rate_limit.py       # per-user sliding-window limiter
    ├── storage.py          # SQLite stats / error log
    ├── handlers.py         # /start, /help, URL handler, inline buttons
    ├── admin.py            # /stats, /logs, /whoami
    └── utils.py
```

### Fallback strategy

For each request the downloader tries format specs in order:

1. `bestvideo[mp4,<=720p]+bestaudio[m4a]/best[mp4,<=720p]`
2. `bestvideo[mp4,<=1080p]+bestaudio[m4a]/best[mp4,<=1080p]`
3. `bestvideo+bestaudio/best`
4. `best` (single stream, no merge)

Audio uses `bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio` then postprocesses
to MP3 (192 kbps) via ffmpeg.

Error categories that short-circuit retries: `private`, `age_restricted`,
`geo_blocked`, `not_found`, `unsupported`, `removed`.

### Why no `aria2`?

`aria2` is disallowed on several cloud PaaS providers and adds operational
overhead. yt-dlp's `concurrent_fragment_downloads` is enough for our use
case.

## Limitations

- Telegram Bot API uploads cap at **50 MB** per file. For 4K YouTube etc.
  either run a [local Bot API server](https://github.com/tdlib/telegram-bot-api)
  (raises the limit to ~2 GB; then bump `MAX_UPLOAD_MB`) or fall back to
  `/audio` which is almost always under the cap.
- Playlists are not auto-expanded; only the first item is downloaded, per
  user expectation.
- Private Instagram / members-only YouTube require cookies (`COOKIES_FILE`).
