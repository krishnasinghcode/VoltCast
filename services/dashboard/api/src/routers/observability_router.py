"""GET /api/v1/observability — merges ingestion /metrics, this service's own request counters, fleet counts,
and a /health sweep of every peer (contract_version check, CONTRACT.md header: a mismatch is a hard bug).

Response shape is dashboardMaster.md §8, plus `services` and `generated_at`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from .. import config, db
from ..auth import READ_ROLES, require_role
from ..metrics import request_metrics
from ..upstream import get_json

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["observability"], dependencies=[Depends(require_role(*READ_ROLES))])

_INGESTION_KEYS = ("events_consumed_total", "events_deduplicated_total", "events_rejected_total",
                   "late_events_total", "consumer_lag", "current_watermark_timestamp")


def _fleet_counts() -> dict:
    try:
        with db.get_engine().connect() as conn:
            vehicle_count = conn.execute(text("SELECT COUNT(*) FROM vehicles")).scalar()
            active_alerts = conn.execute(text("SELECT COUNT(*) FROM alerts WHERE status = 'OPEN'")).scalar()
        return {"vehicle_count": int(vehicle_count or 0), "active_alerts": int(active_alerts or 0)}
    except Exception as exc:
        log.warning("observability fleet counts failed: %s", exc)
        return {"vehicle_count": None, "active_alerts": None, "error": "postgres unavailable"}


def _redis_ok() -> bool:
    try:
        return bool(db.get_redis().ping())
    except Exception:
        return False


# Peer /health results are cached briefly: the dashboard polls every few seconds, and a down peer would
# otherwise add its full connect timeout to every single observability request.
_HEALTH_TTL_S = 10.0
_health_cache: dict[str, tuple[float, dict]] = {}


async def _health(name: str, base_url: str) -> dict:
    cached = _health_cache.get(name)
    if cached and time.monotonic() - cached[0] < _HEALTH_TTL_S:
        return cached[1]
    result = await _probe(name, base_url)
    _health_cache[name] = (time.monotonic(), result)
    return result


async def _probe(name: str, base_url: str) -> dict:
    started = time.perf_counter()
    payload, err = await get_json(f"{base_url}/health", timeout=1.0)
    latency = round((time.perf_counter() - started) * 1000, 1)
    if payload is None:
        return {"name": name, "status": "down", "error": err, "latency_ms": latency, "contract_version": None,
                "contract_ok": False}
    version = payload.get("contract_version") if isinstance(payload, dict) else None
    return {"name": name, "status": payload.get("status", "unknown") if isinstance(payload, dict) else "unknown",
            "latency_ms": latency, "contract_version": version, "contract_ok": version == config.CONTRACT_VERSION}


@router.get("/observability")
async def observability():
    ingestion_task = get_json(f"{config.INGESTION_URL}/metrics", timeout=1.5)
    health_tasks = [_health("data-gen", config.DATA_GEN_URL), _health("ingestion", config.INGESTION_URL),
                    _health("ml", config.ML_URL)]
    (ingestion_payload, ingestion_err), *peer_health = await asyncio.gather(ingestion_task, *health_tasks)
    fleet, redis_ok, api_metrics = await asyncio.gather(
        run_in_threadpool(_fleet_counts), run_in_threadpool(_redis_ok),
        run_in_threadpool(request_metrics.cluster_snapshot, db.get_redis()))

    ingestion = {k: (ingestion_payload or {}).get(k) for k in _INGESTION_KEYS}
    ingestion["reachable"] = ingestion_payload is not None
    if ingestion_err:
        ingestion["error"] = ingestion_err

    services = [{"name": "api", "status": "ok", "latency_ms": 0.0, "contract_version": config.CONTRACT_VERSION,
                 "contract_ok": True}, *peer_health]
    services.append({"name": "postgres", "status": "ok" if fleet.get("vehicle_count") is not None else "down"})
    services.append({"name": "redis", "status": "ok" if redis_ok else "down"})

    return {
        "ingestion": ingestion,
        "api": api_metrics,
        "fleet": fleet,
        "services": services,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
