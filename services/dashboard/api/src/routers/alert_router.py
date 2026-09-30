"""GET /api/v1/alerts (keyset, filterable), GET /api/v1/alerts/{alert_id}, POST /api/v1/alerts/{id}/acknowledge.

Alerts are created by ingestion (REALTIME_RULE) and ML (ML_PREDICTION); this service only changes status.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from .. import db
from ..auth import READ_ROLES, WRITE_ROLES, CurrentUser, require_role
from ..errors import ApiError

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

STATUSES = ("OPEN", "ACKNOWLEDGED", "RESOLVED")
SEVERITIES = ("INFO", "WARNING", "CRITICAL")
SOURCES = ("REALTIME_RULE", "ML_PREDICTION")

_COLUMNS = "a.alert_id, a.vehicle_id, a.created_at, a.alert_type, a.severity, a.status, a.source, a.prediction_id"


def _enum(value: Optional[str], allowed: tuple[str, ...], field: str) -> Optional[str]:
    if not value:
        return None
    value = value.upper()
    if value not in allowed:
        raise ApiError(400, "VALIDATION_ERROR", f"{field} must be one of {list(allowed)}", {"field": field})
    return value


def list_alerts_query(status: Optional[str] = None, severity: Optional[str] = None,
                      vehicle_id: Optional[str] = None, alert_type: Optional[str] = None,
                      source: Optional[str] = None, fleet_id: Optional[str] = None,
                      limit: int = 50, after: Optional[str] = None) -> dict:
    cursor = db.decode_cursor(after, ("created_at", "alert_id"))
    clauses, params = [], {"lim": limit + 1}
    join = ""
    for field, value, allowed in (("status", status, STATUSES), ("severity", severity, SEVERITIES),
                                  ("source", source, SOURCES)):
        value = _enum(value, allowed, field)
        if value:
            clauses.append(f"a.{field} = :{field}")
            params[field] = value
    if vehicle_id:
        clauses.append("a.vehicle_id = :vehicle_id")
        params["vehicle_id"] = vehicle_id
    if alert_type:
        clauses.append("a.alert_type = :alert_type")
        params["alert_type"] = alert_type.upper()
    if fleet_id:
        join = "JOIN vehicles v ON v.vehicle_id = a.vehicle_id"
        clauses.append("v.fleet_id = :fleet_id")
        params["fleet_id"] = db.validate_uuid(fleet_id, "fleet_id")
    if cursor:
        clauses.append("(a.created_at < :c_ts OR (a.created_at = :c_ts AND a.alert_id < :c_id))")
        params.update(c_ts=cursor["created_at"], c_id=cursor["alert_id"])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db.get_engine().connect() as conn:
        rows = conn.execute(text(f"""
            SELECT {_COLUMNS} FROM alerts a {join} {where}
            ORDER BY a.created_at DESC, a.alert_id DESC
            LIMIT :lim
        """), params).fetchall()
    items = db.rows_to_dicts(rows[:limit])
    next_cursor = (db.encode_cursor({"created_at": items[-1]["created_at"], "alert_id": items[-1]["alert_id"]})
                   if len(rows) > limit else None)
    return {"items": items, "next_cursor": next_cursor}


@router.get("", dependencies=[Depends(require_role(*READ_ROLES))])
def list_alerts(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    vehicle_id: Optional[str] = None,
    alert_type: Optional[str] = None,
    source: Optional[str] = None,
    fleet_id: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=200),
    after: Optional[str] = None,
):
    return list_alerts_query(status, severity, vehicle_id, alert_type, source, fleet_id,
                             db.clamp_limit(limit), after)


def _get_alert(conn, alert_id: str):
    return conn.execute(text(f"SELECT {_COLUMNS} FROM alerts a WHERE a.alert_id = :id"), {"id": alert_id}).first()


@router.get("/{alert_id}", dependencies=[Depends(require_role(*READ_ROLES))])
def get_alert(alert_id: str):
    alert_id = db.validate_uuid(alert_id, "alert_id")
    with db.get_engine().connect() as conn:
        row = _get_alert(conn, alert_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", f"alert {alert_id} not found")
    return db.row_to_dict(row)


@router.post("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str, user: CurrentUser = Depends(require_role(*WRITE_ROLES))):
    alert_id = db.validate_uuid(alert_id, "alert_id")
    with db.get_engine().begin() as conn:
        result = conn.execute(text(
            "UPDATE alerts SET status = 'ACKNOWLEDGED' WHERE alert_id = :id AND status = 'OPEN'"
        ), {"id": alert_id})
        row = _get_alert(conn, alert_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", f"alert {alert_id} not found")
    if result.rowcount == 0:
        raise ApiError(409, "INVALID_STATE", f"alert is already {row.status}", {"status": row.status})
    body = db.row_to_dict(row)
    body["acknowledged_by"] = user.user_id
    return body
