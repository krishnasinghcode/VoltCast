"""FastAPI app exposing the data-gen control API (CONTRACT.md §8 HTTP conventions)."""
from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from src import db, fleet
from src.config import CONTRACT_VERSION, DEFAULT_ACTIVE_VEHICLE_COUNT
from src.models import SimulationConfig, SimulationMode, SimulationStatus
from src.simulation_runner import RUNNERS, SimulationRunner

app = FastAPI(title="data-gen", version=CONTRACT_VERSION)


def error_response(status_code: int, code: str, message: str, details: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


@app.on_event("startup")
def on_startup() -> None:
    db.init_db()


@app.get("/api/v1/health")
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "contract_version": CONTRACT_VERSION}


@app.post("/api/v1/simulation")
def create_simulation(config: SimulationConfig):
    if config.active_vehicle_count is None:
        config.active_vehicle_count = min(DEFAULT_ACTIVE_VEHICLE_COUNT, config.vehicle_count)
    if config.active_vehicle_count > config.vehicle_count:
        return error_response(
            400, "VALIDATION_ERROR",
            "active_vehicle_count must be <= vehicle_count",
        )

    with db.session() as conn:
        existing_count = db.count_vehicles(conn)
    if config.mode == SimulationMode.DEMO and 0 < existing_count < config.vehicle_count:
        config.vehicle_count = existing_count
        if config.active_vehicle_count > existing_count:
            config.active_vehicle_count = existing_count
    if 0 < existing_count < config.vehicle_count:
        return error_response(
            400, "VALIDATION_ERROR",
            f"vehicle_count ({config.vehicle_count}) exceeds the {existing_count} vehicles already "
            "in the database — truncate the fleet tables first, or use vehicle_count <= "
            f"{existing_count}.",
        )

    statics = fleet.build_or_load_fleet(
        config.vehicle_count, config.seed, config.archetype_distribution, config.start_time
    )

    simulation_id = str(uuid.uuid4())
    with db.session() as conn:
        db.insert_simulation(
            conn, simulation_id, config.seed, config.mode.value, config.start_time,
            config.duration_days, config.simulation_speed,
        )

    runner = SimulationRunner(simulation_id, config, statics)
    RUNNERS[simulation_id] = runner

    return {
        "simulation_id": simulation_id,
        "seed": config.seed,
        "mode": config.mode.value,
        "status": SimulationStatus.CREATED.value,
        "start_time": config.start_time,
        "duration_days": config.duration_days,
        "simulation_speed": config.simulation_speed,
    }


def _get_runner(simulation_id: str) -> SimulationRunner:
    runner = RUNNERS.get(simulation_id)
    if runner is None:
        raise HTTPException(status_code=404, detail={
            "error": {"code": "NOT_FOUND", "message": f"simulation {simulation_id} not found", "details": {}}
        })
    return runner


@app.post("/api/v1/simulation/{simulation_id}/start")
async def start_simulation(simulation_id: str):
    runner = _get_runner(simulation_id)
    await runner.start()
    return runner.status_payload()


@app.post("/api/v1/simulation/{simulation_id}/pause")
async def pause_simulation(simulation_id: str):
    runner = _get_runner(simulation_id)
    await runner.pause()
    return runner.status_payload()


@app.post("/api/v1/simulation/{simulation_id}/resume")
async def resume_simulation(simulation_id: str):
    runner = _get_runner(simulation_id)
    await runner.resume()
    return runner.status_payload()


@app.post("/api/v1/simulation/{simulation_id}/reset")
async def reset_simulation(simulation_id: str):
    runner = _get_runner(simulation_id)
    await runner.reset()
    return runner.status_payload()


@app.get("/api/v1/simulation/{simulation_id}")
def get_simulation_status(simulation_id: str):
    runner = _get_runner(simulation_id)
    return runner.status_payload()


@app.get("/api/v1/simulations")
def list_simulations(limit: int = 50, after: Optional[str] = None):
    with db.session() as conn:
        rows = db.list_simulations(conn, limit, after)
    items = [dict(r) for r in rows]
    next_cursor = items[-1]["simulation_id"] if len(items) == limit else None
    return {"items": items, "next_cursor": str(next_cursor) if next_cursor else None}
