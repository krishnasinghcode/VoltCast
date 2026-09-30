"""Training pair construction per CONTRACT.md ss9 ground truth + mlMaster.md ss5.

label = soh_now - soh_future, always >= 0 under the degradation model.
Leakage guard: features for as_of_day must only use agg_daily rows with
date <= as_of_day; the label uses a strictly later date.
"""
from datetime import timedelta

import pandas as pd
from sqlalchemy import text

from . import config
from .feature_engineering import build_feature_vector, fetch_static_context, has_min_history


def fetch_soh_series(session, vehicle_id: str) -> pd.DataFrame:
    rows = session.execute(
        text(
            """
            SELECT date, soh_pct FROM agg_daily
            WHERE vehicle_id = :vehicle_id AND soh_pct IS NOT NULL
            ORDER BY date
            """
        ),
        {"vehicle_id": vehicle_id},
    ).fetchall()
    df = pd.DataFrame(rows, columns=["date", "soh_pct"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def soh_at(soh_series: pd.DataFrame, target_date) -> float | None:
    row = soh_series.loc[soh_series["date"] == pd.Timestamp(target_date)]
    if row.empty:
        return None
    return float(row.iloc[0]["soh_pct"])


def build_training_example(agg_df: pd.DataFrame, static_context: dict, soh_series: pd.DataFrame,
                            as_of_date, horizon_days: int) -> tuple[dict, float] | None:
    """Pure function: given agg_df already filtered to date <= as_of_date, build one
    (features, label) pair, or None if the future SOH point is unavailable."""
    feature_window_end = agg_df["date"].max() if not agg_df.empty else as_of_date
    assert feature_window_end <= pd.Timestamp(as_of_date), (
        f"leakage: feature window end {feature_window_end} is after as_of_date {as_of_date}"
    )

    soh_now = static_context["current_soh"]
    future_date = pd.Timestamp(as_of_date) + timedelta(days=horizon_days)
    soh_future = soh_at(soh_series, future_date)
    if soh_future is None:
        return None

    features = build_feature_vector(agg_df, static_context, as_of_date)
    label = soh_now - soh_future
    return features, label


def build_training_set(session, simulation_id: str, duration_days: int,
                        horizon_days: int = config.HORIZON_DAYS_DEFAULT,
                        stride_days: int = config.FEATURE_STRIDE_DAYS) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    # ASSUMPTION/DEVIATION (flagged per CONTRACT.md ss0 process): agg_daily (CONTRACT.md ss3)
    # has no simulation_id column, so training rows cannot be scoped to one simulation at the
    # SQL level. simulation_id is accepted (per mlMaster.md ss3.1's /train contract) and used
    # only to look up duration_days from `simulations`; all agg_daily history for every vehicle
    # is used. Safe for this platform's single-simulation-at-a-time demo usage; would need an
    # agg_daily.simulation_id column to be correct with multiple concurrent simulations.
    vehicle_ids = [
        r[0] for r in session.execute(
            text(
                """
                SELECT DISTINCT ad.vehicle_id FROM agg_daily ad
                JOIN vehicles v ON v.vehicle_id = ad.vehicle_id
                """
            )
        ).fetchall()
    ]

    feature_rows = []
    labels = []
    meta_rows = []
    for vehicle_id in vehicle_ids:
        full_agg_df = session.execute(
            text(
                """
                SELECT vehicle_id, date, distance_km, energy_consumed_kwh, energy_charged_kwh,
                       charging_sessions, fast_charge_sessions, deep_discharge_events,
                       high_temp_hours, max_temp_c, equivalent_full_cycles, soh_pct
                FROM agg_daily WHERE vehicle_id = :vehicle_id ORDER BY date
                """
            ),
            {"vehicle_id": vehicle_id},
        ).fetchall()
        from .feature_engineering import AGG_DAILY_COLUMNS
        full_df = pd.DataFrame(full_agg_df, columns=AGG_DAILY_COLUMNS)
        if full_df.empty:
            continue
        full_df["date"] = pd.to_datetime(full_df["date"])
        soh_series = full_df[["date", "soh_pct"]].dropna()

        min_date = full_df["date"].min()
        max_as_of_day = duration_days - horizon_days
        for day_offset in range(config.FEATURE_WINDOW_SHORT_DAYS, max_as_of_day, stride_days):
            as_of_date = min_date + timedelta(days=day_offset)
            window_df = full_df.loc[full_df["date"] <= as_of_date]
            if not has_min_history(window_df, as_of_date):
                continue
            static_context = fetch_static_context(session, vehicle_id, as_of_date)
            if static_context is None:
                continue
            example = build_training_example(window_df, static_context, soh_series, as_of_date, horizon_days)
            if example is None:
                continue
            features, label = example
            soh_7d_ago = soh_at(soh_series, as_of_date - timedelta(days=7))
            feature_rows.append(features)
            labels.append(label)
            meta_rows.append({
                "vehicle_id": vehicle_id,
                "as_of_date": as_of_date,
                "soh_now": static_context["current_soh"],
                "soh_7d_ago": soh_7d_ago if soh_7d_ago is not None else static_context["current_soh"],
            })

    features_df = pd.DataFrame(feature_rows, columns=config.FEATURE_COLUMNS)
    labels_series = pd.Series(labels, name="label")
    meta_df = pd.DataFrame(meta_rows)
    return features_df, labels_series, meta_df
