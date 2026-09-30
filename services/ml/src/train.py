"""Trains GradientBoostingRegressor, evaluates vs baseline, saves model (mlMaster.md ss6.2)."""
import json
import os
import time
from datetime import datetime, timezone

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import train_test_split
from sqlalchemy import text

from . import baseline_model, config
from .label_construction import build_training_set


class InsufficientHistoryError(Exception):
    pass


def _get_simulation_duration_days(session, simulation_id: str) -> int:
    row = session.execute(
        text("SELECT duration_days FROM simulations WHERE simulation_id = :sid"),
        {"sid": simulation_id},
    ).fetchone()
    if row is None:
        raise InsufficientHistoryError(f"simulation {simulation_id} not found")
    return int(row[0])


def run_training(session, simulation_id: str, horizon_days: int = config.HORIZON_DAYS_DEFAULT) -> dict:
    duration_days = _get_simulation_duration_days(session, simulation_id)
    features_df, labels, meta_df = build_training_set(session, simulation_id, duration_days, horizon_days)

    if len(features_df) < 50:
        raise InsufficientHistoryError(
            f"only {len(features_df)} training rows available — need a simulation with more "
            f"aggregated history (>= ~{config.MIN_HISTORY_DAYS} days) before /train can produce "
            "a non-degenerate model"
        )

    idx_train, idx_test = train_test_split(features_df.index, test_size=0.2, random_state=42)

    X_train, X_test = features_df.loc[idx_train], features_df.loc[idx_test]
    y_train, y_test = labels.loc[idx_train], labels.loc[idx_test]
    meta_test = meta_df.loc[idx_test]

    X_train_filled = X_train.fillna(X_train.median(numeric_only=True))
    X_test_filled = X_test.fillna(X_train.median(numeric_only=True))

    model = GradientBoostingRegressor(n_estimators=200, max_depth=3, learning_rate=0.05, random_state=42)
    model.fit(X_train_filled, y_train)

    y_pred = model.predict(X_test_filled)
    mae_model = float(mean_absolute_error(y_test, y_pred))
    rmse_model = float(np.sqrt(mean_squared_error(y_test, y_pred)))

    y_baseline = baseline_model.predict_baseline_batch(
        meta_test["soh_7d_ago"], meta_test["soh_now"], horizon_days
    )
    mae_baseline = float(mean_absolute_error(y_test, y_baseline))
    rmse_baseline = float(np.sqrt(mean_squared_error(y_test, y_baseline)))

    model_version = str(int(time.time()))
    os.makedirs(config.MODEL_DIR, exist_ok=True)
    model_path = os.path.join(config.MODEL_DIR, f"model_{model_version}.pkl")
    joblib.dump({"model": model, "feature_columns": config.FEATURE_COLUMNS,
                 "median_fill": X_train.median(numeric_only=True).to_dict()}, model_path)

    trained_at = datetime.now(timezone.utc).isoformat()
    metrics = {
        "mae_model": mae_model,
        "mae_baseline": mae_baseline,
        "rmse_model": rmse_model,
        "rmse_baseline": rmse_baseline,
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "trained_at": trained_at,
        "model_version": model_version,
        "contract_version": config.CONTRACT_VERSION,
        "horizon_days": horizon_days,
        "beats_baseline": mae_model < mae_baseline,
    }
    metrics_path = os.path.join(config.MODEL_DIR, f"model_{model_version}.metrics.json")
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    with open(os.path.join(config.MODEL_DIR, "current.txt"), "w") as f:
        f.write(model_version)

    return metrics


def load_current_model():
    pointer_path = os.path.join(config.MODEL_DIR, "current.txt")
    if not os.path.exists(pointer_path):
        return None, None
    with open(pointer_path) as f:
        model_version = f.read().strip()
    model_path = os.path.join(config.MODEL_DIR, f"model_{model_version}.pkl")
    if not os.path.exists(model_path):
        return None, None
    bundle = joblib.load(model_path)
    return bundle, model_version


def load_current_metrics() -> dict | None:
    pointer_path = os.path.join(config.MODEL_DIR, "current.txt")
    if not os.path.exists(pointer_path):
        return None
    with open(pointer_path) as f:
        model_version = f.read().strip()
    metrics_path = os.path.join(config.MODEL_DIR, f"model_{model_version}.metrics.json")
    if not os.path.exists(metrics_path):
        return None
    with open(metrics_path) as f:
        return json.load(f)
