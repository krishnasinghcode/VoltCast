"""Thin proxy to the ML service (CONTRACT.md §2, port 8003). Train/predict are FLEET_MANAGER+ only.

POST /api/v1/ml/train                 -> ML POST /api/v1/train  {"simulation_id", "horizon_days"}
GET  /api/v1/ml/train/status          -> ML GET  /api/v1/train/status (MAE model vs baseline)
POST /api/v1/ml/predict               -> ML POST /api/v1/predict  {"simulation_id"?}
POST /api/v1/ml/predict/{vehicle_id}  -> ML POST /api/v1/predict/{vehicle_id}
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from .. import config
from ..auth import READ_ROLES, WRITE_ROLES, require_role
from ..upstream import proxy

router = APIRouter(prefix="/api/v1/ml", tags=["ml"])
SERVICE = "ml"


class TrainRequest(BaseModel):
    simulation_id: str
    horizon_days: int = Field(30, ge=1, le=365)


class PredictRequest(BaseModel):
    simulation_id: Optional[str] = None


@router.post("/train", dependencies=[Depends(require_role(*WRITE_ROLES))])
async def train(body: TrainRequest):
    return await proxy("POST", config.ML_URL, "/api/v1/train", SERVICE, json_body=body.model_dump(),
                       timeout=config.ML_TRAIN_TIMEOUT_SECONDS)


@router.get("/train/status", dependencies=[Depends(require_role(*READ_ROLES))])
async def train_status():
    return await proxy("GET", config.ML_URL, "/api/v1/train/status", SERVICE,
                       timeout=config.UPSTREAM_TIMEOUT_SECONDS)


@router.post("/predict", dependencies=[Depends(require_role(*WRITE_ROLES))])
async def predict(body: Optional[PredictRequest] = Body(None)):
    payload = (body or PredictRequest()).model_dump()
    return await proxy("POST", config.ML_URL, "/api/v1/predict", SERVICE, json_body=payload,
                       timeout=config.ML_TRAIN_TIMEOUT_SECONDS)


@router.post("/predict/{vehicle_id}", dependencies=[Depends(require_role(*WRITE_ROLES))])
async def predict_vehicle(vehicle_id: str):
    return await proxy("POST", config.ML_URL, f"/api/v1/predict/{vehicle_id}", SERVICE,
                       timeout=config.UPSTREAM_TIMEOUT_SECONDS * 4)
