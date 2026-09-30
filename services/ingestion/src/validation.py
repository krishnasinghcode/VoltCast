"""Event schema/range validation per CONTRACT.md §5."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from . import config

logger = logging.getLogger("ingestion.validation")

REQUIRED_FIELDS = [
    "event_id",
    "vehicle_id",
    "simulation_id",
    "timestamp",
    "sequence_no",
    "soc_pct",
    "voltage_v",
    "current_a",
    "temperature_c",
    "is_charging",
    "charging_power_kw",
    "speed_kmh",
    "odometer_km",
    "ambient_temperature_c",
    "contract_version",
]

RANGES = {
    "soc_pct": (0, 100),
    "voltage_v": (250, 450),
    "current_a": (-400, 400),
    "temperature_c": (-20, 80),
    "charging_power_kw": (0, 350),
    "speed_kmh": (0, 220),
    "ambient_temperature_c": (-30, 55),
}


class ValidationError(Exception):
    def __init__(self, reason: str, code: str = "VALIDATION_ERROR"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def parse_event(raw: bytes) -> dict:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValidationError(f"json parse failed: {exc}", code="PARSE_ERROR")
    if not isinstance(data, dict):
        raise ValidationError("event is not a JSON object", code="PARSE_ERROR")
    return data


def validate_event(event: dict) -> None:
    """Raises ValidationError if the event fails schema/range/contract checks."""
    missing = [f for f in REQUIRED_FIELDS if f not in event]
    if missing:
        raise ValidationError(f"missing required fields: {missing}", code="MISSING_FIELD")

    for field, (lo, hi) in RANGES.items():
        value = event[field]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValidationError(f"field {field} is not numeric", code="TYPE_ERROR")
        if value < lo or value > hi:
            raise ValidationError(
                f"field {field}={value} outside range [{lo}, {hi}]", code="RANGE_ERROR"
            )

    if event["contract_version"] != config.CONTRACT_VERSION:
        raise ValidationError(
            f"contract_version mismatch: event={event['contract_version']} "
            f"service={config.CONTRACT_VERSION}",
            code="CONTRACT_VERSION_MISMATCH",
        )

    try:
        parse_timestamp(event["timestamp"])
    except (ValueError, TypeError) as exc:
        raise ValidationError(f"bad timestamp: {exc}", code="TYPE_ERROR")


def parse_timestamp(ts: Any) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
