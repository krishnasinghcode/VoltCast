"""GET /api/v1/predictions/{vehicle_id}, GET /api/v1/risk/high | /medium | /low.

Risk lists are built from each vehicle's LATEST prediction only. If ML has not run yet the tables are empty
and these return empty lists, never errors (dashboardMaster.md §11).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from .. import db
from ..auth import READ_ROLES, require_role
from .vehicle_router import latest_predictions

router = APIRouter(prefix="/api/v1", tags=["predictions"], dependencies=[Depends(require_role(*READ_ROLES))])


@router.get("/predictions/{vehicle_id}")
def predictions_for_vehicle(vehicle_id: str, limit: int = Query(10, ge=1, le=100)):
    return latest_predictions(vehicle_id, limit)


def risk_list(level: str, limit: int, after: Optional[str], fleet_id: Optional[str]) -> dict:
    """Ordered worst-first: highest predicted 30-day degradation, then vehicle_id. Keyset over that pair."""
    cursor = db.decode_cursor(after, ("deg", "vehicle_id"))
    clauses, params = ["lp.risk_level = :level"], {"level": level, "lim": limit + 1}
    if fleet_id:
        clauses.append("v.fleet_id = :fleet_id")
        params["fleet_id"] = db.validate_uuid(fleet_id, "fleet_id")
    if cursor:
        clauses.append("(lp.predicted_degradation_30d_pct < :c_deg OR "
                       "(lp.predicted_degradation_30d_pct = :c_deg AND lp.vehicle_id > :c_vid))")
        params.update(c_deg=cursor["deg"], c_vid=cursor["vehicle_id"])
    sql = f"""
        WITH lp AS (
            SELECT * FROM (
                SELECT p.prediction_id, p.vehicle_id, p.predicted_at, p.current_soh,
                       p.predicted_degradation_30d_pct, p.predicted_soh_30d, p.risk_level, p.horizon_days,
                       p.model_version,
                       ROW_NUMBER() OVER (PARTITION BY p.vehicle_id ORDER BY p.predicted_at DESC) AS rn
                FROM battery_predictions p
            ) ranked WHERE rn = 1
        )
        SELECT lp.prediction_id, lp.vehicle_id, lp.predicted_at, lp.current_soh,
               lp.predicted_degradation_30d_pct, lp.predicted_soh_30d, lp.risk_level, lp.horizon_days,
               lp.model_version, v.fleet_id, f.name AS fleet_name, v.archetype
        FROM lp
        JOIN vehicles v ON v.vehicle_id = lp.vehicle_id
        JOIN fleets f ON f.fleet_id = v.fleet_id
        WHERE {' AND '.join(clauses)}
        ORDER BY lp.predicted_degradation_30d_pct DESC, lp.vehicle_id ASC
        LIMIT :lim
    """
    with db.get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    items = db.rows_to_dicts(rows[:limit])
    next_cursor = (db.encode_cursor({"deg": items[-1]["predicted_degradation_30d_pct"],
                                     "vehicle_id": items[-1]["vehicle_id"]})
                   if len(rows) > limit else None)
    return {"risk_level": level, "items": items, "next_cursor": next_cursor}


@router.get("/risk/high")
def high_risk(limit: Optional[int] = Query(None, ge=1, le=200), after: Optional[str] = None,
              fleet_id: Optional[str] = None):
    return risk_list("HIGH", db.clamp_limit(limit), after, fleet_id)


@router.get("/risk/medium")
def medium_risk(limit: Optional[int] = Query(None, ge=1, le=200), after: Optional[str] = None,
                fleet_id: Optional[str] = None):
    return risk_list("MEDIUM", db.clamp_limit(limit), after, fleet_id)


@router.get("/risk/low")
def low_risk(limit: Optional[int] = Query(None, ge=1, le=200), after: Optional[str] = None,
             fleet_id: Optional[str] = None):
    return risk_list("LOW", db.clamp_limit(limit), after, fleet_id)
