# Crypto PAPER-trading bot (simulation only - no exchange keys, no real orders)
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/Chicago
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY paperbot ./paperbot
COPY dashboard ./dashboard
COPY config.yaml .
RUN mkdir -p /app/data && useradd -m bot && chown -R bot /app
USER bot
VOLUME ["/app/data"]
CMD ["python", "-m", "paperbot", "live"]
