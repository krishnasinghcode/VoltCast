"""Minimal JWT auth + RBAC (dashboardMaster.md §3, CONTRACT.md §8).

Deliberate scope cut: HS256 access tokens only — no refresh tokens, OAuth2/OIDC, or password reset. A real
IdP can replace create_access_token()/decode_token() later without touching any require_role() call site.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from . import config
from .errors import ApiError

ROLES = ("ADMIN", "FLEET_MANAGER", "VIEWER")
READ_ROLES = ("ADMIN", "FLEET_MANAGER", "VIEWER")
WRITE_ROLES = ("ADMIN", "FLEET_MANAGER")
ADMIN_ONLY = ("ADMIN",)

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    role: str
    email: Optional[str] = None


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, role: str, email: Optional[str] = None) -> tuple[str, int]:
    expires_in = config.JWT_EXPIRY_MINUTES * 60
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    if email:
        claims["email"] = email
    return jwt.encode(claims, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM), expires_in


def decode_token(token: str) -> CurrentUser:
    try:
        claims = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except JWTError:
        raise ApiError(401, "UNAUTHORIZED", "invalid or expired token")
    sub, role = claims.get("sub"), claims.get("role")
    if not sub or role not in ROLES:
        raise ApiError(401, "UNAUTHORIZED", "token is missing required claims")
    return CurrentUser(user_id=sub, role=role, email=claims.get("email"))


def try_decode_token(token: Optional[str]) -> Optional[CurrentUser]:
    """Non-raising variant, used by the rate limiter to key requests by user when possible."""
    if not token:
        return None
    try:
        return decode_token(token)
    except ApiError:
        return None


def bearer_token_from_header(value: Optional[str]) -> Optional[str]:
    if value and value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return None


def get_current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> CurrentUser:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise ApiError(401, "UNAUTHORIZED", "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    return decode_token(creds.credentials)


def require_role(*roles: str):
    """FastAPI dependency factory: every endpoint declares exactly which roles may call it."""
    allowed = set(roles)

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise ApiError(403, "FORBIDDEN", f"role {user.role} may not perform this action",
                           {"required_roles": sorted(allowed)})
        return user

    return _dep
