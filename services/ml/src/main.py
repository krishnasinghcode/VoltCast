from datetime import date

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import config, train
from .db import get_session
from .predict import ModelNotTrainedError, latest_agg_date, predict_all_vehicles, predict_for_vehicle, persist_prediction
from .train import InsufficientHistoryError, load_current_model, run_training

app = FastAPI(title="CVI Platform — ML / Intelligence Service")


class TrainRequest(BaseModel):
    simulation_id: str
    horizon_days: int = config.HORIZON_DAYS_DEFAULT


class PredictRequest(BaseModel):
    simulation_id: str | None = None
    as_of_date: date | None = None


def error_response(code: str, message: str, details: dict | None = None):
    return {"error": {"code": code, "message": message, "details": details or {}}}


@app.get("/health")
def health():
    _, model_version = load_current_model()
    return {"status": "ok", "contract_version": config.CONTRACT_VERSION, "model_loaded": model_version is not None}


@app.post("/api/v1/train")
def train_endpoint(req: TrainRequest, session: Session = Depends(get_session)):
    try:
        metrics = run_training(session, req.simulation_id, req.horizon_days)
        session.commit()
        return metrics
    except InsufficientHistoryError as exc:
        raise HTTPException(status_code=400, detail=error_response("INSUFFICIENT_HISTORY", str(exc)))


@app.get("/api/v1/train/status")
def train_status():
    metrics = train.load_current_metrics()
    if metrics is None:
        raise HTTPException(status_code=404, detail=error_response("NOT_TRAINED", "no training run has completed yet"))
    return metrics


@app.post("/api/v1/predict")
def predict_endpoint(req: PredictRequest, session: Session = Depends(get_session)):
    try:
        return predict_all_vehicles(session, req.as_of_date)
    except ModelNotTrainedError as exc:
        raise HTTPException(status_code=400, detail=error_response("MODEL_NOT_TRAINED", str(exc)))


@app.post("/api/v1/predict/{vehicle_id}")
def predict_single_endpoint(vehicle_id: str, session: Session = Depends(get_session)):
    try:
        prediction = predict_for_vehicle(session, vehicle_id, latest_agg_date(session))
    except ModelNotTrainedError as exc:
        raise HTTPException(status_code=400, detail=error_response("MODEL_NOT_TRAINED", str(exc)))

    if prediction is None:
        raise HTTPException(
            status_code=400,
            detail=error_response(
                "INSUFFICIENT_HISTORY",
                f"vehicle {vehicle_id} does not have enough agg_daily history for a prediction",
            ),
        )

    persist_prediction(session, prediction)
    session.commit()
    return prediction
