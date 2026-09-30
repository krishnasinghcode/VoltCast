"""Naive persistence baseline (mlMaster.md ss6.1). No fitting required — a formula."""
import numpy as np
import pandas as pd


def predict_baseline(soh_7d_ago: float, soh_now: float, horizon_days: int = 30) -> float:
    """predicted_degradation_30d = recent_daily_degradation_rate * horizon_days,
    where recent_daily_degradation_rate = (soh 7 days ago - soh_now) / 7."""
    recent_daily_rate = (soh_7d_ago - soh_now) / 7.0
    return max(0.0, recent_daily_rate * horizon_days)


def predict_baseline_batch(soh_7d_ago: pd.Series, soh_now: pd.Series, horizon_days: int = 30) -> np.ndarray:
    daily_rate = (soh_7d_ago.to_numpy() - soh_now.to_numpy()) / 7.0
    return np.clip(daily_rate * horizon_days, 0.0, None)
