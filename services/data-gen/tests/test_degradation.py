from src.degradation import apply_degradation, compute_daily_degradation
from src.models import Archetype


def test_compute_daily_degradation_known_inputs():
    result = compute_daily_degradation(
        chemistry_calendar_factor=1.0,
        chemistry_cycle_factor=1.0,
        equivalent_full_cycles_today=0.5,
        hours_above_35c_today=2.0,
        fast_charge_sessions_today=1,
        deep_discharge_events_today=1,
        archetype=Archetype.NORMAL,
    )
    calendar_term = 0.0008 * 1.0
    cycle_term = 0.015 * 1.0 * 0.5
    thermal_term = 0.004 * 2.0
    fastcharge_term = 0.01 * 1
    deepdischarge_term = 0.008 * 1
    expected = calendar_term + cycle_term + 1.0 * (thermal_term + fastcharge_term + deepdischarge_term)
    assert abs(result - expected) < 1e-9


def test_daily_degradation_clipped_at_max():
    result = compute_daily_degradation(
        chemistry_calendar_factor=1.0,
        chemistry_cycle_factor=1.0,
        equivalent_full_cycles_today=1000.0,
        hours_above_35c_today=100.0,
        fast_charge_sessions_today=50,
        deep_discharge_events_today=50,
        archetype=Archetype.BAD,
    )
    assert result == 0.05


def test_daily_degradation_never_negative():
    result = compute_daily_degradation(
        chemistry_calendar_factor=0.0,
        chemistry_cycle_factor=0.0,
        equivalent_full_cycles_today=0.0,
        hours_above_35c_today=0.0,
        fast_charge_sessions_today=0,
        deep_discharge_events_today=0,
        archetype=Archetype.HEALTHY,
    )
    assert result == 0.0


def test_soh_floor_at_60():
    result = apply_degradation(current_soh_pct=60.02, daily_degradation_pct=0.05)
    assert result == 60.0
    result2 = apply_degradation(current_soh_pct=100.0, daily_degradation_pct=0.05)
    assert abs(result2 - 99.95) < 1e-9
