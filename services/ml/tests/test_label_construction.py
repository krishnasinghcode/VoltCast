import pandas as pd
import pytest

from src.label_construction import build_training_example, soh_at


STATIC_CONTEXT = {
    "current_soh": 95.0,
    "vehicle_age_days": 200,
    "nominal_capacity_kwh": 75.0,
    "chemistry_calendar_factor": 1.0,
    "chemistry_cycle_factor": 1.0,
}


def _agg_df(days):
    rows = [
        {"vehicle_id": "EV-1", "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=d),
         "distance_km": 100.0, "energy_consumed_kwh": 20.0, "energy_charged_kwh": 18.0,
         "charging_sessions": 1, "fast_charge_sessions": 0, "deep_discharge_events": 0,
         "high_temp_hours": 1.0, "max_temp_c": 30.0, "equivalent_full_cycles": 0.5,
         "soh_pct": 100.0 - d * 0.1}
        for d in range(days)
    ]
    return pd.DataFrame(rows)


def test_label_equals_soh_now_minus_soh_future():
    agg_df = _agg_df(10)
    as_of_date = pd.Timestamp("2026-01-10")
    soh_series = agg_df[["date", "soh_pct"]]
    future_row = pd.DataFrame([{"date": pd.Timestamp("2026-02-09"), "soh_pct": 97.0}])
    soh_series = pd.concat([soh_series, future_row], ignore_index=True)

    example = build_training_example(agg_df, STATIC_CONTEXT, soh_series, as_of_date, horizon_days=30)

    assert example is not None
    features, label = example
    assert label == pytest.approx(STATIC_CONTEXT["current_soh"] - 97.0)


def test_leakage_check_trips_when_feature_window_extends_past_as_of_day():
    agg_df = _agg_df(10)  # rows go up to day 9 (2026-01-10)
    as_of_date = pd.Timestamp("2026-01-05")  # earlier than the latest row in agg_df
    soh_series = agg_df[["date", "soh_pct"]]

    with pytest.raises(AssertionError, match="leakage"):
        build_training_example(agg_df, STATIC_CONTEXT, soh_series, as_of_date, horizon_days=30)


def test_soh_at_returns_none_when_date_missing():
    agg_df = _agg_df(5)
    soh_series = agg_df[["date", "soh_pct"]]
    assert soh_at(soh_series, pd.Timestamp("2030-01-01")) is None
