"""Loads model, predicts for one or all vehicles, writes battery_predictions + alerts
(mlMaster.md ss3.1, ss6.3)."""
from datetime import date, datetime

import pandas as pd
from sqlalchemy import text

from . import config
from .feature_engineering import build_features_for_vehicle
from .risk_rules import risk_level
from .train import load_current_model


class ModelNotTrainedError(Exception):
    pass


def latest_agg_date(session) -> date | None:
    """Return the newest event date available for predictions."""
    value = session.execute(text("SELECT MAX(date) FROM agg_daily")).scalar()
    return value.date() if isinstance(value, datetime) else value


def _apply_median_fill(features: dict, median_fill: dict) -> pd.DataFrame:
    row = pd.DataFrame([features], columns=config.FEATURE_COLUMNS)
    for col, fill_value in median_fill.items():
        if col in row.columns:
            row[col] = row[col].fillna(fill_value)
    return row


def predict_for_vehicle(session, vehicle_id: str, as_of_date=None) -> dict | None:
    """Returns a prediction dict or None if insufficient history. Raises
    ModelNotTrainedError if no model has been trained yet."""
    bundle, model_version = load_current_model()
    if bundle is None:
        raise ModelNotTrainedError("no trained model available — call /train first")

    if as_of_date is None:
        as_of_date = latest_agg_date(session)
    if as_of_date is None:
        return None
    features = build_features_for_vehicle(session, vehicle_id, as_of_date)
    if features is None:
        return None

    X = _apply_median_fill(features, bundle["median_fill"])
    predicted_degradation = float(max(0.0, bundle["model"].predict(X)[0]))
    current_soh = features["current_soh"]
    predicted_soh_30d = max(current_soh - predicted_degradation, 0.0)
    level = risk_level(predicted_degradation)

    return {
        "vehicle_id": vehicle_id,
        "current_soh": current_soh,
        "predicted_degradation_30d_pct": predicted_degradation,
        "predicted_soh_30d": predicted_soh_30d,
        "risk_level": level,
        "horizon_days": config.HORIZON_DAYS_DEFAULT,
        "model_version": model_version,
    }


def persist_prediction(session, prediction: dict) -> str:
    # A rerun with the same model should refresh its vehicle/horizon result,
    # not append another indistinguishable row. New model versions remain in
    # the table as history.
    existing = session.execute(
        text(
            """
            SELECT prediction_id
            FROM battery_predictions
            WHERE vehicle_id = :vehicle_id
              AND horizon_days = :horizon_days
              AND model_version = :model_version
            ORDER BY predicted_at DESC
            LIMIT 1
            """
        ),
        prediction,
    ).fetchone()

    if existing:
        prediction_id = str(existing[0])
        session.execute(
            text(
                """
                UPDATE battery_predictions
                SET predicted_at = now(), current_soh = :current_soh,
                    predicted_degradation_30d_pct = :predicted_degradation_30d_pct,
                    predicted_soh_30d = :predicted_soh_30d, risk_level = :risk_level
                WHERE prediction_id = :prediction_id
                """
            ),
            {**prediction, "prediction_id": prediction_id},
        )
    else:
        row = session.execute(
            text(
                """
                INSERT INTO battery_predictions
                    (vehicle_id, current_soh, predicted_degradation_30d_pct, predicted_soh_30d,
                     risk_level, horizon_days, model_version)
                VALUES (:vehicle_id, :current_soh, :predicted_degradation_30d_pct, :predicted_soh_30d,
                        :risk_level, :horizon_days, :model_version)
                RETURNING prediction_id
                """
            ),
            prediction,
        ).fetchone()
        prediction_id = str(row[0])

    if prediction["risk_level"] == "HIGH":
        alert = session.execute(
            text(
                """
                SELECT alert_id FROM alerts
                WHERE prediction_id = :prediction_id AND source = 'ML_PREDICTION'
                ORDER BY created_at DESC LIMIT 1
                """
            ),
            {"prediction_id": prediction_id},
        ).fetchone()
        if alert:
            session.execute(
                text(
                    """
                    UPDATE alerts SET created_at = now(),
                        status = CASE WHEN status = 'RESOLVED' THEN 'OPEN' ELSE status END
                    WHERE alert_id = :alert_id
                    """
                ),
                {"alert_id": alert[0]},
            )
        else:
            session.execute(
                text(
                    """
                    INSERT INTO alerts (vehicle_id, alert_type, severity, status, source, prediction_id)
                    VALUES (:vehicle_id, 'HIGH_RISK_PREDICTION', 'WARNING', 'OPEN', 'ML_PREDICTION', :prediction_id)
                    """
                ),
                {"vehicle_id": prediction["vehicle_id"], "prediction_id": prediction_id},
            )
    else:
        session.execute(
            text(
                """
                UPDATE alerts SET status = 'RESOLVED'
                WHERE prediction_id = :prediction_id AND source = 'ML_PREDICTION' AND status = 'OPEN'
                """
            ),
            {"prediction_id": prediction_id},
        )
    return prediction_id


def predict_all_vehicles(session, as_of_date=None) -> dict:
    # simulation_id (mlMaster.md ss3.1, optional on this endpoint) is not applied as a filter
    # here for the same reason noted in label_construction.build_training_set: neither
    # `vehicles` nor `agg_daily` (CONTRACT.md ss3) carries a simulation_id column to filter by.
    as_of_date = as_of_date or latest_agg_date(session)
    vehicle_ids = [r[0] for r in session.execute(text("SELECT vehicle_id FROM vehicles")).fetchall()]

    counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    skipped = 0
    predicted = 0
    for vehicle_id in vehicle_ids:
        prediction = predict_for_vehicle(session, vehicle_id, as_of_date)
        if prediction is None:
            skipped += 1
            continue
        persist_prediction(session, prediction)
        counts[prediction["risk_level"]] += 1
        predicted += 1

    session.commit()
    return {
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "predicted": predicted,
        "skipped_insufficient_history": skipped,
        "risk_counts": counts,
    }
