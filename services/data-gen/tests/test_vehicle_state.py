from datetime import datetime, timedelta, timezone

import numpy as np

from src.models import Archetype, VehicleDynamicState, VehicleStaticInfo
from src.vehicle_state import (
    MAX_CHARGE_POWER_STEP_KW,
    MAX_SPEED_STEP_KMH,
    MAX_TEMP_STEP_C,
    tick,
)


def make_static() -> VehicleStaticInfo:
    return VehicleStaticInfo(
        vehicle_id="EV-000000001",
        fleet_id="fleet-1",
        model_id="model-1",
        battery_id="battery-1",
        archetype=Archetype.NORMAL,
        chemistry_id="chem-1",
        chemistry_name="NMC",
        calendar_factor=1.0,
        cycle_factor=1.0,
        nominal_capacity_kwh=75.0,
        rated_voltage_v=400.0,
        internal_resistance_ohm=0.05,
        manufacture_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
        initial_odometer_km=1000.0,
        aggressiveness=1.0,
        routine_seed=1,
        regional_offset_c=0.0,
        commute_am_start_hr=7.0,
        commute_am_end_hr=8.0,
        commute_pm_start_hr=17.0,
        commute_pm_end_hr=18.0,
        charge_hour=20.0,
    )


def make_state() -> VehicleDynamicState:
    return VehicleDynamicState(
        soc_pct=50.0,
        usable_capacity_kwh=75.0,
        soh_pct=100.0,
        odometer_km=1000.0,
        equivalent_full_cycles=0.0,
        battery_temp_c=25.0,
        voltage_v=380.0,
        current_a=0.0,
        speed_kmh=0.0,
        is_charging=False,
        charging_power_kw=0.0,
        behavior_mode="IDLE",
        day_start_odometer_km=1000.0,
    )


def test_bounded_steps_driving_then_charging():
    static = make_static()
    state = make_state()
    state.behavior_mode = "DRIVING"
    sim_time = datetime(2026, 6, 1, 7, 30, tzinfo=timezone.utc)  # inside AM commute window
    rng = np.random.default_rng(123)

    prev_speed, prev_temp = state.speed_kmh, state.battery_temp_c
    for _ in range(100):
        state.behavior_mode = "DRIVING"  # force, isolate the bounded-step assertion
        tick(static, state, sim_time, rng)
        assert abs(state.speed_kmh - prev_speed) <= MAX_SPEED_STEP_KMH + 1e-9
        assert abs(state.battery_temp_c - prev_temp) <= MAX_TEMP_STEP_C + 1e-9
        assert 0.0 <= state.soc_pct <= 100.0
        prev_speed, prev_temp = state.speed_kmh, state.battery_temp_c
        sim_time += timedelta(seconds=1)

    sim_time = datetime(2026, 6, 1, 21, 0, tzinfo=timezone.utc)  # inside the charge window
    state.behavior_mode = "CHARGING"
    prev_power = state.charging_power_kw
    for _ in range(100):
        tick(static, state, sim_time, rng)
        assert abs(state.charging_power_kw - prev_power) <= MAX_CHARGE_POWER_STEP_KW + 1e-9
        assert 0.0 <= state.soc_pct <= 100.0
        prev_power = state.charging_power_kw
        sim_time += timedelta(seconds=1)


def test_odometer_never_decreases():
    static = make_static()
    state = make_state()
    sim_time = datetime(2026, 6, 1, 7, 30, tzinfo=timezone.utc)
    rng = np.random.default_rng(7)
    prev_odometer = state.odometer_km
    for _ in range(200):
        tick(static, state, sim_time, rng)
        assert state.odometer_km >= prev_odometer - 1e-9
        prev_odometer = state.odometer_km
        sim_time += timedelta(seconds=1)
