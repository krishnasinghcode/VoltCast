"""Idempotent bootstrap: demo users + the SQL-sandbox read-only role.

    python src/seed_users.py            (from services/dashboard/api, or `docker compose exec api ...`)

1. Waits for Postgres and for the `users` table (created by infra/postgres/init.sql).
2. Creates one ADMIN, one FLEET_MANAGER and one VIEWER user if their email doesn't exist yet. Passwords come
   from SEED_*_PASSWORD env vars (dev defaults below — local demo only, change them for anything shared).
3. Ensures POSTGRES_READONLY_USER exists with SELECT-only grants and NO access to `users` (password hashes),
   plus a role-level statement_timeout. dashboardMaster.md §7 puts this in init.sql; init.sql in this repo
   doesn't have it, and this module may not edit infra/ files, so it is created here (needs the app user to
   have CREATEROLE — true for the docker postgres image, where POSTGRES_USER is a superuser). Skipped with a
   warning if not permitted; the sandbox then returns 503 SANDBOX_UNAVAILABLE.

The Docker image runs this before uvicorn on every boot, so a fresh `docker compose up` is immediately usable.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from src import config, db  # noqa: E402
from src.auth import hash_password  # noqa: E402

DEMO_USERS = [
    ("admin@cvi.local", os.getenv("SEED_ADMIN_PASSWORD", "admin123"), "ADMIN"),
    ("manager@cvi.local", os.getenv("SEED_MANAGER_PASSWORD", "manager123"), "FLEET_MANAGER"),
    ("viewer@cvi.local", os.getenv("SEED_VIEWER_PASSWORD", "viewer123"), "VIEWER"),
]


def wait_for_schema(timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            with db.get_engine().connect() as conn:
                conn.execute(text("SELECT 1 FROM users LIMIT 1"))
            return
        except SQLAlchemyError as exc:
            last_err = exc
            time.sleep(2)
    raise SystemExit(f"postgres/users table not ready after {timeout_s}s: {last_err}")


def seed_users() -> None:
    with db.get_engine().begin() as conn:
        for email, password, role in DEMO_USERS:
            exists = conn.execute(text("SELECT 1 FROM users WHERE email = :e"), {"e": email}).first()
            if exists:
                print(f"[seed] user {email} already exists — skipped")
                continue
            conn.execute(text("INSERT INTO users (email, password_hash, role) VALUES (:e, :h, :r)"),
                         {"e": email, "h": hash_password(password), "r": role})
            print(f"[seed] created {role} user {email}")


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def ensure_readonly_role() -> None:
    role, password = config.POSTGRES_READONLY_USER, config.POSTGRES_READONLY_PASSWORD
    ident, database = _quote_ident(role), _quote_ident(config.POSTGRES_DB)
    try:
        with db.get_engine().begin() as conn:
            exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
            if exists:
                conn.exec_driver_sql(f"ALTER ROLE {ident} WITH LOGIN PASSWORD {_quote_literal(password)}")
            else:
                conn.exec_driver_sql(f"CREATE ROLE {ident} WITH LOGIN PASSWORD {_quote_literal(password)}")
            conn.exec_driver_sql(f"ALTER ROLE {ident} SET statement_timeout = '3s'")
            conn.exec_driver_sql(f"ALTER ROLE {ident} SET default_transaction_read_only = on")
            conn.exec_driver_sql(f"GRANT CONNECT ON DATABASE {database} TO {ident}")
            conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {ident}")
            conn.exec_driver_sql(f"REVOKE CREATE ON SCHEMA public FROM {ident}")
            conn.exec_driver_sql(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {ident}")
            conn.exec_driver_sql(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {ident}")
            # password hashes are never exposed through the sandbox
            conn.exec_driver_sql(f"REVOKE ALL ON TABLE users FROM {ident}")
        print(f"[seed] read-only role {role} ready (SELECT only, no access to users)")
    except SQLAlchemyError as exc:
        print(f"[seed] WARNING: could not create read-only role {role}: {exc.__class__.__name__}: "
              f"{str(exc).splitlines()[0]} — SQL sandbox will be unavailable")


def main() -> None:
    wait_for_schema()
    seed_users()
    ensure_readonly_role()


if __name__ == "__main__":
    main()
