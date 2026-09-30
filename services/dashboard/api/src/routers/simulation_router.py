"""Thin proxy to data-gen's control API (CONTRACT.md §2, port 8001). Mutations are FLEET_MANAGER+ only.

POST /api/v1/simulation                    -> data-gen POST /api/v1/simulation
POST /api/v1/simulation/{id}/start|pause|resume|reset
GET  /api/v1/simulation/{id}               -> status payload (progress, events produced, sim time)
GET  /api/v1/simulations                   -> list (keyset, data-gen's own cursor)
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Body, Depends, Query

from .. import config
from ..auth import READ_ROLES, WRITE_ROLES, require_role
from ..upstream import proxy

router = APIRouter(prefix="/api/v1", tags=["simulation"])
SERVICE = "data-gen"


@router.post("/simulation", dependencies=[Depends(require_role(*WRITE_ROLES))])
async def create_simulation(body: dict[str, Any] = Body(...)):
    # data-gen validates the SimulationConfig (seed, mode, vehicle_count, start_time, ...) itself.
    return await proxy("POST", config.DATA_GEN_URL, "/api/v1/simulation", SERVICE, json_body=body,
                       timeout=max(config.UPSTREAM_TIMEOUT_SECONDS, 120))


@router.post("/simulation/{simulation_id}/{action}", dependencies=[Depends(require_role(*WRITE_ROLES))])
async def control_simulation(simulation_id: str, action: Literal["start", "pause", "resume", "reset"]):
    return await proxy("POST", config.DATA_GEN_URL, f"/api/v1/simulation/{simulation_id}/{action}", SERVICE,
                       timeout=config.UPSTREAM_TIMEOUT_SECONDS)


@router.get("/simulation/{simulation_id}", dependencies=[Depends(require_role(*READ_ROLES))])
async def get_simulation(simulation_id: str):
    return await proxy("GET", config.DATA_GEN_URL, f"/api/v1/simulation/{simulation_id}", SERVICE,
                       timeout=config.UPSTREAM_TIMEOUT_SECONDS)


@router.get("/simulations", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_simulations(limit: int = Query(50, ge=1, le=200), after: Optional[str] = None):
    params = {"limit": limit}
    if after:
        params["after"] = after
    return await proxy("GET", config.DATA_GEN_URL, "/api/v1/simulations", SERVICE, params=params,
                       timeout=config.UPSTREAM_TIMEOUT_SECONDS)
