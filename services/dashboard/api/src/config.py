"""Environment-driven settings (CONTRACT.md §7). Every value has a local-dev default so the service and
its tests can boot without a .env file; docker compose injects the real values via env_file."""
from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


CONTRACT_VERSION = "1.0.0"
SERVICE_NAME = "api"

# Postgres
POSTGRES_HOST = _env("POSTGRES_HOST", "postgres")
POSTGRES_PORT = _int("POSTGRES_PORT", 5432)
POSTGRES_DB = _env("POSTGRES_DB", "cvi")
POSTGRES_USER = _env("POSTGRES_USER", "cvi_app")
POSTGRES_PASSWORD = _env("POSTGRES_PASSWORD", "changeme_local_only")
POSTGRES_READONLY_USER = _env("POSTGRES_READONLY_USER", "cvi_readonly")
POSTGRES_READONLY_PASSWORD = _env("POSTGRES_READONLY_PASSWORD", "changeme_local_only_readonly")
# Optional full override (used by tests / non-docker local runs)
DATABASE_URL = os.getenv("DATABASE_URL")

# Redis
REDIS_HOST = _env("REDIS_HOST", "redis")
REDIS_PORT = _int("REDIS_PORT", 6379)

# Peer services
DATA_GEN_URL = _env("DATA_GEN_URL", "http://data-gen:8001").rstrip("/")
INGESTION_URL = _env("INGESTION_URL", "http://ingestion:8002").rstrip("/")
ML_URL = _env("ML_URL", "http://ml:8003").rstrip("/")

# Auth
JWT_SECRET = _env("JWT_SECRET", "changeme_local_only_jwt_secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = _int("JWT_EXPIRY_MINUTES", 120)

# Rate limiting (dashboardMaster.md §4)
RATE_LIMIT_PER_MINUTE = _int("RATE_LIMIT_PER_MINUTE", 120)
RATE_LIMIT_WINDOW_SECONDS = 60

# Caching / live updates
FLEET_SUMMARY_TTL_SECONDS = 10
WS_PUSH_INTERVAL_SECONDS = float(os.getenv("WS_PUSH_INTERVAL_SECONDS", "2"))

# CORS: comma separated. The prod nginx build is same-origin, so this only matters for `npm run dev`.
CORS_ORIGINS = [o.strip() for o in _env(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"
).split(",") if o.strip()]

# Timeouts for proxied calls
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "15"))
ML_TRAIN_TIMEOUT_SECONDS = float(os.getenv("ML_TRAIN_TIMEOUT_SECONDS", "600"))

# SQL sandbox (dashboardMaster.md §7)
SQL_SANDBOX_ENABLED = _env("SQL_SANDBOX_ENABLED", "true").lower() == "true"
SQL_SANDBOX_TIMEOUT = "3s"
SQL_SANDBOX_MAX_ROWS = 500
