FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg for audio extraction / remuxing.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY bot.py ./
COPY mdbot ./mdbot

RUN mkdir -p /app/data /tmp/media_bot_downloads

ENV DOWNLOAD_DIR=/tmp/media_bot_downloads \
    DB_PATH=/app/data/media_bot.db

CMD ["python", "bot.py"]
