# FinMind — AI financial Telegram bot
# Build: docker build -t finmind-bot .
# Run:   docker compose up -d

FROM python:3.14-slim

# Non-interactive pip and UTF-8 for the Russian/Cyrillic strings in i18n
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY src/ ./src/
COPY pytest.ini ./

# The bot runs as a non-root user
RUN useradd -m -u 1000 bot && chown -R bot:bot /app
USER bot

# Matplotlib writes config to HOME on first use
ENV MPLCONFIGDIR=/tmp/matplotlib

# Polling entry point
CMD ["python", "-m", "src.main"]
