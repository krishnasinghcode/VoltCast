"""Builds rolling 7d/30d feature vectors from agg_daily rows (CONTRACT.md ss3, mlMaster.md ss4).

Pure functions here operate on pandas DataFrames so they can be unit-tested
without a live Postgres connection. DB-facing wrappers fetch the rows and
static context, then delegate to the pure functions.
"""
from datetime import timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text

from . import config

AGG_DAILY_COLUMNS = [
    "vehicle_id", "date", "distance_km", "energy_consumed_kwh", "energy_charged_kwh",
    "charging_sessions", "fast_charge_sessions", "deep_discharge_events",
    "high_temp_hours", "max_temp_c", "equivalent_full_cycles", "soh_pct",
]


def _window_slice(agg_df: pd.DataFrame, as_of_date, window_days: int) -> pd.DataFrame:
    # SQLAlchemy returns PostgreSQL DATE values as datetime.date, while this
    # frame's date column is pandas datetime64. Normalize before comparison.
    as_of_date = pd.Timestamp(as_of_date)
    start = as_of_date - timedelta(days=window_days - 1)
    mask = (agg_df["date"] >= start) & (agg_df["date"] <= as_of_date)
    return agg_df.loc[mask]


def _window_aggregates(window_df: pd.DataFrame) -> dict:
    if window_df.empty:
        return {
            "high_temp_hours": 0.0, "fast_charges": 0, "deep_discharges": 0,
            "distance": 0.0, "energy_consumed": 0.0, "efc": 0.0,
            "avg_max_temp": np.nan, "energy_per_km": np.nan,
        }
    distance = window_df["distance_km"].sum()
    energy_consumed = window_df["energy_consumed_kwh"].sum()
    return {
        "high_temp_hours": window_df["high_temp_hours"].sum(),
        "fast_charges": int(window_df["fast_charge_sessions"].sum()),
        "deep_discharges": int(window_df["deep_discharge_events"].sum()),
        "distance": distance,
        "energy_consumed": energy_consumed,
        "efc": window_df["equivalent_full_cycles"].sum(),
        "avg_max_temp": window_df["max_temp_c"].mean(),
        "energy_per_km": (energy_consumed / distance) if distance else np.nan,
    }


def _soh_trend(window_df: pd.DataFrame) -> float:
    """Linear slope of soh_pct over the window (pct per day). 0.0 if <2 points."""
    points = window_df.dropna(subset=["soh_pct"]).sort_values("date")
    if len(points) < 2:
        return 0.0
    x = (points["date"] - points["date"].min()).dt.days.to_numpy(dtype=float)
    y = points["soh_pct"].to_numpy(dtype=float)
    slope, _ = np.polyfit(x, y, 1)
    return float(slope)


def has_min_history(agg_df: pd.DataFrame, as_of_date, min_days: int = config.FEATURE_WINDOW_SHORT_DAYS) -> bool:
    window = _window_slice(agg_df, as_of_date, config.FEATURE_WINDOW_SHORT_DAYS)
    return len(window) >= min_days


def build_feature_vector(agg_df: pd.DataFrame, static_context: dict, as_of_date) -> dict:
    """Build one feature row for vehicle at as_of_date from agg_daily rows.

    agg_df must contain only rows with date <= as_of_date for the caller's
    vehicle (leakage guard asserted below). static_context carries
    nominal_capacity_kwh, chemistry_calendar_factor, chemistry_cycle_factor,
    vehicle_age_days, current_soh.
    """
    as_of_date = pd.Timestamp(as_of_date)
    feature_window_end = agg_df["date"].max() if not agg_df.empty else as_of_date
    assert feature_window_end <= as_of_date, (
        f"leakage: feature window end {feature_window_end} is after as_of_date {as_of_date}"
    )

    win7 = _window_slice(agg_df, as_of_date, config.FEATURE_WINDOW_SHORT_DAYS)
    win30 = _window_slice(agg_df, as_of_date, config.FEATURE_WINDOW_LONG_DAYS)
    agg7 = _window_aggregates(win7)
    agg30 = _window_aggregates(win30)
    trend30 = _soh_trend(win30)

    return {
        "current_soh": static_context["current_soh"],
        "vehicle_age_days": static_context["vehicle_age_days"],
        "nominal_capacity_kwh": static_context["nominal_capacity_kwh"],
        "chemistry_calendar_factor": static_context["chemistry_calendar_factor"],
        "chemistry_cycle_factor": static_context["chemistry_cycle_factor"],
        "avg_max_temp_7d": agg7["avg_max_temp"],
        "high_temp_hours_7d": agg7["high_temp_hours"],
        "fast_charges_7d": agg7["fast_charges"],
        "deep_discharges_7d": agg7["deep_discharges"],
        "distance_7d": agg7["distance"],
        "energy_per_km_7d": agg7["energy_per_km"],
        "efc_7d": agg7["efc"],
        "high_temp_hours_30d": agg30["high_temp_hours"],
        "fast_charges_30d": agg30["fast_charges"],
        "deep_discharges_30d": agg30["deep_discharges"],
        "efc_30d": agg30["efc"],
        "soh_trend_30d": trend30,
    }


# ---- DB-facing helpers -----------------------------------------------------

def fetch_agg_daily_df(session, vehicle_id: str, as_of_date, lookback_days: int = config.FEATURE_WINDOW_LONG_DAYS) -> pd.DataFrame:
    start = as_of_date - timedelta(days=lookback_days - 1)
    rows = session.execute(
        text(
            """
            SELECT vehicle_id, date, distance_km, energy_consumed_kwh, energy_charged_kwh,
                   charging_sessions, fast_charge_sessions, deep_discharge_events,
                   high_temp_hours, max_temp_c, equivalent_full_cycles, soh_pct
            FROM agg_daily
            WHERE vehicle_id = :vehicle_id AND date BETWEEN :start AND :as_of_date
            ORDER BY date
            """
        ),
        {"vehicle_id": vehicle_id, "start": start, "as_of_date": as_of_date},
    ).fetchall()
    df = pd.DataFrame(rows, columns=AGG_DAILY_COLUMNS)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        as_of_date = pd.Timestamp(as_of_date)
    return df


def fetch_static_context(session, vehicle_id: str, as_of_date) -> dict | None:
    row = session.execute(
        text(
            """
            SELECT v.manufacture_date, b.nominal_capacity_kwh,
                   c.calendar_factor, c.cycle_factor
            FROM vehicles v
            JOIN batteries b ON b.battery_id = v.battery_id
            JOIN battery_chemistries c ON c.chemistry_id = b.chemistry_id
            WHERE v.vehicle_id = :vehicle_id
            """
        ),
        {"vehicle_id": vehicle_id},
    ).fetchone()
    if row is None:
        return None
    manufacture_date, nominal_capacity_kwh, calendar_factor, cycle_factor = row

    latest_soh_row = session.execute(
        text(
            """
            SELECT soh_pct FROM agg_daily
            WHERE vehicle_id = :vehicle_id AND date <= :as_of_date AND soh_pct IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """
        ),
        {"vehicle_id": vehicle_id, "as_of_date": as_of_date},
    ).fetchone()
    if latest_soh_row is None:
        return None

    as_of_ts = pd.Timestamp(as_of_date)
    manufacture_ts = pd.Timestamp(manufacture_date)
    return {
        "current_soh": float(latest_soh_row[0]),
        "vehicle_age_days": (as_of_ts - manufacture_ts).days,
        "nominal_capacity_kwh": float(nominal_capacity_kwh),
        "chemistry_calendar_factor": float(calendar_factor),
        "chemistry_cycle_factor": float(cycle_factor),
    }


def build_features_for_vehicle(session, vehicle_id: str, as_of_date) -> dict | None:
    """Returns None (skip) if insufficient history, per mlMaster.md ss4."""
    agg_df = fetch_agg_daily_df(session, vehicle_id, as_of_date, config.FEATURE_WINDOW_LONG_DAYS)
    if not has_min_history(agg_df, as_of_date):
        return None
    static_context = fetch_static_context(session, vehicle_id, as_of_date)
    if static_context is None:
        return None
    as_of_ts = pd.Timestamp(as_of_date)
    return build_feature_vector(agg_df, static_context, as_of_ts)
