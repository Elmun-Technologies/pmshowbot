FROM python:3.11-slim

WORKDIR /app

# DejaVu fonts (Cyrillic + Latin) for the generated participant ticket.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/

# Runtime data (mount these as volumes to persist across restarts).
RUN mkdir -p /app/data /app/media

CMD ["python", "-m", "bot.main"]
