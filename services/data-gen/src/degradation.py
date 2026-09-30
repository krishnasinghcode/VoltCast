"""Implements CONTRACT.md §9 degradation formula exactly. Same constant names as the contract —
this is the one place a deviation from CONTRACT.md would silently break ML's label construction."""
from src.config import (
    DAILY_DEGRADATION_MAX,
    K_CAL,
    K_CYC,
    K_DEEP,
    K_FAST,
    K_THERM,
    SOH_FLOOR,
)
from src.models import ARCHETYPE_MULTIPLIER, Archetype


def compute_daily_degradation(
    chemistry_calendar_factor: float,
    chemistry_cycle_factor: float,
    equivalent_full_cycles_today: float,
    hours_above_35c_today: float,
    fast_charge_sessions_today: int,
    deep_discharge_events_today: int,
    archetype: Archetype,
) -> float:
    """SOH(t) = 100 - cumulative_degradation_pct(t)

    daily_degradation_pct =
          calendar_term
        + cycle_term
        + archetype_multiplier * (thermal_term + fastcharge_term + deepdischarge_term)

    Clipped to [0, 0.05] before being added to cumulative_degradation_pct.
    """
    calendar_term = K_CAL * chemistry_calendar_factor
    cycle_term = K_CYC * chemistry_cycle_factor * equivalent_full_cycles_today
    thermal_term = K_THERM * hours_above_35c_today
    fastcharge_term = K_FAST * fast_charge_sessions_today
    deepdischarge_term = K_DEEP * deep_discharge_events_today

    archetype_multiplier = ARCHETYPE_MULTIPLIER[archetype]

    daily_degradation_pct = calendar_term + cycle_term + archetype_multiplier * (
        thermal_term + fastcharge_term + deepdischarge_term
    )

    return max(0.0, min(DAILY_DEGRADATION_MAX, daily_degradation_pct))


def apply_degradation(current_soh_pct: float, daily_degradation_pct: float) -> float:
    """SOH is floored at 60.0."""
    return max(SOH_FLOOR, current_soh_pct - daily_degradation_pct)
