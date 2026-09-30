"""POST /api/v1/auth/login, GET /api/v1/auth/me, POST /api/v1/auth/register (ADMIN only).

No public self-registration: seed_users.py creates the demo accounts; /register exists only so an ADMIN can
add users without shell access.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .. import db
from ..auth import ADMIN_ONLY, READ_ROLES, ROLES, CurrentUser, create_access_token, hash_password, require_role, \
    verify_password
from ..errors import ApiError

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Verifying against a throwaway hash when the email is unknown keeps login timing roughly constant, so
    response time does not reveal which emails exist."""
    return hash_password("not-a-real-password")


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=72)
    role: str


@router.post("/login")
def login(body: LoginRequest):
    email = body.email.strip().lower()
    with db.get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT user_id, email, password_hash, role FROM users WHERE lower(email) = :email"),
            {"email": email},
        ).first()
    if row is None:
        verify_password(body.password, _dummy_hash())
        raise ApiError(401, "INVALID_CREDENTIALS", "invalid email or password")
    if not verify_password(body.password, row.password_hash):
        raise ApiError(401, "INVALID_CREDENTIALS", "invalid email or password")
    token, expires_in = create_access_token(str(row.user_id), row.role, row.email)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "role": row.role,
        "user_id": str(row.user_id),
        "email": row.email,
    }


@router.get("/me")
def me(user: CurrentUser = Depends(require_role(*READ_ROLES))):
    return {"user_id": user.user_id, "role": user.role, "email": user.email}


@router.post("/register", status_code=201)
def register(body: RegisterRequest, _: CurrentUser = Depends(require_role(*ADMIN_ONLY))):
    if body.role not in ROLES:
        raise ApiError(400, "VALIDATION_ERROR", f"role must be one of {list(ROLES)}")
    email = body.email.strip().lower()
    try:
        with db.get_engine().begin() as conn:
            conn.execute(
                text("INSERT INTO users (email, password_hash, role) VALUES (:email, :hash, :role)"),
                {"email": email, "hash": hash_password(body.password), "role": body.role},
            )
            row = conn.execute(text("SELECT user_id, email, role FROM users WHERE email = :email"),
                               {"email": email}).first()
    except IntegrityError:
        raise ApiError(409, "CONFLICT", "a user with that email already exists")
    return db.row_to_dict(row)
