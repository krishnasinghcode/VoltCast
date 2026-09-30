"""Postgres (SQLAlchemy) + Redis clients.

This service never creates schema (dashboardMaster.md §11) — it only queries the tables from CONTRACT.md §3.
Engines are module-level singletons created lazily; tests swap them via set_engine()/set_redis().

Queries in the routers deliberately stick to SQL that runs on both Postgres and SQLite (no ::casts, ILIKE,
DISTINCT ON or LATERAL), so the router tests can run against an in-memory SQLite fixture DB in CI without a
Postgres container. Every "latest row per vehicle" lookup is a correlated `ORDER BY ... LIMIT 1` subquery,
which Postgres serves from the (vehicle_id, <time>) indexes in CONTRACT.md §3.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import redis
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, Engine

from . import config
from .errors import ApiError

_engine: Optional[Engine] = None
_readonly_engine: Optional[Engine] = None
_redis: Optional[redis.Redis] = None


def _pg_url(user: str, password: str) -> URL:
    return URL.create(
        "postgresql+psycopg2",
        username=user,
        password=password,
        host=config.POSTGRES_HOST,
        port=config.POSTGRES_PORT,
        database=config.POSTGRES_DB,
    )


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = config.DATABASE_URL or _pg_url(config.POSTGRES_USER, config.POSTGRES_PASSWORD)
        _engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)
    return _engine


def get_readonly_engine() -> Engine:
    """Separate pool logged in as POSTGRES_READONLY_USER — used ONLY by the SQL sandbox."""
    global _readonly_engine
    if _readonly_engine is None:
        _readonly_engine = create_engine(
            _pg_url(config.POSTGRES_READONLY_USER, config.POSTGRES_READONLY_PASSWORD),
            pool_pre_ping=True, pool_size=2, max_overflow=2,
        )
    return _readonly_engine


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis(host=config.REDIS_HOST, port=config.REDIS_PORT, decode_responses=True,
                             socket_timeout=2, socket_connect_timeout=2)
    return _redis


def set_engine(engine: Engine) -> None:
    global _engine
    _engine = engine


def set_readonly_engine(engine: Optional[Engine]) -> None:
    global _readonly_engine
    _readonly_engine = engine


def set_redis(client: redis.Redis) -> None:
    global _redis
    _redis = client


# ---------------------------------------------------------------------------------------------------------
# Row / value helpers
# ---------------------------------------------------------------------------------------------------------

def to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def row_to_dict(row) -> dict:
    return {k: to_jsonable(v) for k, v in row._mapping.items()}


def rows_to_dicts(rows) -> list[dict]:
    return [row_to_dict(r) for r in rows]


def cursor_value(value: Any) -> Any:
    """Raw DB value -> something that round-trips through a cursor and binds back as the same value."""
    return to_jsonable(value)


# ---------------------------------------------------------------------------------------------------------
# Keyset pagination (CONTRACT.md §8): opaque base64url(JSON) cursor, never offsets.
# ---------------------------------------------------------------------------------------------------------

def encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: Optional[str], required_keys: tuple[str, ...]) -> Optional[dict]:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        raise ApiError(400, "INVALID_CURSOR", "cursor is malformed")
    if not isinstance(payload, dict) or any(k not in payload for k in required_keys):
        raise ApiError(400, "INVALID_CURSOR", "cursor does not match this endpoint")
    return payload


def clamp_limit(limit: Optional[int], default: int = 50, maximum: int = 200) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), maximum))


def validate_uuid(value: str, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError):
        raise ApiError(400, "VALIDATION_ERROR", f"{field} must be a UUID", {"field": field})
