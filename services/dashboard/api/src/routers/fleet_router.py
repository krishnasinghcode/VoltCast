"""GET /api/v1/fleets, GET /api/v1/fleets/summary, GET /api/v1/fleets/{fleet_id}/summary.

Summaries are cached in Redis for 10 s (CONTRACT.md §6). The all-fleets summary uses the exact contract key
`cache:fleet_summary`; per-fleet summaries use `cache:fleet_summary:<fleet_id>` (same namespace, one key per
fleet — a single STRING cannot hold several independently-expiring summaries).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from .. import config, db
from ..auth import READ_ROLES, require_role
from ..errors import ApiError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/fleets", tags=["fleets"], dependencies=[Depends(require_role(*READ_ROLES))])

CACHE_KEY = "cache:fleet_summary"

# Latest prediction per vehicle. ROW_NUMBER works on Postgres and SQLite alike.
_LATEST_PREDICTION_CTE = """
latest_pred AS (
    SELECT vehicle_id, risk_level FROM (
        SELECT p.vehicle_id, p.risk_level,
               ROW_NUMBER() OVER (PARTITION BY p.vehicle_id ORDER BY p.predicted_at DESC) AS rn
        FROM battery_predictions p
    ) ranked WHERE rn = 1
)"""

# Latest ground-truth state per vehicle (simulation_vehicle_state has one row per simulation × vehicle).
_LATEST_STATE_CTE = """
latest_state AS (
    SELECT vehicle_id, soh_pct, soc_pct FROM (
        SELECT s.vehicle_id, s.soh_pct, s.soc_pct,
               ROW_NUMBER() OVER (PARTITION BY s.vehicle_id ORDER BY s.sim_timestamp DESC) AS rn
        FROM simulation_vehicle_state s
    ) ranked WHERE rn = 1
)"""


def _cache_key(fleet_id: Optional[str]) -> str:
    return f"{CACHE_KEY}:{fleet_id}" if fleet_id else CACHE_KEY


def _cache_get(key: str) -> Optional[dict]:
    try:
        raw = db.get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        log.warning("fleet summary cache read failed: %s", exc)
        return None


def _cache_set(key: str, value: dict) -> None:
    try:
        db.get_redis().set(key, json.dumps(value), ex=config.FLEET_SUMMARY_TTL_SECONDS)
    except Exception as exc:
        log.warning("fleet summary cache write failed: %s", exc)


def _compute_summary(fleet_id: Optional[str]) -> dict:
    fleet_filter = "WHERE v.fleet_id = :fleet_id" if fleet_id else ""
    params = {"fleet_id": fleet_id} if fleet_id else {}
    with db.get_engine().connect() as conn:
        fleet_name = None
        if fleet_id:
            fleet = conn.execute(text("SELECT name, region FROM fleets WHERE fleet_id = :fleet_id"), params).first()
            if fleet is None:
                raise ApiError(404, "NOT_FOUND", f"fleet {fleet_id} not found")
            fleet_name = fleet.name

        agg = conn.execute(text(f"""
            WITH {_LATEST_PREDICTION_CTE}, {_LATEST_STATE_CTE}
            SELECT
                COUNT(*)                                                   AS vehicle_count,
                AVG(ls.soh_pct)                                            AS avg_soh_pct,
                MIN(ls.soh_pct)                                            AS min_soh_pct,
                AVG(ls.soc_pct)                                            AS avg_soc_pct,
                SUM(CASE WHEN lp.risk_level = 'LOW' THEN 1 ELSE 0 END)     AS risk_low,
                SUM(CASE WHEN lp.risk_level = 'MEDIUM' THEN 1 ELSE 0 END)  AS risk_medium,
                SUM(CASE WHEN lp.risk_level = 'HIGH' THEN 1 ELSE 0 END)    AS risk_high,
                SUM(CASE WHEN lp.risk_level IS NULL THEN 1 ELSE 0 END)     AS risk_unpredicted,
                SUM(CASE WHEN v.archetype = 'HEALTHY' THEN 1 ELSE 0 END)   AS arch_healthy,
                SUM(CASE WHEN v.archetype = 'NORMAL' THEN 1 ELSE 0 END)    AS arch_normal,
                SUM(CASE WHEN v.archetype = 'STRESSED' THEN 1 ELSE 0 END)  AS arch_stressed,
                SUM(CASE WHEN v.archetype = 'BAD' THEN 1 ELSE 0 END)       AS arch_bad
            FROM vehicles v
            LEFT JOIN latest_pred lp ON lp.vehicle_id = v.vehicle_id
            LEFT JOIN latest_state ls ON ls.vehicle_id = v.vehicle_id
            {fleet_filter}
        """), params).first()

        alert_join = "JOIN vehicles v ON v.vehicle_id = a.vehicle_id AND v.fleet_id = :fleet_id" if fleet_id else ""
        alerts = conn.execute(text(f"""
            SELECT
                SUM(CASE WHEN a.status = 'OPEN' THEN 1 ELSE 0 END)                             AS active_alerts,
                SUM(CASE WHEN a.status = 'OPEN' AND a.severity = 'CRITICAL' THEN 1 ELSE 0 END) AS critical_alerts
            FROM alerts a {alert_join}
        """), params).first()

    def _num(v, digits=2):
        return round(float(v), digits) if v is not None else None

    return {
        "fleet_id": fleet_id,
        "fleet_name": fleet_name,
        "vehicle_count": int(agg.vehicle_count or 0),
        "avg_soh_pct": _num(agg.avg_soh_pct),
        "min_soh_pct": _num(agg.min_soh_pct),
        "avg_soc_pct": _num(agg.avg_soc_pct),
        "risk_breakdown": {
            "LOW": int(agg.risk_low or 0),
            "MEDIUM": int(agg.risk_medium or 0),
            "HIGH": int(agg.risk_high or 0),
            "UNPREDICTED": int(agg.risk_unpredicted or 0),
        },
        "archetype_breakdown": {
            "HEALTHY": int(agg.arch_healthy or 0),
            "NORMAL": int(agg.arch_normal or 0),
            "STRESSED": int(agg.arch_stressed or 0),
            "BAD": int(agg.arch_bad or 0),
        },
        "active_alerts": int(alerts.active_alerts or 0),
        "critical_alerts": int(alerts.critical_alerts or 0),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_fleet_summary(fleet_id: Optional[str] = None) -> dict:
    """Cache-aside summary; shared with the /ws/live broadcaster."""
    key = _cache_key(fleet_id)
    cached = _cache_get(key)
    if cached is not None:
        cached["cached"] = True
        return cached
    summary = _compute_summary(fleet_id)
    _cache_set(key, summary)
    summary["cached"] = False
    return summary


@router.get("")
def list_fleets(limit: Optional[int] = Query(None, ge=1, le=200), after: Optional[str] = None):
    limit = db.clamp_limit(limit)
    cursor = db.decode_cursor(after, ("fleet_id",))
    where = "WHERE f.fleet_id > :after_id" if cursor else ""
    params = {"lim": limit + 1}
    if cursor:
        params["after_id"] = cursor["fleet_id"]
    with db.get_engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT f.fleet_id, f.name, f.region, f.created_at,
                   (SELECT COUNT(*) FROM vehicles v WHERE v.fleet_id = f.fleet_id) AS vehicle_count
            FROM fleets f {where}
            ORDER BY f.fleet_id
            LIMIT :lim
        """), params).fetchall()
    items = db.rows_to_dicts(rows[:limit])
    next_cursor = db.encode_cursor({"fleet_id": items[-1]["fleet_id"]}) if len(rows) > limit else None
    return {"items": items, "next_cursor": next_cursor}


@router.get("/summary")
def all_fleets_summary():
    return get_fleet_summary(None)


@router.get("/{fleet_id}/summary")
def fleet_summary(fleet_id: str):
    return get_fleet_summary(db.validate_uuid(fleet_id, "fleet_id"))
