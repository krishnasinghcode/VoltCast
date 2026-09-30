FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

EXPOSE 8000

# Seed demo users + read-only sandbox role (idempotent, waits for Postgres), then serve.
# API_WORKERS processes (default 4): Python request handling is CPU-bound, one process saturates ~150 req/s.
# Rate limits live in Redis and request metrics are merged via Redis, so they stay correct across workers.
# No --forwarded-allow-ips='*': trusting X-Forwarded-For from anyone would let a client spoof its IP and dodge
# the login rate limit.
ENV API_WORKERS=4
CMD ["sh", "-c", "python src/seed_users.py && exec uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers ${API_WORKERS}"]
