"""The clock loop: advances sim time, ticks the active vehicle fleet, calls the producer.

Physics for the whole active fleet is vectorized with numpy (one array per field, indexed by
vehicle position) instead of a per-vehicle Python object loop. This is a documented performance
necessity: dataGenMaster.md §5 requires physics to update every simulated second regardless of
mode, and LONGITUDINAL's ~90 simulated days x up to a few thousand vehicles is ~10^10
vehicle-ticks — intractable as a per-vehicle Python loop, tractable as a numpy-vectorized one.
The scalar per-vehicle formulas in vehicle_state.py are the reference implementation (and what
tests/test_vehicle_state.py checks bounded-step behavior against); the vectorized arrays below
apply the identical formulas across the whole fleet at once.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from src import db
from src.config import (
    DEEP_DISCHARGE_SOC_THRESHOLD,
    FAST_CHARGE_POWER_FRACTION,
    THERMAL_THRESHOLD_C,
)
from src.degradation import compute_daily_degradation
from src.event_builder import FaultInjector, build_event
from src.kafka_producer import TelemetryProducer, ensure_topic_exists
from src.models import (
    ARCHETYPE_MULTIPLIER,
    FaultInjectionConfig,
    SimulationConfig,
    SimulationMode,
    SimulationStatus,
    VehicleStaticInfo,
)
from src.vehicle_state import ambient_temperature_c

logger = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400
MAX_SPEED_STEP_KMH = 5.0
MAX_CHARGE_POWER_STEP_KW = 8.0
MAX_TEMP_STEP_C = 0.05
IDLE_CURRENT_DECAY_A = 0.5
VOLTAGE_NOISE_STD_FRACTION = 0.003
SELF_DISCHARGE_PCT_PER_TICK = 0.0000008
STICKY_STAY_PROB = 0.98

MODE_IDLE, MODE_DRIVING, MODE_CHARGING = 0, 1, 2

EMIT_INTERVAL_SECONDS = {
    SimulationMode.SHORT_BURST: 1,
    SimulationMode.LONGITUDINAL: 300,
}


@dataclass
class FleetArrays:
    vehicle_ids: list[str]
    aggressiveness: np.ndarray
    rated_voltage_v: np.ndarray
    nominal_capacity_kwh: np.ndarray
    internal_resistance_ohm: np.ndarray
    regional_offset_c: np.ndarray
    commute_am_start: np.ndarray
    commute_am_end: np.ndarray
    commute_pm_start: np.ndarray
    commute_pm_end: np.ndarray
    charge_hour: np.ndarray
    calendar_factor: np.ndarray
    cycle_factor: np.ndarray
    archetype_multiplier: np.ndarray

    soc_pct: np.ndarray
    usable_capacity_kwh: np.ndarray
    soh_pct: np.ndarray
    odometer_km: np.ndarray
    equivalent_full_cycles: np.ndarray
    battery_temp_c: np.ndarray
    voltage_v: np.ndarray
    current_a: np.ndarray
    speed_kmh: np.ndarray
    is_charging: np.ndarray
    charging_power_kw: np.ndarray
    behavior_mode: np.ndarray
    sequence_no: np.ndarray

    day_equivalent_full_cycles: np.ndarray
    day_hours_above_35c: np.ndarray
    day_fast_charge_sessions: np.ndarray
    day_deep_discharge_events: np.ndarray
    day_start_odometer_km: np.ndarray
    last_soc_below_20: np.ndarray
    in_fast_charge_session: np.ndarray

    thermal_exposure_hours: np.ndarray
    fast_charge_count: np.ndarray
    deep_discharge_count: np.ndarray


def build_fleet_arrays(statics: list[VehicleStaticInfo], seed: int, start_time: datetime) -> FleetArrays:
    n = len(statics)
    rng = np.random.default_rng(seed)

    soc_pct = rng.uniform(20, 90, size=n)
    ages_years = np.array([
        max(0.0, (start_time - s.manufacture_date).days / 365.0) for s in statics
    ])
    soh_pct = np.maximum(90.0, 100 - ages_years * 0.8)
    nominal_capacity_kwh = np.array([s.nominal_capacity_kwh for s in statics])
    rated_voltage_v = np.array([s.rated_voltage_v for s in statics])
    odometer_km = np.array([s.initial_odometer_km for s in statics])

    return FleetArrays(
        vehicle_ids=[s.vehicle_id for s in statics],
        aggressiveness=np.array([s.aggressiveness for s in statics]),
        rated_voltage_v=rated_voltage_v,
        nominal_capacity_kwh=nominal_capacity_kwh,
        internal_resistance_ohm=np.array([s.internal_resistance_ohm for s in statics]),
        regional_offset_c=np.array([s.regional_offset_c for s in statics]),
        commute_am_start=np.array([s.commute_am_start_hr for s in statics]),
        commute_am_end=np.array([s.commute_am_end_hr for s in statics]),
        commute_pm_start=np.array([s.commute_pm_start_hr for s in statics]),
        commute_pm_end=np.array([s.commute_pm_end_hr for s in statics]),
        charge_hour=np.array([s.charge_hour for s in statics]),
        calendar_factor=np.array([s.calendar_factor for s in statics]),
        cycle_factor=np.array([s.cycle_factor for s in statics]),
        archetype_multiplier=np.array([ARCHETYPE_MULTIPLIER[s.archetype] for s in statics]),
        soc_pct=soc_pct,
        usable_capacity_kwh=nominal_capacity_kwh * soh_pct / 100,
        soh_pct=soh_pct,
        odometer_km=odometer_km,
        equivalent_full_cycles=ages_years * 150,
        battery_temp_c=np.full(n, 25.0),
        voltage_v=rated_voltage_v * (0.85 + 0.15 * soc_pct / 100),
        current_a=np.zeros(n),
        speed_kmh=np.zeros(n),
        is_charging=np.zeros(n, dtype=bool),
        charging_power_kw=np.zeros(n),
        behavior_mode=np.full(n, MODE_IDLE, dtype=np.int8),
        sequence_no=np.zeros(n, dtype=np.int64),
        day_equivalent_full_cycles=np.zeros(n),
        day_hours_above_35c=np.zeros(n),
        day_fast_charge_sessions=np.zeros(n, dtype=np.int64),
        day_deep_discharge_events=np.zeros(n, dtype=np.int64),
        day_start_odometer_km=odometer_km.copy(),
        last_soc_below_20=np.zeros(n, dtype=bool),
        in_fast_charge_session=np.zeros(n, dtype=bool),
        thermal_exposure_hours=np.zeros(n),
        fast_charge_count=np.zeros(n, dtype=np.int64),
        deep_discharge_count=np.zeros(n, dtype=np.int64),
    )


def _hour_frac(sim_time: datetime) -> float:
    return sim_time.hour + sim_time.minute / 60 + sim_time.second / 3600


def _preferred_mode_vec(f: FleetArrays, sim_time: datetime) -> np.ndarray:
    hour = _hour_frac(sim_time)
    am = (f.commute_am_start <= hour) & (hour < f.commute_am_end)
    pm = (f.commute_pm_start <= hour) & (hour < f.commute_pm_end)
    charge_end = (f.charge_hour + 6) % 24
    wraps = f.charge_hour > charge_end
    in_charge = np.where(
        wraps,
        (hour >= f.charge_hour) | (hour < charge_end),
        (f.charge_hour <= hour) & (hour < charge_end),
    )
    preferred = np.full(f.behavior_mode.shape, MODE_IDLE, dtype=np.int8)
    preferred[in_charge] = MODE_CHARGING
    preferred[am | pm] = MODE_DRIVING
    return preferred


def tick_fleet(f: FleetArrays, sim_time: datetime, rng: np.random.Generator) -> None:
    n = len(f.vehicle_ids)
    ambient = ambient_temperature_c(sim_time, 0.0) - 0.0  # base without regional offset
    ambient_vec = ambient + f.regional_offset_c

    transition_mask = rng.random(n) >= STICKY_STAY_PROB
    if transition_mask.any():
        preferred = _preferred_mode_vec(f, sim_time)
        want_charge_but_full = (preferred == MODE_CHARGING) & (f.soc_pct >= 99.5)
        preferred = np.where(want_charge_but_full, MODE_IDLE, preferred)
        f.behavior_mode = np.where(transition_mask, preferred, f.behavior_mode)

    driving = f.behavior_mode == MODE_DRIVING
    charging = f.behavior_mode == MODE_CHARGING
    idle = f.behavior_mode == MODE_IDLE

    # --- DRIVING ---
    if driving.any():
        target_speed = rng.uniform(20, 110, size=n)
        new_speed = f.speed_kmh + np.clip(target_speed - f.speed_kmh, -MAX_SPEED_STEP_KMH, MAX_SPEED_STEP_KMH)
        f.speed_kmh = np.where(driving, np.maximum(0.0, new_speed), f.speed_kmh)

        drive_current = 0.6 * f.speed_kmh * f.aggressiveness
        f.current_a = np.where(driving, drive_current, f.current_a)
        f.odometer_km = np.where(driving, f.odometer_km + f.speed_kmh / 3600.0, f.odometer_km)

        soc_delta = np.abs(f.current_a * f.rated_voltage_v) / (f.usable_capacity_kwh * 1000 * 3600) * 100
        f.soc_pct = np.where(driving, np.maximum(0.0, f.soc_pct - soc_delta), f.soc_pct)
        f.is_charging = np.where(driving, False, f.is_charging)
        f.charging_power_kw = np.where(driving, 0.0, f.charging_power_kw)

    # --- CHARGING ---
    if charging.any():
        max_power = np.where(f.nominal_capacity_kwh > 65, 150.0, 90.0)
        target_power = np.where(
            f.soc_pct > 80,
            max_power * (1.0 - np.maximum(0.0, (f.soc_pct - 80) / 20)),
            max_power,
        )
        new_power = f.charging_power_kw + np.clip(
            target_power - f.charging_power_kw, -MAX_CHARGE_POWER_STEP_KW, MAX_CHARGE_POWER_STEP_KW
        )
        new_power = np.maximum(0.0, new_power)
        f.charging_power_kw = np.where(charging, new_power, f.charging_power_kw)
        f.current_a = np.where(charging, -(f.charging_power_kw * 1000) / np.maximum(f.voltage_v, 1.0), f.current_a)

        soc_delta = (f.charging_power_kw * 1000) / (f.usable_capacity_kwh * 1000 * 3600) * 100
        new_soc = np.minimum(100.0, f.soc_pct + soc_delta)
        f.soc_pct = np.where(charging, new_soc, f.soc_pct)

        is_fast = charging & (f.charging_power_kw > FAST_CHARGE_POWER_FRACTION * max_power)
        newly_fast = is_fast & (~f.in_fast_charge_session)
        f.day_fast_charge_sessions = np.where(newly_fast, f.day_fast_charge_sessions + 1, f.day_fast_charge_sessions)
        f.in_fast_charge_session = np.where(charging, is_fast, f.in_fast_charge_session)

        f.is_charging = np.where(charging, True, f.is_charging)
        f.speed_kmh = np.where(charging, 0.0, f.speed_kmh)

    # --- IDLE ---
    if idle.any():
        new_current = f.current_a + np.clip(0.0 - f.current_a, -IDLE_CURRENT_DECAY_A, IDLE_CURRENT_DECAY_A)
        f.current_a = np.where(idle, new_current, f.current_a)
        f.soc_pct = np.where(idle, np.maximum(0.0, f.soc_pct - SELF_DISCHARGE_PCT_PER_TICK), f.soc_pct)
        f.speed_kmh = np.where(idle, 0.0, f.speed_kmh)
        f.is_charging = np.where(idle, False, f.is_charging)
        f.charging_power_kw = np.where(idle, 0.0, f.charging_power_kw)
        f.in_fast_charge_session = np.where(idle, False, f.in_fast_charge_session)

    # --- voltage ---
    base_voltage = f.rated_voltage_v * (0.85 + 0.15 * f.soc_pct / 100)
    voltage = base_voltage - (f.current_a * f.internal_resistance_ohm)
    noise = rng.normal(0, VOLTAGE_NOISE_STD_FRACTION * np.abs(voltage) + 1e-9)
    f.voltage_v = voltage + noise

    # --- temperature ---
    k_heat = 0.08
    target_temp = ambient_vec + k_heat * np.abs(f.current_a)
    f.battery_temp_c = f.battery_temp_c + np.clip(target_temp - f.battery_temp_c, -MAX_TEMP_STEP_C, MAX_TEMP_STEP_C)

    # --- daily accumulators ---
    above_thresh = f.battery_temp_c > THERMAL_THRESHOLD_C
    f.day_hours_above_35c = f.day_hours_above_35c + np.where(above_thresh, 1.0 / 3600.0, 0.0)

    has_current = np.abs(f.current_a) > 0.01
    nominal_ah = (f.nominal_capacity_kwh * 1000) / f.rated_voltage_v
    ah_tick = np.abs(f.current_a) / 3600.0
    efc_delta = np.where(has_current, ah_tick / (2 * nominal_ah), 0.0)
    f.day_equivalent_full_cycles = f.day_equivalent_full_cycles + efc_delta
    f.equivalent_full_cycles = f.equivalent_full_cycles + efc_delta

    is_below_20 = f.soc_pct < DEEP_DISCHARGE_SOC_THRESHOLD
    recovered = (~is_below_20) & f.last_soc_below_20
    f.day_deep_discharge_events = np.where(recovered, f.day_deep_discharge_events + 1, f.day_deep_discharge_events)
    f.last_soc_below_20 = is_below_20


def apply_daily_rollup_fleet(f: FleetArrays) -> None:
    daily_degradation = (
        0.0008 * f.calendar_factor
        + 0.015 * f.cycle_factor * f.day_equivalent_full_cycles
        + f.archetype_multiplier
        * (
            0.004 * f.day_hours_above_35c
            + 0.01 * f.day_fast_charge_sessions
            + 0.008 * f.day_deep_discharge_events
        )
    )
    daily_degradation = np.clip(daily_degradation, 0.0, 0.05)
    f.soh_pct = np.maximum(60.0, f.soh_pct - daily_degradation)
    f.usable_capacity_kwh = f.nominal_capacity_kwh * f.soh_pct / 100

    f.thermal_exposure_hours += f.day_hours_above_35c
    f.fast_charge_count += f.day_fast_charge_sessions
    f.deep_discharge_count += f.day_deep_discharge_events

    f.day_equivalent_full_cycles[:] = 0.0
    f.day_hours_above_35c[:] = 0.0
    f.day_fast_charge_sessions[:] = 0
    f.day_deep_discharge_events[:] = 0
    f.day_start_odometer_km = f.odometer_km.copy()


class SimulationRunner:
    def __init__(self, simulation_id: str, config: SimulationConfig, statics: list[VehicleStaticInfo]):
        self.simulation_id = simulation_id
        self.config = config
        self.status = SimulationStatus.CREATED
        self.events_produced = 0
        self.sim_time = config.start_time
        if self.sim_time.tzinfo is None:
            self.sim_time = self.sim_time.replace(tzinfo=timezone.utc)

        if config.mode == SimulationMode.SHORT_BURST:
            active_statics = statics
        else:
            active_count = min(config.active_vehicle_count or 1500, len(statics))
            active_statics = statics[:active_count]

        self.active_statics = active_statics
        self.fleet = build_fleet_arrays(active_statics, config.seed, self.sim_time)
        self.rng = np.random.default_rng(config.seed)
        self.fault_injectors = {
            s.vehicle_id: FaultInjector(config.fault_injection) for s in active_statics
        }
        self.producer = TelemetryProducer()
        self._task: Optional[asyncio.Task] = None
        self._paused = asyncio.Event()
        self._paused.set()
        self._stop_requested = False
        self._tick_index = 0
        self._last_day_index = -1
        self._run_started_at: Optional[float] = None  # time.monotonic(), set on first start()

    def emit_interval(self) -> int:
        return EMIT_INTERVAL_SECONDS.get(self.config.mode, 1)

    def total_ticks(self) -> int:
        return self.config.duration_days * SECONDS_PER_DAY

    async def start(self) -> None:
        if self.status == SimulationStatus.RUNNING:
            return
        ensure_topic_exists()
        self.status = SimulationStatus.RUNNING
        with db.session() as conn:
            db.update_simulation_status(conn, self.simulation_id, self.status.value)
        self._stop_requested = False
        if self._run_started_at is None:
            self._run_started_at = time.monotonic()
        self._task = asyncio.create_task(self._run_loop())

    async def pause(self) -> None:
        self.status = SimulationStatus.PAUSED
        self._paused.clear()
        with db.session() as conn:
            db.update_simulation_status(conn, self.simulation_id, self.status.value)

    async def resume(self) -> None:
        self.status = SimulationStatus.RUNNING
        self._paused.set()
        with db.session() as conn:
            db.update_simulation_status(conn, self.simulation_id, self.status.value)

    async def reset(self) -> None:
        self._stop_requested = True
        if self._task:
            self._task.cancel()
        with db.session() as conn:
            db.clear_simulation_vehicle_state(conn, self.simulation_id)
        self.sim_time = self.config.start_time
        self.fleet = build_fleet_arrays(self.active_statics, self.config.seed, self.sim_time)
        self.events_produced = 0
        self._tick_index = 0
        self._last_day_index = -1
        self._run_started_at = None
        self.status = SimulationStatus.RESET
        with db.session() as conn:
            db.update_simulation_status(conn, self.simulation_id, self.status.value)

    async def _run_loop(self) -> None:
        try:
            speed = max(1.0, self.config.simulation_speed)
            emit_every = self.emit_interval()
            total = self.total_ticks()

            while self._tick_index < total and not self._stop_requested:
                await self._paused.wait()

                batch_size = min(int(speed), total - self._tick_index)
                for _ in range(max(1, batch_size)):
                    tick_fleet(self.fleet, self.sim_time, self.rng)
                    day_index = self._tick_index // SECONDS_PER_DAY
                    if day_index != self._last_day_index and self._tick_index > 0:
                        apply_daily_rollup_fleet(self.fleet)
                        self._snapshot_to_db()
                        self._last_day_index = day_index

                    if self._tick_index % emit_every == 0:
                        self._emit_events()
                        # Keep HTTP status/control routes responsive while a large simulation
                        # batch is doing synchronous NumPy work on the service event loop.
                        await asyncio.sleep(0)

                    self.sim_time += timedelta(seconds=1)
                    self._tick_index += 1

                await asyncio.sleep(1.0)

            self._flush_fault_injectors()
            # Advance the event-time watermark beyond the final simulation day so ingestion
            # closes and persists that day's aggregate instead of leaving it in memory.
            self._emit_events(self.sim_time + timedelta(hours=1))
            self._flush_fault_injectors()
            undelivered = self.producer.flush(60)
            if undelivered:
                raise RuntimeError(f"Kafka producer still has {undelivered} undelivered event(s)")
            self._snapshot_to_db()
            self.status = SimulationStatus.COMPLETED
            with db.session() as conn:
                db.update_simulation_status(conn, self.simulation_id, self.status.value)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("simulation %s failed", self.simulation_id)
            self.status = SimulationStatus.FAILED
            with db.session() as conn:
                db.update_simulation_status(conn, self.simulation_id, self.status.value)

    def _emit_events(self, event_time: Optional[datetime] = None) -> None:
        f = self.fleet
        event_time = event_time or self.sim_time
        ambient_base = ambient_temperature_c(event_time, 0.0)
        for i, vehicle_id in enumerate(f.vehicle_ids):
            static = self.active_statics[i]
            state_snapshot = _RowView(f, i)
            event = build_event(
                static, state_snapshot, self.simulation_id, event_time,
                ambient_base + f.regional_offset_c[i],
            )
            f.sequence_no[i] = state_snapshot.sequence_no
            injector = self.fault_injectors[vehicle_id]
            for out_event in injector.process(event, self.rng):
                self.producer.send(vehicle_id, out_event)
                self.events_produced += 1
        self.producer.poll(0)

    def _flush_fault_injectors(self) -> None:
        for vehicle_id, injector in self.fault_injectors.items():
            for out_event in injector.flush():
                self.producer.send(vehicle_id, out_event)
                self.events_produced += 1
        self.producer.poll(0)

    def _snapshot_to_db(self) -> None:
        f = self.fleet
        rows = [
            {
                "simulation_id": self.simulation_id,
                "vehicle_id": f.vehicle_ids[i],
                "sim_timestamp": self.sim_time,
                "soc_pct": float(f.soc_pct[i]),
                "usable_capacity_kwh": float(f.usable_capacity_kwh[i]),
                "soh_pct": float(f.soh_pct[i]),
                "odometer_km": float(f.odometer_km[i]),
                "equivalent_full_cycles": float(f.equivalent_full_cycles[i]),
                "battery_temp_c": float(f.battery_temp_c[i]),
                "thermal_exposure_hours": float(f.thermal_exposure_hours[i]),
                "fast_charge_count": int(f.fast_charge_count[i]),
                "deep_discharge_count": int(f.deep_discharge_count[i]),
            }
            for i in range(len(f.vehicle_ids))
        ]
        with db.session() as conn:
            db.upsert_simulation_vehicle_state(conn, rows)

    def status_payload(self) -> dict:
        total = self.total_ticks()
        progress_pct = round(100.0 * self._tick_index / total, 1) if total else 0.0

        eta_seconds: Optional[float] = None
        elapsed_seconds: Optional[float] = None
        if self._run_started_at is not None:
            elapsed_seconds = round(time.monotonic() - self._run_started_at, 1)
            # ETA from the ACTUAL observed rate so far (ticks/elapsed_seconds), not the configured
            # simulation_speed -- the configured speed is a target, not a guarantee; real throughput
            # can be slower under I/O contention (Kafka/DB), so this stays accurate even then.
            if self.status == SimulationStatus.RUNNING and self._tick_index > 0 and elapsed_seconds > 0:
                ticks_per_second = self._tick_index / elapsed_seconds
                remaining_ticks = total - self._tick_index
                eta_seconds = round(remaining_ticks / ticks_per_second, 1) if ticks_per_second > 0 else None
            elif self.status == SimulationStatus.COMPLETED:
                eta_seconds = 0.0

        return {
            "simulation_id": self.simulation_id,
            "status": self.status.value,
            "mode": self.config.mode.value,
            "current_sim_time": self.sim_time,
            "events_produced": self.events_produced,
            "vehicles_count": len(self.fleet.vehicle_ids),
            "progress_pct": progress_pct,
            "elapsed_seconds": elapsed_seconds,
            "eta_seconds": eta_seconds,
        }


class _RowView:
    """Adapts one row of FleetArrays to the scalar VehicleDynamicState-like interface
    expected by event_builder.build_event."""

    __slots__ = ("f", "i", "sequence_no", "soc_pct", "voltage_v", "current_a", "battery_temp_c",
                 "is_charging", "charging_power_kw", "speed_kmh", "odometer_km")

    def __init__(self, f: FleetArrays, i: int):
        self.f = f
        self.i = i
        self.sequence_no = int(f.sequence_no[i])
        self.soc_pct = float(f.soc_pct[i])
        self.voltage_v = float(f.voltage_v[i])
        self.current_a = float(f.current_a[i])
        self.battery_temp_c = float(f.battery_temp_c[i])
        self.is_charging = bool(f.is_charging[i])
        self.charging_power_kw = float(f.charging_power_kw[i])
        self.speed_kmh = float(f.speed_kmh[i])
        self.odometer_km = float(f.odometer_km[i])


RUNNERS: dict[str, SimulationRunner] = {}
