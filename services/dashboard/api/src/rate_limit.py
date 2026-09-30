"""Redis-backed fixed-window rate limiter (dashboardMaster.md §4, key per CONTRACT.md §6).

Key: ratelimit:<user_id_or_ip>:<endpoint>. INCR, EXPIRE 60 on the first hit, 429 once the count exceeds the
limit. <endpoint> is the matched route template (e.g. /api/v1/vehicles/{vehicle_id}) so browsing many
vehicles still counts against one bucket. Login is always keyed by client IP to throttle password guessing.

Fails open: if Redis is unreachable the request is allowed (and logged) — availability of the read API
matters more than strict throttling for this deployment.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from starlette.requests import Request
from starlette.routing import Match

from . import config, db
from .auth import bearer_token_from_header, try_decode_token

log = logging.getLogger(__name__)

EXEMPT_PATHS = {"/health", "/api/v1/health", "/docs", "/redoc", "/openapi.json"}
LOGIN_PATH = "/api/v1/auth/login"


@dataclass
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int = 0


def route_template(request: Request) -> str:
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


def client_ip(request: Request) -> str:
    # Deliberately NOT reading X-Forwarded-For: it is client-controlled, so trusting it would let an attacker
    # rotate fake IPs to dodge the login throttle. Behind the frontend's nginx all browsers share nginx's IP
    # bucket for /auth/login, which is the safe failure mode.
    return request.client.host if request.client else "unknown"


def identity(request: Request, path: str) -> str:
    if path != LOGIN_PATH:
        user = try_decode_token(bearer_token_from_header(request.headers.get("authorization")))
        if user:
            return user.user_id
    return client_ip(request)


def check(identity_key: str, endpoint: str, limit: Optional[int] = None) -> RateDecision:
    limit = limit if limit is not None else config.RATE_LIMIT_PER_MINUTE
    key = f"ratelimit:{identity_key}:{endpoint}"
    try:
        r = db.get_redis()
        count = r.incr(key)
        if count == 1:
            r.expire(key, config.RATE_LIMIT_WINDOW_SECONDS)
        if count > limit:
            ttl = r.ttl(key)
            if ttl is None or ttl < 0:  # key lost its TTL (crash between INCR and EXPIRE) — repair it
                r.expire(key, config.RATE_LIMIT_WINDOW_SECONDS)
                ttl = config.RATE_LIMIT_WINDOW_SECONDS
            return RateDecision(False, limit, 0, int(ttl))
        return RateDecision(True, limit, max(0, limit - count))
    except Exception as exc:  # fail open
        log.warning("rate limiter unavailable, allowing request: %s", exc)
        return RateDecision(True, limit, limit)
