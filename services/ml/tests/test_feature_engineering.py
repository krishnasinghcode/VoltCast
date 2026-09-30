import pandas as pd
import pytest

from src.feature_engineering import build_feature_vector, has_min_history


def _agg_df(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


STATIC_CONTEXT = {
    "current_soh": 95.0,
    "vehicle_age_days": 200,
    "nominal_capacity_kwh": 75.0,
    "chemistry_calendar_factor": 1.0,
    "chemistry_cycle_factor": 1.0,
}


def test_7d_window_aggregates_match_hand_calculated_values():
    rows = [
        {"vehicle_id": "EV-1", "date": f"2026-01-{d:02d}", "distance_km": 100.0,
         "energy_consumed_kwh": 20.0, "energy_charged_kwh": 18.0, "charging_sessions": 1,
         "fast_charge_sessions": 1 if d % 2 == 0 else 0, "deep_discharge_events": 0,
         "high_temp_hours": 1.0, "max_temp_c": 30.0, "equivalent_full_cycles": 0.5, "soh_pct": 100.0 - d * 0.1}
        for d in range(1, 8)
    ]
    agg_df = _agg_df(rows)
    as_of_date = pd.Timestamp("2026-01-07")

    features = build_feature_vector(agg_df, STATIC_CONTEXT, as_of_date)

    assert features["distance_7d"] == pytest.approx(700.0)
    assert features["avg_max_temp_7d"] == pytest.approx(30.0)
    assert features["high_temp_hours_7d"] == pytest.approx(7.0)
    assert features["fast_charges_7d"] == 3
    assert features["efc_7d"] == pytest.approx(3.5)
    assert features["energy_per_km_7d"] == pytest.approx(20.0 / 100.0)


def test_30d_window_only_includes_rows_within_window():
    rows = [
        {"vehicle_id": "EV-1", "date": f"2025-12-{d:02d}" if d <= 31 else f"2026-01-{d-31:02d}",
         "distance_km": 50.0, "energy_consumed_kwh": 10.0, "energy_charged_kwh": 9.0,
         "charging_sessions": 0, "fast_charge_sessions": 0, "deep_discharge_events": 0,
         "high_temp_hours": 0.0, "max_temp_c": 25.0, "equivalent_full_cycles": 1.0, "soh_pct": 99.0}
        for d in range(1, 41)
    ]
    agg_df = _agg_df(rows)
    as_of_date = pd.Timestamp("2026-01-09")

    features = build_feature_vector(agg_df, STATIC_CONTEXT, as_of_date)

    assert features["efc_30d"] == pytest.approx(30.0)


def test_has_min_history_false_when_fewer_than_7_days():
    rows = [
        {"vehicle_id": "EV-1", "date": "2026-01-01", "distance_km": 50.0,
         "energy_consumed_kwh": 10.0, "energy_charged_kwh": 9.0, "charging_sessions": 0,
         "fast_charge_sessions": 0, "deep_discharge_events": 0, "high_temp_hours": 0.0,
         "max_temp_c": 25.0, "equivalent_full_cycles": 1.0, "soh_pct": 99.0}
    ]
    agg_df = _agg_df(rows)
    assert has_min_history(agg_df, pd.Timestamp("2026-01-01")) is False
