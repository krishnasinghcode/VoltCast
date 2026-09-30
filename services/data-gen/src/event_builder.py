"""Builds the canonical telemetry event (CONTRACT.md §5) and injects duplicate/out-of-order/
malformed faults into what is SENT (never into generation order — sequence_no always reflects
true generation order so ingestion can detect out-of-order arrivals correctly)."""
from __future__ import annotations

import uuid
from collections import deque
from datetime import datetime
from typing import Optional

import numpy as np

from src.config import CONTRACT_VERSION
from src.models import FaultInjectionConfig, VehicleDynamicState, VehicleStaticInfo


def build_event(static: VehicleStaticInfo, state: VehicleDynamicState, simulation_id: str,
                 sim_time: datetime, ambient_temperature_c: float) -> dict:
    """Assigns the next monotonic sequence_no for this vehicle and returns the canonical
    event dict matching CONTRACT.md §5."""
    state.sequence_no += 1
    return {
        "event_id": str(uuid.uuid4()),
        "vehicle_id": static.vehicle_id,
        "simulation_id": simulation_id,
        "timestamp": sim_time.isoformat().replace("+00:00", "Z"),
        "sequence_no": state.sequence_no,
        "soc_pct": round(state.soc_pct, 2),
        "voltage_v": round(state.voltage_v, 2),
        "current_a": round(state.current_a, 2),
        "temperature_c": round(state.battery_temp_c, 2),
        "is_charging": state.is_charging,
        "charging_power_kw": round(state.charging_power_kw, 2),
        "speed_kmh": round(state.speed_kmh, 2),
        "odometer_km": round(state.odometer_km, 3),
        "ambient_temperature_c": round(ambient_temperature_c, 2),
        "contract_version": CONTRACT_VERSION,
    }


def corrupt_event(event: dict, rng: np.random.Generator) -> dict:
    """Corrupt one field to prove ingestion's schema validation/quarantine path — these
    events are intentionally invalid, documented as a feature for the demo."""
    corrupted = dict(event)
    field_choice = rng.integers(0, 4)
    if field_choice == 0:
        corrupted["soc_pct"] = 150.0  # out of [0,100] range
    elif field_choice == 1:
        corrupted["voltage_v"] = -10.0  # out of [250,450] range
    elif field_choice == 2:
        corrupted["temperature_c"] = 999.0  # out of [-20,80] range
    else:
        corrupted.pop("odometer_km", None)  # drop a required field
    return corrupted


class FaultInjector:
    """Per-vehicle fault injection state machine. Call `process(event, rng)` with every
    true-order event; it returns the list of events to actually send this tick (0, 1, or
    more, in send order, which may differ from true generation order)."""

    def __init__(self, config: FaultInjectionConfig, max_delay_positions: int = 5):
        self._config = config
        self._max_delay = max_delay_positions
        self._delay_buffer: deque[tuple[int, dict]] = deque()  # (release_after_n_sends, event)
        self._pending_duplicates: deque[tuple[int, dict]] = deque()  # (release_after_n_sends, event)
        self._send_counter = 0

    def process(self, event: dict, rng: np.random.Generator) -> list[dict]:
        out: list[dict] = []

        if rng.random() < self._config.malformed_rate:
            event = corrupt_event(event, rng)

        if rng.random() < self._config.out_of_order_rate:
            delay = int(rng.integers(1, self._max_delay + 1))
            self._delay_buffer.append((self._send_counter + delay, event))
        else:
            out.append(event)
            self._send_counter += 1
            if rng.random() < self._config.duplicate_rate:
                resend_after = int(rng.integers(1, 4))
                self._pending_duplicates.append((self._send_counter + resend_after, event))

        out.extend(self._drain_ready(self._delay_buffer))
        out.extend(self._drain_ready(self._pending_duplicates))
        return out

    def flush(self) -> list[dict]:
        """Release every still-buffered delayed/duplicate event, in whatever order they were
        held. Call at simulation shutdown so no fault-injected event is silently lost."""
        out = [ev for _, ev in self._delay_buffer] + [ev for _, ev in self._pending_duplicates]
        self._delay_buffer.clear()
        self._pending_duplicates.clear()
        return out

    def _drain_ready(self, buf: deque[tuple[int, dict]]) -> list[dict]:
        ready = []
        remaining: deque[tuple[int, dict]] = deque()
        for release_at, ev in buf:
            if release_at <= self._send_counter:
                ready.append(ev)
                self._send_counter += 1
            else:
                remaining.append((release_at, ev))
        buf.clear()
        buf.extend(remaining)
        return ready
