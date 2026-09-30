"""Per-tick vehicle physics update — the realism contract (dataGenMaster.md §6).

State evolves by bounded delta from previous state, never resampled independently. Every
continuous field follows the same pattern: exponential-approach-to-target with a max
per-tick step, plus small bounded noise. Called once per simulated second per vehicle.
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from src.config import (
    DEEP_DISCHARGE_SOC_THRESHOLD,
    FAST_CHARGE_POWER_FRACTION,
    THERMAL_THRESHOLD_C,
)
from src.degradation import apply_degradation, compute_daily_degradation
from src.models import VehicleDynamicState, VehicleStaticInfo

MAX_SPEED_STEP_KMH = 5.0
MAX_CHARGE_POWER_STEP_KW = 8.0
MAX_TEMP_STEP_C = 0.05
IDLE_CURRENT_DECAY_A = 0.5
IDLE_PARASITIC_DRAW_A = 0.5
VOLTAGE_NOISE_STD_FRACTION = 0.003
SELF_DISCHARGE_PCT_PER_TICK = 0.0000008

STICKY_STAY_PROB = 0.98

DRIVING = "DRIVING"
CHARGING = "CHARGING"
IDLE = "IDLE"


def ambient_temperature_c(sim_time: datetime, regional_offset_c: float) -> float:
    """Smooth daily sine curve + small seeded regional offset, not random per tick."""
    hour_frac = sim_time.hour + sim_time.minute / 60 + sim_time.second / 3600
    day_of_year = sim_time.timetuple().tm_yday
    # daily cycle: coolest ~4am, warmest ~4pm
    daily = 10 * math.sin((hour_frac - 10) / 24 * 2 * math.pi)
    # seasonal drift, mild
    seasonal = 5 * math.sin((day_of_year / 365) * 2 * math.pi)
    return 20.0 + daily + seasonal + regional_offset_c


def _hour_frac(sim_time: datetime) -> float:
    return sim_time.hour + sim_time.minute / 60 + sim_time.second / 3600


def _preferred_mode(static: VehicleStaticInfo, sim_time: datetime) -> str:
    hour = _hour_frac(sim_time)
    if static.commute_am_start_hr <= hour < static.commute_am_end_hr:
        return DRIVING
    if static.commute_pm_start_hr <= hour < static.commute_pm_end_hr:
        return DRIVING
    charge_end = (static.charge_hour + 6) % 24
    if static.charge_hour <= charge_end:
        in_charge_window = static.charge_hour <= hour < charge_end
    else:
        in_charge_window = hour >= static.charge_hour or hour < charge_end
    if in_charge_window:
        return CHARGING
    return IDLE


def decide_behavior_mode(static: VehicleStaticInfo, state: VehicleDynamicState,
                          sim_time: datetime, rng: np.random.Generator) -> str:
    """Sticky Markov-ish transition: 98% stay in current mode, 2% transition toward the
    routine's preferred mode for this time window, so states last minutes, not single ticks."""
    if rng.random() < STICKY_STAY_PROB:
        return state.behavior_mode
    preferred = _preferred_mode(static, sim_time)
    if preferred == CHARGING and state.soc_pct >= 99.5:
        return IDLE
    return preferred


def _approach(current: float, target: float, max_step: float) -> float:
    delta = target - current
    step = max(-max_step, min(max_step, delta))
    return current + step


def tick(static: VehicleStaticInfo, state: VehicleDynamicState, sim_time: datetime,
         rng: np.random.Generator) -> None:
    ambient = ambient_temperature_c(sim_time, static.regional_offset_c)
    state.behavior_mode = decide_behavior_mode(static, state, sim_time, rng)

    if state.behavior_mode == DRIVING:
        _tick_driving(static, state, rng)
    elif state.behavior_mode == CHARGING:
        _tick_charging(static, state, rng)
    else:
        _tick_idle(static, state, rng)

    _update_voltage(static, state, rng)
    _update_temperature(static, state, ambient)
    _accumulate_daily_stats(static, state)


def _tick_driving(static: VehicleStaticInfo, state: VehicleDynamicState,
                   rng: np.random.Generator) -> None:
    target_speed = float(rng.uniform(20, 110))
    state.speed_kmh = max(0.0, _approach(state.speed_kmh, target_speed, MAX_SPEED_STEP_KMH))

    base_current = 0.6 * state.speed_kmh  # amps, rough discharge-current-vs-speed model
    state.current_a = base_current * static.aggressiveness

    state.odometer_km += state.speed_kmh / 3600.0

    soc_delta = (state.current_a * static.rated_voltage_v) / (
        state.usable_capacity_kwh * 1000 * 3600
    ) * 100
    state.soc_pct = max(0.0, state.soc_pct - abs(soc_delta))

    state.is_charging = False
    state.charging_power_kw = 0.0


def _tick_charging(static: VehicleStaticInfo, state: VehicleDynamicState,
                    rng: np.random.Generator) -> None:
    state.is_charging = True
    max_power = 150.0 if static.nominal_capacity_kwh > 65 else 90.0
    target_power = max_power * (1.0 - max(0.0, (state.soc_pct - 80) / 20)) if state.soc_pct > 80 else max_power
    state.charging_power_kw = max(0.0, _approach(state.charging_power_kw, target_power, MAX_CHARGE_POWER_STEP_KW))

    state.current_a = -(state.charging_power_kw * 1000) / max(state.voltage_v, 1.0)

    soc_delta = (state.charging_power_kw * 1000) / (
        state.usable_capacity_kwh * 1000 * 3600
    ) * 100
    state.soc_pct = min(100.0, state.soc_pct + soc_delta)

    if state.charging_power_kw > FAST_CHARGE_POWER_FRACTION * max_power:
        if not state.in_fast_charge_session:
            state.in_fast_charge_session = True
            state.day_fast_charge_sessions += 1
    else:
        state.in_fast_charge_session = False

    state.speed_kmh = 0.0


def _tick_idle(static: VehicleStaticInfo, state: VehicleDynamicState,
               rng: np.random.Generator) -> None:
    state.current_a = _approach(state.current_a, 0.0, IDLE_CURRENT_DECAY_A)
    state.soc_pct = max(0.0, state.soc_pct - SELF_DISCHARGE_PCT_PER_TICK)
    state.speed_kmh = 0.0
    state.is_charging = False
    state.charging_power_kw = 0.0
    state.in_fast_charge_session = False


def _update_voltage(static: VehicleStaticInfo, state: VehicleDynamicState,
                     rng: np.random.Generator) -> None:
    base_voltage = static.rated_voltage_v * (0.85 + 0.15 * state.soc_pct / 100)
    voltage = base_voltage - (state.current_a * static.internal_resistance_ohm)
    noise = rng.normal(0, VOLTAGE_NOISE_STD_FRACTION * abs(voltage))
    state.voltage_v = voltage + noise


def _update_temperature(static: VehicleStaticInfo, state: VehicleDynamicState,
                         ambient_c: float) -> None:
    k_heat = 0.08
    target_temp = ambient_c + k_heat * abs(state.current_a)
    state.battery_temp_c = _approach(state.battery_temp_c, target_temp, MAX_TEMP_STEP_C)


def _accumulate_daily_stats(static: VehicleStaticInfo, state: VehicleDynamicState) -> None:
    if state.battery_temp_c > THERMAL_THRESHOLD_C:
        state.day_hours_above_35c += 1.0 / 3600.0

    distance_this_tick_km = state.odometer_km - state.day_start_odometer_km
    # equivalent full cycles: track via charge/discharge throughput
    if abs(state.current_a) > 0.01:
        ah_this_tick = abs(state.current_a) / 3600.0
        nominal_ah = (static.nominal_capacity_kwh * 1000) / static.rated_voltage_v
        state.day_equivalent_full_cycles += ah_this_tick / (2 * nominal_ah)
        state.equivalent_full_cycles += ah_this_tick / (2 * nominal_ah)

    is_below_20 = state.soc_pct < DEEP_DISCHARGE_SOC_THRESHOLD
    if is_below_20 and not state.last_soc_below_20:
        pass  # entering deep discharge, count on the way back up (a full event = dip + recovery)
    if not is_below_20 and state.last_soc_below_20:
        state.day_deep_discharge_events += 1
    state.last_soc_below_20 = is_below_20


def apply_daily_rollup(static: VehicleStaticInfo, state: VehicleDynamicState) -> None:
    """Once per simulated DAY boundary: compute degradation.py's daily formula from the
    ticks accumulated that day, apply it to SOH, then reset the day accumulators."""
    daily_degradation_pct = compute_daily_degradation(
        chemistry_calendar_factor=static.calendar_factor,
        chemistry_cycle_factor=static.cycle_factor,
        equivalent_full_cycles_today=state.day_equivalent_full_cycles,
        hours_above_35c_today=state.day_hours_above_35c,
        fast_charge_sessions_today=state.day_fast_charge_sessions,
        deep_discharge_events_today=state.day_deep_discharge_events,
        archetype=static.archetype,
    )
    state.soh_pct = apply_degradation(state.soh_pct, daily_degradation_pct)
    state.usable_capacity_kwh = static.nominal_capacity_kwh * state.soh_pct / 100

    state.thermal_exposure_hours += state.day_hours_above_35c
    state.fast_charge_count += state.day_fast_charge_sessions
    state.deep_discharge_count += state.day_deep_discharge_events

    state.day_equivalent_full_cycles = 0.0
    state.day_hours_above_35c = 0.0
    state.day_fast_charge_sessions = 0
    state.day_deep_discharge_events = 0
    state.day_start_odometer_km = state.odometer_km
