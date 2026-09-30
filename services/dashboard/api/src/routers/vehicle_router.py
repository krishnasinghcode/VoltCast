"""Vehicle search, detail, history and predictions.

GET /api/v1/vehicles                              keyset-paginated search (vehicle_id substring + filters)
GET /api/v1/vehicles/{vehicle_id}                 static info + current state (Redis first, Postgres fallback)
GET /api/v1/vehicles/{vehicle_id}/history         agg_daily / agg_hourly / agg_5min, newest first, keyset
GET /api/v1/vehicles/{vehicle_id}/predictions     latest N battery_predictions rows
GET /api/v1/vehicles/{vehicle_id}/charging-sessions  latest charging sessions (keyset)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from .. import db
from ..auth import READ_ROLES, require_role
from ..errors import ApiError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/vehicles", tags=["vehicles"], dependencies=[Depends(require_role(*READ_ROLES))])

ARCHETYPES = ("HEALTHY", "NORMAL", "STRESSED", "BAD")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")

_LATEST_RISK = ("(SELECT p.risk_level FROM battery_predictions p WHERE p.vehicle_id = v.vehicle_id "
                "ORDER BY p.predicted_at DESC LIMIT 1)")
_LATEST_PRED_SOH = ("(SELECT p.predicted_soh_30d FROM battery_predictions p WHERE p.vehicle_id = v.vehicle_id "
                    "ORDER BY p.predicted_at DESC LIMIT 1)")
# agg_daily has an index on (vehicle_id, date) — the cheap way to get a recent SOH for list rows.
_LATEST_SOH = ("(SELECT d.soh_pct FROM agg_daily d WHERE d.vehicle_id = v.vehicle_id AND d.soh_pct IS NOT NULL "
               "ORDER BY d.date DESC LIMIT 1)")

_HISTORY_TABLES = {
    "daily": ("agg_daily", "date"),
    "hourly": ("agg_hourly", "hour_start"),
    "5min": ("agg_5min", "window_start"),
}


def _like_pattern(search: str) -> str:
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _parse_ts(value: Optional[str], field: str) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "VALIDATION_ERROR", f"{field} must be an ISO-8601 date or timestamp", {"field": field})
    return value


def _vehicle_exists(conn, vehicle_id: str) -> bool:
    return conn.execute(text("SELECT 1 FROM vehicles WHERE vehicle_id = :vid"), {"vid": vehicle_id}).first() is not None


@router.get("")
def search_vehicles(
    search: Optional[str] = Query(None, max_length=64),
    fleet_id: Optional[str] = None,
    archetype: Optional[str] = None,
    risk_level: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=200),
    after: Optional[str] = None,
):
    limit = db.clamp_limit(limit)
    cursor = db.decode_cursor(after, ("vehicle_id",))
    clauses, params = [], {"lim": limit + 1}

    if search and search.strip():
        clauses.append("v.vehicle_id LIKE :pattern ESCAPE '\\'")
        params["pattern"] = _like_pattern(search.strip().upper())
    if fleet_id:
        clauses.append("v.fleet_id = :fleet_id")
        params["fleet_id"] = db.validate_uuid(fleet_id, "fleet_id")
    if archetype:
        archetype = archetype.upper()
        if archetype not in ARCHETYPES:
            raise ApiError(400, "VALIDATION_ERROR", f"archetype must be one of {list(ARCHETYPES)}")
        clauses.append("v.archetype = :archetype")
        params["archetype"] = archetype
    if risk_level:
        risk_level = risk_level.upper()
        if risk_level not in RISK_LEVELS:
            raise ApiError(400, "VALIDATION_ERROR", f"risk_level must be one of {list(RISK_LEVELS)}")
        clauses.append(f"{_LATEST_RISK} = :risk_level")
        params["risk_level"] = risk_level
    if cursor:
        clauses.append("v.vehicle_id > :after_id")
        params["after_id"] = cursor["vehicle_id"]

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT v.vehicle_id, v.fleet_id, f.name AS fleet_name, f.region, v.archetype, v.status,
               v.manufacture_date, m.manufacturer, m.model_name, m.model_year,
               {_LATEST_RISK} AS risk_level,
               {_LATEST_PRED_SOH} AS predicted_soh_30d,
               {_LATEST_SOH} AS soh_pct
        FROM vehicles v
        JOIN fleets f ON f.fleet_id = v.fleet_id
        JOIN vehicle_models m ON m.model_id = v.model_id
        {where}
        ORDER BY v.vehicle_id
        LIMIT :lim
    """
    with db.get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    items = db.rows_to_dicts(rows[:limit])
    next_cursor = db.encode_cursor({"vehicle_id": items[-1]["vehicle_id"]}) if len(rows) > limit else None
    return {"items": items, "next_cursor": next_cursor}


def _redis_state(vehicle_id: str) -> tuple[dict, list[str]]:
    try:
        r = db.get_redis()
        pipe = r.pipeline(transaction=False)
        pipe.hgetall(f"vehicle:state:{vehicle_id}")
        pipe.smembers(f"vehicle:alert_flags:{vehicle_id}")
        state, flags = pipe.execute()
        return state or {}, sorted(flags or [])
    except Exception as exc:
        log.warning("redis unavailable for vehicle state %s: %s", vehicle_id, exc)
        return {}, []


def parse_redis_state(raw: dict) -> dict:
    """Redis stores everything as strings; coerce the ingestion-written hash back to typed values."""
    out: dict = {}
    for key, value in raw.items():
        if key == "is_charging":
            out[key] = str(value).lower() in ("1", "true")
        elif key == "last_updated":
            out[key] = value
        else:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                out[key] = value
    return out


@router.get("/{vehicle_id}")
def vehicle_detail(vehicle_id: str):
    with db.get_engine().connect() as conn:
        static = conn.execute(text("""
            SELECT v.vehicle_id, v.fleet_id, f.name AS fleet_name, f.region, v.archetype, v.status,
                   v.manufacture_date, v.initial_odometer_km, v.created_at,
                   m.manufacturer, m.model_name, m.model_year,
                   b.battery_id, b.nominal_capacity_kwh, b.rated_voltage_v, b.installation_date,
                   c.name AS chemistry
            FROM vehicles v
            JOIN fleets f ON f.fleet_id = v.fleet_id
            JOIN vehicle_models m ON m.model_id = v.model_id
            JOIN batteries b ON b.battery_id = v.battery_id
            JOIN battery_chemistries c ON c.chemistry_id = b.chemistry_id
            WHERE v.vehicle_id = :vid
        """), {"vid": vehicle_id}).first()
        if static is None:
            raise ApiError(404, "NOT_FOUND", f"vehicle {vehicle_id} not found")

        ground_truth = conn.execute(text("""
            SELECT simulation_id, sim_timestamp, soc_pct, usable_capacity_kwh, soh_pct, odometer_km,
                   equivalent_full_cycles, battery_temp_c, thermal_exposure_hours, fast_charge_count,
                   deep_discharge_count
            FROM simulation_vehicle_state WHERE vehicle_id = :vid
            ORDER BY sim_timestamp DESC LIMIT 1
        """), {"vid": vehicle_id}).first()

        latest_daily_soh = None
        if ground_truth is None:
            latest_daily_soh = conn.execute(text("""
                SELECT soh_pct FROM agg_daily WHERE vehicle_id = :vid AND soh_pct IS NOT NULL
                ORDER BY date DESC LIMIT 1
            """), {"vid": vehicle_id}).scalar()

        prediction = conn.execute(text("""
            SELECT prediction_id, predicted_at, current_soh, predicted_degradation_30d_pct, predicted_soh_30d,
                   risk_level, horizon_days, model_version
            FROM battery_predictions WHERE vehicle_id = :vid
            ORDER BY predicted_at DESC LIMIT 1
        """), {"vid": vehicle_id}).first()

        open_alerts = conn.execute(text(
            "SELECT COUNT(*) FROM alerts WHERE vehicle_id = :vid AND status = 'OPEN'"
        ), {"vid": vehicle_id}).scalar()

    raw_state, alert_flags = _redis_state(vehicle_id)
    gt = db.row_to_dict(ground_truth) if ground_truth is not None else None

    if raw_state:
        live = parse_redis_state(raw_state)
        current = {
            "source": "redis",
            "soc_pct": live.get("soc_pct"),
            "temperature_c": live.get("temperature_c"),
            "is_charging": live.get("is_charging"),
            "odometer_km": live.get("odometer_km"),
            "last_updated": live.get("last_updated"),
            # ingestion's latest-state hash has no SOH (it's data-gen's ground truth) — take it from Postgres
            "soh_pct": live.get("soh") or live.get("soh_pct") or (gt or {}).get("soh_pct")
                       or db.to_jsonable(latest_daily_soh),
        }
    elif gt is not None:
        current = {
            "source": "postgres",
            "soc_pct": gt["soc_pct"],
            "temperature_c": gt["battery_temp_c"],
            "is_charging": None,
            "odometer_km": gt["odometer_km"],
            "last_updated": gt["sim_timestamp"],
            "soh_pct": gt["soh_pct"],
        }
    else:
        current = {"source": "none", "soc_pct": None, "temperature_c": None, "is_charging": None,
                   "odometer_km": None, "last_updated": None, "soh_pct": db.to_jsonable(latest_daily_soh)}

    return {
        "vehicle": db.row_to_dict(static),
        "current_state": current,
        "ground_truth": gt,
        "active_alert_flags": alert_flags,
        "open_alert_count": int(open_alerts or 0),
        "latest_prediction": db.row_to_dict(prediction) if prediction is not None else None,
    }


@router.get("/{vehicle_id}/history")
def vehicle_history(
    vehicle_id: str,
    resolution: Literal["daily", "hourly", "5min"] = "daily",
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=2000),
    after: Optional[str] = None,
):
    """Newest-first so the first page is always the most recent window (simulated time is arbitrary, the
    client can't guess a sensible `from`). Charts reverse the page client-side."""
    table, time_col = _HISTORY_TABLES[resolution]
    limit = db.clamp_limit(limit, default=500, maximum=2000)
    cursor = db.decode_cursor(after, ("t",))
    clauses, params = ["vehicle_id = :vid"], {"vid": vehicle_id, "lim": limit + 1}
    start, end = _parse_ts(from_, "from"), _parse_ts(to, "to")
    if start:
        clauses.append(f"{time_col} >= :start")
        params["start"] = start
    if end:
        clauses.append(f"{time_col} <= :end")
        params["end"] = end
    if cursor:
        clauses.append(f"{time_col} < :after_t")
        params["after_t"] = cursor["t"]

    with db.get_engine().connect() as conn:
        if not _vehicle_exists(conn, vehicle_id):
            raise ApiError(404, "NOT_FOUND", f"vehicle {vehicle_id} not found")
        rows = conn.execute(text(
            f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} ORDER BY {time_col} DESC LIMIT :lim"
        ), params).fetchall()

    items = db.rows_to_dicts(rows[:limit])
    next_cursor = db.encode_cursor({"t": items[-1][time_col]}) if len(rows) > limit else None
    return {"vehicle_id": vehicle_id, "resolution": resolution, "time_field": time_col,
            "items": items, "next_cursor": next_cursor}


def latest_predictions(vehicle_id: str, limit: int) -> dict:
    with db.get_engine().connect() as conn:
        if not _vehicle_exists(conn, vehicle_id):
            raise ApiError(404, "NOT_FOUND", f"vehicle {vehicle_id} not found")
        rows = conn.execute(text("""
            SELECT prediction_id, vehicle_id, predicted_at, current_soh, predicted_degradation_30d_pct,
                   predicted_soh_30d, risk_level, horizon_days, model_version
            FROM battery_predictions WHERE vehicle_id = :vid
            ORDER BY predicted_at DESC LIMIT :lim
        """), {"vid": vehicle_id, "lim": limit}).fetchall()
    return {"vehicle_id": vehicle_id, "items": db.rows_to_dicts(rows)}


@router.get("/{vehicle_id}/predictions")
def vehicle_predictions(vehicle_id: str, limit: int = Query(10, ge=1, le=100)):
    return latest_predictions(vehicle_id, limit)


@router.get("/{vehicle_id}/charging-sessions")
def vehicle_charging_sessions(vehicle_id: str, limit: Optional[int] = Query(None, ge=1, le=200),
                              after: Optional[str] = None):
    limit = db.clamp_limit(limit, default=20)
    cursor = db.decode_cursor(after, ("started_at", "session_id"))
    clauses, params = ["vehicle_id = :vid"], {"vid": vehicle_id, "lim": limit + 1}
    if cursor:
        clauses.append("(started_at < :c_ts OR (started_at = :c_ts AND session_id < :c_id))")
        params.update(c_ts=cursor["started_at"], c_id=cursor["session_id"])
    with db.get_engine().connect() as conn:
        if not _vehicle_exists(conn, vehicle_id):
            raise ApiError(404, "NOT_FOUND", f"vehicle {vehicle_id} not found")
        rows = conn.execute(text(f"""
            SELECT session_id, vehicle_id, started_at, ended_at, start_soc, end_soc, energy_delivered_kwh,
                   max_charging_power_kw, charging_type
            FROM charging_sessions WHERE {' AND '.join(clauses)}
            ORDER BY started_at DESC, session_id DESC LIMIT :lim
        """), params).fetchall()
    items = db.rows_to_dicts(rows[:limit])
    next_cursor = (db.encode_cursor({"started_at": items[-1]["started_at"], "session_id": items[-1]["session_id"]})
                   if len(rows) > limit else None)
    return {"items": items, "next_cursor": next_cursor}
