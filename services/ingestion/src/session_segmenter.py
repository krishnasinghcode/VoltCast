"""Charging session interval segmentation — ingestionMaster.md §6.

Single-pass, O(1)-per-event, O(1)-memory streaming state machine over `is_charging` transitions
per vehicle. Also tracks deep-discharge crossings (soc_pct < 20 -> back above) with the same
pattern.
"""
from __future__ import annotations

from datetime import datetime

from . import config
from .postgres_writer import PostgresWriter
from .windowed_aggregator import WindowedAggregator

DEEP_DISCHARGE_SOC_PCT = 20.0


class _ChargingSession:
    __slots__ = ("started_at", "start_soc", "energy_accum", "max_power_seen")

    def __init__(self, started_at: datetime, start_soc: float):
        self.started_at = started_at
        self.start_soc = start_soc
        self.energy_accum = 0.0
        self.max_power_seen = 0.0


class SessionSegmenter:
    def __init__(self, postgres_writer: PostgresWriter, aggregator: WindowedAggregator):
        self._pg = postgres_writer
        self._agg = aggregator
        self._sessions: dict[str, _ChargingSession] = {}
        self._last_event: dict[str, dict] = {}
        self._below_deep_discharge: dict[str, bool] = {}

    def process(self, event: dict, ts: datetime):
        vehicle_id = event["vehicle_id"]
        is_charging = bool(event["is_charging"])
        last = self._last_event.get(vehicle_id)
        dt_hours = 0.0
        if last is not None and ts > last["timestamp"]:
            dt_hours = (ts - last["timestamp"]).total_seconds() / 3600.0

        session = self._sessions.get(vehicle_id)
        if is_charging and session is None:
            session = _ChargingSession(ts, event["soc_pct"])
            self._sessions[vehicle_id] = session
        elif is_charging and session is not None:
            session.energy_accum += event["charging_power_kw"] * dt_hours
            session.max_power_seen = max(session.max_power_seen, event["charging_power_kw"])
        elif not is_charging and session is not None:
            ended_at = last["timestamp"] if last is not None else ts
            end_soc = last["soc_pct"] if last is not None else event["soc_pct"]
            is_fast = session.max_power_seen > config.FAST_CHARGE_POWER_KW_THRESHOLD
            row = {
                "vehicle_id": vehicle_id,
                "started_at": session.started_at,
                "ended_at": ended_at,
                "start_soc": session.start_soc,
                "end_soc": end_soc,
                "energy_delivered_kwh": session.energy_accum,
                "max_charging_power_kw": session.max_power_seen,
                "charging_type": "FAST" if is_fast else "NORMAL",
            }
            self._pg.insert_charging_session(row)
            self._agg.record_charging_session_closed(vehicle_id, ended_at, is_fast)
            del self._sessions[vehicle_id]

        self._check_deep_discharge(vehicle_id, event, ts)
        self._last_event[vehicle_id] = {"timestamp": ts, "soc_pct": event["soc_pct"]}

    def _check_deep_discharge(self, vehicle_id: str, event: dict, ts: datetime):
        currently_below = event["soc_pct"] < DEEP_DISCHARGE_SOC_PCT
        was_below = self._below_deep_discharge.get(vehicle_id, False)
        if currently_below and not was_below:
            self._agg.record_deep_discharge(vehicle_id, ts)
        self._below_deep_discharge[vehicle_id] = currently_below
