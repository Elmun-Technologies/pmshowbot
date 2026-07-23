FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

# Runtime data (mount these as volumes to persist across restarts).
RUN mkdir -p /app/data /app/media

CMD ["python", "-m", "bot.main"]
