from datetime import datetime, timedelta, timezone

import numpy as np

from src.event_builder import FaultInjector, build_event
from src.models import Archetype, FaultInjectionConfig, VehicleDynamicState, VehicleStaticInfo

FIELD_RANGES = {
    "soc_pct": (0, 100),
    "voltage_v": (250, 450),
    "current_a": (-400, 400),
    "temperature_c": (-20, 80),
    "charging_power_kw": (0, 350),
    "speed_kmh": (0, 220),
    "ambient_temperature_c": (-30, 55),
}


def make_static() -> VehicleStaticInfo:
    return VehicleStaticInfo(
        vehicle_id="EV-000000042",
        fleet_id="fleet-1",
        model_id="model-1",
        battery_id="battery-1",
        archetype=Archetype.HEALTHY,
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
        soc_pct=72.4,
        usable_capacity_kwh=75.0,
        soh_pct=98.0,
        odometer_km=48231.6,
        equivalent_full_cycles=10.0,
        battery_temp_c=34.7,
        voltage_v=391.8,
        current_a=48.2,
        speed_kmh=52.4,
        is_charging=True,
        charging_power_kw=42.5,
        behavior_mode="DRIVING",
    )


def test_event_matches_contract_field_ranges():
    static = make_static()
    state = make_state()
    sim_time = datetime(2026, 9, 28, 10, 21, 31, tzinfo=timezone.utc)
    event = build_event(static, state, "sim-1", sim_time, ambient_temperature_c=31.2)

    for field_name, (lo, hi) in FIELD_RANGES.items():
        assert lo <= event[field_name] <= hi, f"{field_name}={event[field_name]} out of range"

    assert event["vehicle_id"] == static.vehicle_id
    assert event["contract_version"] == "1.0.0"
    assert event["sequence_no"] == 1


def test_sequence_no_monotonic_despite_fault_injection_reorder():
    static = make_static()
    state = make_state()
    sim_time = datetime(2026, 9, 28, 10, 21, 31, tzinfo=timezone.utc)
    rng = np.random.default_rng(99)

    injector = FaultInjector(FaultInjectionConfig(duplicate_rate=0.1, out_of_order_rate=0.3, malformed_rate=0.0))

    true_order_sequence_nos = []
    sent_events = []
    for _ in range(50):
        event = build_event(static, state, "sim-1", sim_time, ambient_temperature_c=25.0)
        true_order_sequence_nos.append(event["sequence_no"])
        sent_events.extend(injector.process(event, rng))
        sim_time += timedelta(seconds=1)
    sent_events.extend(injector.flush())

    # true generation order is strictly monotonic regardless of send order
    assert true_order_sequence_nos == sorted(true_order_sequence_nos)
    assert true_order_sequence_nos == list(range(1, 51))

    # send order may be out of order (that's the point of the fault injection) but every
    # sequence_no that was generated must appear at least once among sent events
    sent_sequence_nos = {e["sequence_no"] for e in sent_events}
    assert set(true_order_sequence_nos).issubset(sent_sequence_nos)
