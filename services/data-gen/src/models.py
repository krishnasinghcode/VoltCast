"""Pydantic request/response models and internal dataclasses for the data-gen service."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, field_validator


CONTRACT_VERSION = "1.0.0"


class SimulationMode(str, Enum):
    SHORT_BURST = "SHORT_BURST"
    LONGITUDINAL = "LONGITUDINAL"
    DEMO = "DEMO"


class SimulationStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RESET = "RESET"


class Archetype(str, Enum):
    HEALTHY = "HEALTHY"
    NORMAL = "NORMAL"
    STRESSED = "STRESSED"
    BAD = "BAD"


ARCHETYPE_MULTIPLIER = {
    Archetype.HEALTHY: 0.6,
    Archetype.NORMAL: 1.0,
    Archetype.STRESSED: 1.5,
    Archetype.BAD: 2.2,
}


class BurstConfig(BaseModel):
    enabled: bool = False
    duration_seconds: int = 60
    target_events_per_sec: int = 100000


class FaultInjectionConfig(BaseModel):
    duplicate_rate: float = 0.0
    out_of_order_rate: float = 0.0
    malformed_rate: float = 0.0


class SimulationConfig(BaseModel):
    seed: int = 42
    mode: SimulationMode = SimulationMode.LONGITUDINAL
    vehicle_count: int = 100000
    active_vehicle_count: Optional[int] = None
    duration_days: int = 90
    simulation_speed: float = 2880
    start_time: datetime
    archetype_distribution: dict[str, float] = field(
        default_factory=lambda: {"HEALTHY": 0.35, "NORMAL": 0.45, "STRESSED": 0.15, "BAD": 0.05}
    )  # type: ignore[call-overload]
    burst: BurstConfig = BurstConfig()
    fault_injection: FaultInjectionConfig = FaultInjectionConfig()

    @field_validator("archetype_distribution")
    @classmethod
    def validate_distribution(cls, v: dict[str, float]) -> dict[str, float]:
        if not v:
            return {"HEALTHY": 0.35, "NORMAL": 0.45, "STRESSED": 0.15, "BAD": 0.05}
        total = sum(v.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"archetype_distribution must sum to 1.0, got {total}")
        for key in v:
            if key not in Archetype.__members__:
                raise ValueError(f"unknown archetype '{key}'")
        return v


class SimulationResponse(BaseModel):
    simulation_id: UUID
    seed: int
    mode: SimulationMode
    status: SimulationStatus
    start_time: datetime
    duration_days: int
    simulation_speed: float
    created_at: datetime


class SimulationStatusResponse(BaseModel):
    simulation_id: UUID
    status: SimulationStatus
    mode: SimulationMode
    current_sim_time: Optional[datetime] = None
    events_produced: int = 0
    vehicles_count: int = 0


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = field(default_factory=dict)  # type: ignore[call-overload]


class ErrorResponse(BaseModel):
    error: ErrorDetail


@dataclass
class BehaviorMode:
    DRIVING: str = "DRIVING"
    CHARGING: str = "CHARGING"
    IDLE: str = "IDLE"


@dataclass
class VehicleStaticInfo:
    """Immutable per-vehicle info decided once at fleet-build time."""
    vehicle_id: str
    fleet_id: str
    model_id: str
    battery_id: str
    archetype: Archetype
    chemistry_id: str
    chemistry_name: str
    calendar_factor: float
    cycle_factor: float
    nominal_capacity_kwh: float
    rated_voltage_v: float
    internal_resistance_ohm: float
    manufacture_date: datetime
    initial_odometer_km: float
    aggressiveness: float  # archetype-driven driving current factor
    routine_seed: int  # seeds this vehicle's daily routine schedule
    regional_offset_c: float  # seeded regional ambient temperature offset
    commute_am_start_hr: float
    commute_am_end_hr: float
    commute_pm_start_hr: float
    commute_pm_end_hr: float
    charge_hour: float  # preferred overnight charging start hour


@dataclass
class VehicleDynamicState:
    """Mutable per-tick state for one vehicle within one simulation run."""
    soc_pct: float
    usable_capacity_kwh: float
    soh_pct: float
    odometer_km: float
    equivalent_full_cycles: float
    battery_temp_c: float
    voltage_v: float
    current_a: float
    speed_kmh: float
    is_charging: bool
    charging_power_kw: float
    behavior_mode: str
    sequence_no: int = 0
    thermal_exposure_hours: float = 0.0
    fast_charge_count: int = 0
    deep_discharge_count: int = 0
    # day-accumulator fields, reset at each simulated day boundary
    day_equivalent_full_cycles: float = 0.0
    day_hours_above_35c: float = 0.0
    day_fast_charge_sessions: int = 0
    day_deep_discharge_events: int = 0
    day_start_odometer_km: float = 0.0
    last_soc_below_20: bool = False
    in_fast_charge_session: bool = False
    last_day_index: int = -1
