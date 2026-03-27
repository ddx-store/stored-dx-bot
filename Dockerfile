FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    fonts-noto-color-emoji \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libgtk-3-0 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=0
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV CHROMIUM_PATH=/usr/bin/chromium

WORKDIR /app

COPY bot_system/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot_system/ .

RUN mkdir -p data

CMD ["python", "app/main.py"]
