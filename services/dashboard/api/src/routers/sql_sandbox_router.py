"""SQL sandbox (dashboardMaster.md §7) — every guardrail from the spec, no partial version:

  1. Only a single SELECT / WITH statement (prefix check + no `;` except one trailing).
  2. Executed as POSTGRES_READONLY_USER — a separate role with SELECT-only grants (and no access to `users`).
     This is the real safety boundary; the text checks are defence in depth.
  3. `SET LOCAL statement_timeout = '3s'` inside the same transaction.
  4. `LIMIT 500` applied server-side when the query has none (and results are hard-capped at 500 anyway).
  5. The transaction is always rolled back, even on success.

Extra, beyond spec: transaction is READ ONLY, data-modifying keywords (INSERT/UPDATE/.../INTO) are rejected
even inside CTEs, and seed_users.py sets a role-level statement_timeout as a second timeout.
"""
from __future__ import annotations

import logging
import re
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import DBAPIError, OperationalError

from .. import config, db
from ..auth import READ_ROLES, WRITE_ROLES, CurrentUser, require_role
from ..errors import ApiError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/sql-sandbox", tags=["sql-sandbox"])

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|COPY|CALL|DO|VACUUM|ANALYZE|"
    r"LOCK|INTO|LISTEN|NOTIFY|PREPARE|EXECUTE|REFRESH|REINDEX|CLUSTER|COMMENT|SECURITY)\b"
)
_HAS_LIMIT = re.compile(r"\bLIMIT\b")
_PREFIX = re.compile(r"^(SELECT|WITH)\s")

EXAMPLES = [
    {"title": "Fleet sizes", "query": "SELECT f.name, f.region, COUNT(v.vehicle_id) AS vehicles\n"
                                      "FROM fleets f LEFT JOIN vehicles v ON v.fleet_id = f.fleet_id\n"
                                      "GROUP BY f.name, f.region ORDER BY vehicles DESC"},
    {"title": "Vehicles per archetype", "query": "SELECT archetype, COUNT(*) FROM vehicles GROUP BY archetype"},
    {"title": "Lowest SOH today", "query": "SELECT vehicle_id, date, soh_pct FROM agg_daily\n"
                                           "WHERE date = (SELECT MAX(date) FROM agg_daily)\n"
                                           "ORDER BY soh_pct ASC LIMIT 20"},
    {"title": "High-risk predictions", "query": "SELECT vehicle_id, current_soh, predicted_soh_30d, predicted_at\n"
                                                "FROM battery_predictions WHERE risk_level = 'HIGH'\n"
                                                "ORDER BY predicted_degradation_30d_pct DESC"},
    {"title": "Open alerts by type", "query": "SELECT alert_type, severity, COUNT(*) FROM alerts\n"
                                              "WHERE status = 'OPEN' GROUP BY alert_type, severity"},
    {"title": "Fast charge share (CTE)", "query": "WITH s AS (\n  SELECT charging_type, COUNT(*) AS n "
                                                  "FROM charging_sessions GROUP BY charging_type\n)\n"
                                                  "SELECT charging_type, n, ROUND(100.0 * n / SUM(n) OVER (), 1) "
                                                  "AS pct FROM s"},
]


class SandboxQuery(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)


def _strip_for_analysis(sql: str) -> str:
    """Single-pass scanner that blanks out comments, string literals and quoted identifiers, so keyword checks
    neither false-positive on WHERE alert_type = 'UPDATE' nor get dodged by hiding SQL inside something that
    only *looks* like a comment or string. Dollar-quoted strings are rejected outright (not needed here)."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch, nxt = sql[i], sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            i = n if end == -1 else end
            out.append(" ")
        elif ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                raise ApiError(400, "VALIDATION_ERROR", "unterminated block comment")
            i = end + 2
            out.append(" ")
        elif ch in ("'", '"'):
            j = i + 1
            while True:
                if j >= n:
                    raise ApiError(400, "VALIDATION_ERROR", "unterminated quoted string")
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:  # escaped quote ('' or "")
                        j += 2
                        continue
                    break
                j += 1
            out.append(ch + ch)
            i = j + 1
        elif ch == "$" and (nxt == "$" or nxt.isalpha() or nxt == "_") and re.match(r"\$\w*\$", sql[i:]):
            raise ApiError(400, "VALIDATION_ERROR", "dollar-quoted strings are not allowed in the sandbox")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def prepare_query(raw: str) -> tuple[str, bool]:
    """Validate a sandbox query. Returns (sql_to_execute, limit_was_added). Raises ApiError(400)."""
    sql = raw.strip()
    if sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql:
        raise ApiError(400, "VALIDATION_ERROR", "query is empty")

    analysed = _strip_for_analysis(sql)
    if ";" in analysed or ";" in sql:
        raise ApiError(400, "VALIDATION_ERROR", "only a single statement is allowed (no ';' except a trailing one)")
    upper = analysed.strip().upper()
    if not _PREFIX.match(upper + " "):
        raise ApiError(400, "VALIDATION_ERROR", "query must start with SELECT or WITH")
    forbidden = _FORBIDDEN.search(upper)
    if forbidden:
        raise ApiError(400, "VALIDATION_ERROR", f"keyword {forbidden.group(1)} is not allowed in the sandbox")

    if _HAS_LIMIT.search(upper):
        return sql, False
    # Wrapped (not blindly appended) so a trailing comment or ORDER BY can't swallow the LIMIT.
    return f"SELECT * FROM (\n{sql}\n) AS sandbox_q LIMIT {config.SQL_SANDBOX_MAX_ROWS}", True


def _execute(sql: str) -> dict:
    engine = db.get_readonly_engine()
    try:
        conn = engine.connect()
    except OperationalError as exc:
        log.warning("sql sandbox read-only role unavailable: %s", exc)
        raise ApiError(503, "SANDBOX_UNAVAILABLE",
                       "read-only database role is not available (run seed_users.py to create it)")
    started = time.perf_counter()
    try:
        trans = conn.begin()
        try:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{config.SQL_SANDBOX_TIMEOUT}'")
            result = conn.exec_driver_sql(sql)
            columns = list(result.keys())
            rows = result.fetchmany(config.SQL_SANDBOX_MAX_ROWS + 1)
        finally:
            trans.rollback()  # always — belt and braces on top of the read-only role
    except DBAPIError as exc:
        orig = getattr(exc, "orig", None)
        pgcode = getattr(orig, "pgcode", None)
        message = (getattr(orig, "pgerror", None) or str(orig or exc)).strip().splitlines()[0]
        if pgcode == "57014":
            raise ApiError(400, "QUERY_TIMEOUT", f"query exceeded {config.SQL_SANDBOX_TIMEOUT} statement timeout")
        if pgcode == "42501":
            raise ApiError(403, "PERMISSION_DENIED", message)
        raise ApiError(400, "SQL_ERROR", message, {"pgcode": pgcode})
    finally:
        conn.close()

    truncated = len(rows) > config.SQL_SANDBOX_MAX_ROWS
    rows = rows[:config.SQL_SANDBOX_MAX_ROWS]
    return {
        "columns": columns,
        "rows": [[db.to_jsonable(v) for v in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


@router.get("", dependencies=[Depends(require_role(*READ_ROLES))])
def sandbox_info():
    return {
        "enabled": config.SQL_SANDBOX_ENABLED,
        "max_rows": config.SQL_SANDBOX_MAX_ROWS,
        "statement_timeout": config.SQL_SANDBOX_TIMEOUT,
        "examples": EXAMPLES,
    }


@router.post("")
def run_query(body: SandboxQuery, user: CurrentUser = Depends(require_role(*WRITE_ROLES))):
    if not config.SQL_SANDBOX_ENABLED:
        raise ApiError(404, "NOT_FOUND", "SQL sandbox is disabled")
    sql, limit_added = prepare_query(body.query)
    log.info("sql sandbox query by %s: %s", user.user_id, body.query[:500].replace("\n", " "))
    result = _execute(sql)
    result["limit_added"] = limit_added
    result["executed_sql"] = sql
    return result
