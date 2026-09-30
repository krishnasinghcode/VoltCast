"""Builds/loads the persistent vehicle population, deterministically from `seed`.

Nothing here is drawn independently: age correlates with odometer, odometer correlates with
cycle count, archetype correlates with future stress exposure (dataGenMaster.md §4)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import numpy as np

from src import db
from src.models import ARCHETYPE_MULTIPLIER, Archetype, VehicleStaticInfo

FLEET_REGIONS = [
    "Northeast", "Southeast", "Midwest", "Southwest", "West Coast",
    "Pacific Northwest", "Mountain", "Gulf Coast", "Great Lakes", "New England",
    "Mid-Atlantic", "Central Plains", "Texas Triangle", "Desert Southwest", "Rockies",
    "Bay Area", "Cascadia", "Appalachia", "Deep South", "Upper Midwest",
]

VEHICLE_MODEL_DEFS = [
    ("Voltara", "Aurora", 2023),
    ("Voltara", "Aurora Sport", 2024),
    ("Kinetix", "Ranger", 2022),
    ("Kinetix", "Ranger XL", 2024),
    ("Everdrive", "Stratos", 2023),
    ("Everdrive", "Stratos Pro", 2025),
    ("Solace Motors", "Halcyon", 2022),
    ("Solace Motors", "Halcyon GT", 2024),
]

CHEMISTRY_DEFS = [
    ("NMC", 1.0, 1.0),
    ("LFP", 0.7, 0.6),
]

ARCHETYPE_AGGRESSIVENESS = {
    Archetype.HEALTHY: 0.85,
    Archetype.NORMAL: 1.0,
    Archetype.STRESSED: 1.3,
    Archetype.BAD: 1.6,
}

_INSERT_CHUNK = 5000


def build_or_load_fleet(vehicle_count: int, seed: int, archetype_distribution: dict[str, float],
                         start_time: datetime) -> list[VehicleStaticInfo]:
    """If the `vehicles` table already has >= vehicle_count rows, load and return them.
    Otherwise build deterministically from seed and persist."""
    with db.session() as conn:
        existing = db.get_all_vehicles(conn)

    if len(existing) >= vehicle_count:
        return [_row_to_static_info(r, seed) for r in existing[:vehicle_count]]

    return _build_fleet(vehicle_count, seed, archetype_distribution, start_time)


def _commute_schedule(rng: np.random.Generator) -> dict:
    am_start = float(rng.uniform(6.5, 8.5))
    pm_start = float(rng.uniform(16.5, 18.5))
    return {
        "commute_am_start_hr": am_start,
        "commute_am_end_hr": am_start + float(rng.uniform(0.5, 1.0)),
        "commute_pm_start_hr": pm_start,
        "commute_pm_end_hr": pm_start + float(rng.uniform(0.5, 1.0)),
        "charge_hour": float(rng.uniform(19.0, 23.0)),
    }


def _row_to_static_info(row: dict, seed: int) -> VehicleStaticInfo:
    archetype = Archetype(row["archetype"])
    vehicle_index = int(row["vehicle_id"].split("-")[1])
    rng = np.random.default_rng(seed + vehicle_index)
    schedule = _commute_schedule(rng)
    return VehicleStaticInfo(
        vehicle_id=row["vehicle_id"],
        fleet_id=str(row["fleet_id"]),
        model_id=str(row["model_id"]),
        battery_id=str(row["battery_id"]),
        archetype=archetype,
        chemistry_id=str(row["chemistry_id"]),
        chemistry_name=row["chemistry_name"],
        calendar_factor=float(row["calendar_factor"]),
        cycle_factor=float(row["cycle_factor"]),
        nominal_capacity_kwh=float(row["nominal_capacity_kwh"]),
        rated_voltage_v=float(row["rated_voltage_v"]),
        internal_resistance_ohm=_internal_resistance(float(row["nominal_capacity_kwh"])),
        # tzinfo must be set here: every other datetime in this service (start_time, the fresh-build
        # path below) is timezone-aware. A naive datetime loaded here would raise "can't subtract
        # offset-naive and offset-aware datetimes" the moment age_years is computed against start_time.
        manufacture_date=datetime.combine(row["manufacture_date"], datetime.min.time(), tzinfo=timezone.utc),
        initial_odometer_km=float(row["initial_odometer_km"]),
        aggressiveness=ARCHETYPE_AGGRESSIVENESS[archetype] * rng.uniform(0.9, 1.1),
        routine_seed=seed + vehicle_index,
        regional_offset_c=rng.uniform(-4.0, 4.0),
        **schedule,
    )


def _internal_resistance(nominal_capacity_kwh: float) -> float:
    # larger packs -> lower internal resistance, roughly inverse-correlated
    return round(0.15 - 0.0006 * nominal_capacity_kwh, 4)


def _build_fleet(vehicle_count: int, seed: int, archetype_distribution: dict[str, float],
                  start_time: datetime) -> list[VehicleStaticInfo]:
    rng = np.random.default_rng(seed)

    fleet_ids = [str(uuid.uuid4()) for _ in FLEET_REGIONS]
    fleet_rows = [
        {"fleet_id": fid, "name": f"Fleet {i+1:02d}", "region": region}
        for i, (fid, region) in enumerate(zip(fleet_ids, FLEET_REGIONS))
    ]

    model_ids = [str(uuid.uuid4()) for _ in VEHICLE_MODEL_DEFS]
    model_rows = [
        {"model_id": mid, "manufacturer": m, "model_name": n, "model_year": y}
        for mid, (m, n, y) in zip(model_ids, VEHICLE_MODEL_DEFS)
    ]

    chemistry_ids = [str(uuid.uuid4()) for _ in CHEMISTRY_DEFS]
    chemistry_rows = [
        {"chemistry_id": cid, "name": name, "calendar_factor": cal, "cycle_factor": cyc}
        for cid, (name, cal, cyc) in zip(chemistry_ids, CHEMISTRY_DEFS)
    ]

    with db.session() as conn:
        db.insert_fleets(conn, fleet_rows)
        db.insert_vehicle_models(conn, model_rows)
        db.insert_battery_chemistries(conn, chemistry_rows)

    archetype_names = list(archetype_distribution.keys())
    archetype_probs = np.array([archetype_distribution[k] for k in archetype_names])
    archetype_probs = archetype_probs / archetype_probs.sum()

    static_infos: list[VehicleStaticInfo] = []
    battery_buffer: list[dict] = []
    vehicle_buffer: list[dict] = []

    for i in range(vehicle_count):
        vehicle_id = f"EV-{i:09d}"
        vehicle_rng = np.random.default_rng(seed + i)
        schedule = _commute_schedule(vehicle_rng)

        fleet_id = fleet_ids[rng.integers(0, len(fleet_ids))]
        model_id = model_ids[rng.integers(0, len(model_ids))]

        archetype = Archetype(rng.choice(archetype_names, p=archetype_probs))

        chem_idx = int(rng.integers(0, len(CHEMISTRY_DEFS)))
        chemistry_id = chemistry_ids[chem_idx]
        chemistry_name, calendar_factor, cycle_factor = CHEMISTRY_DEFS[chem_idx]

        nominal_capacity_kwh = float(rng.uniform(40, 100))
        # larger pack -> higher voltage, correlated not independent
        capacity_fraction = (nominal_capacity_kwh - 40) / (100 - 40)
        rated_voltage_v = float(350 + capacity_fraction * (420 - 350) + rng.normal(0, 5))

        age_years = float(rng.uniform(0, 5))
        manufacture_date = start_time - timedelta(days=age_years * 365)
        # higher age -> higher initial odometer, correlated not independent
        initial_odometer_km = float(age_years * rng.uniform(8000, 18000))

        battery_id = str(uuid.uuid4())
        battery_buffer.append({
            "battery_id": battery_id,
            "chemistry_id": chemistry_id,
            "nominal_capacity_kwh": nominal_capacity_kwh,
            "rated_voltage_v": rated_voltage_v,
            "installation_date": manufacture_date.date(),
        })
        vehicle_buffer.append({
            "vehicle_id": vehicle_id,
            "fleet_id": fleet_id,
            "model_id": model_id,
            "battery_id": battery_id,
            "archetype": archetype.value,
            "manufacture_date": manufacture_date.date(),
            "initial_odometer_km": initial_odometer_km,
        })

        static_infos.append(VehicleStaticInfo(
            vehicle_id=vehicle_id,
            fleet_id=fleet_id,
            model_id=model_id,
            battery_id=battery_id,
            archetype=archetype,
            chemistry_id=chemistry_id,
            chemistry_name=chemistry_name,
            calendar_factor=calendar_factor,
            cycle_factor=cycle_factor,
            nominal_capacity_kwh=nominal_capacity_kwh,
            rated_voltage_v=rated_voltage_v,
            internal_resistance_ohm=_internal_resistance(nominal_capacity_kwh),
            manufacture_date=manufacture_date,
            initial_odometer_km=initial_odometer_km,
            aggressiveness=ARCHETYPE_AGGRESSIVENESS[archetype] * vehicle_rng.uniform(0.9, 1.1),
            routine_seed=seed + i,
            regional_offset_c=vehicle_rng.uniform(-4.0, 4.0),
            **schedule,
        ))

        if len(vehicle_buffer) >= _INSERT_CHUNK:
            _flush(battery_buffer, vehicle_buffer)
            battery_buffer, vehicle_buffer = [], []

    if vehicle_buffer:
        _flush(battery_buffer, vehicle_buffer)

    return static_infos


def _flush(battery_buffer: list[dict], vehicle_buffer: list[dict]) -> None:
    with db.session() as conn:
        db.insert_batteries(conn, battery_buffer)
        db.insert_vehicles(conn, vehicle_buffer)


def initial_dynamic_state_for(static: VehicleStaticInfo, seed: int, start_time: datetime):
    """Build the initial VehicleDynamicState for a freshly (re)started simulation."""
    from src.models import VehicleDynamicState  # local import avoids cycle at module load

    rng = np.random.default_rng(static.routine_seed)
    age_years = max(0.0, (start_time - static.manufacture_date).days / 365.0)

    soc_pct = float(rng.uniform(20, 90))
    soh_pct = max(90.0, 100 - age_years * 0.8)
    usable_capacity_kwh = static.nominal_capacity_kwh * soh_pct / 100
    equivalent_full_cycles = age_years * 150  # rough age-correlated estimate

    return VehicleDynamicState(
        soc_pct=soc_pct,
        usable_capacity_kwh=usable_capacity_kwh,
        soh_pct=soh_pct,
        odometer_km=static.initial_odometer_km,
        equivalent_full_cycles=equivalent_full_cycles,
        battery_temp_c=25.0,
        voltage_v=static.rated_voltage_v * (0.85 + 0.15 * soc_pct / 100),
        current_a=0.0,
        speed_kmh=0.0,
        is_charging=False,
        charging_power_kw=0.0,
        behavior_mode="IDLE",
        sequence_no=0,
        day_start_odometer_km=static.initial_odometer_km,
    )
