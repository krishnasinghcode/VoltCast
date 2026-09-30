"""In-memory windowed aggregation (5-min / hourly / daily) — ingestionMaster.md §5.

No Flink/Spark/Kafka Streams (explicit scope cut). State is a plain dict keyed by
(vehicle_id, window_start), updated with O(1)-per-event streaming formulas (Welford's online
variance) so memory stays bounded regardless of event count. A periodic background tick flushes
closed windows to Postgres via UPSERT and evicts them from memory.

Known limitation (documented, not hidden): if this process crashes between flushes, in-flight
bucket state since the last flush is lost from memory. Recovery path: Kafka retains raw events
(`auto.offset.reset=earliest` on a fresh consumer group), so aggregates are recomputable by replay,
just not instantly.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import config
from .postgres_writer import PostgresWriter

logger = logging.getLogger("ingestion.windowed_aggregator")

THERMAL_THRESHOLD_C = 35.0


class WelfordStat:
    """Online mean/variance/min/max — O(1) memory per bucket regardless of event count."""

    __slots__ = ("count", "mean", "m2", "min", "max")

    def __init__(self):
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min: float | None = None
        self.max: float | None = None

    def update(self, x: float):
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        delta2 = x - self.mean
        self.m2 += delta * delta2
        self.min = x if self.min is None else min(self.min, x)
        self.max = x if self.max is None else max(self.max, x)

    @property
    def avg(self) -> float | None:
        return self.mean if self.count else None

    @property
    def variance(self) -> float | None:
        return (self.m2 / self.count) if self.count else None


def floor_to(ts: datetime, seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    floored = epoch - (epoch % seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc)


@dataclass
class FiveMinBucket:
    vehicle_id: str
    window_start: datetime
    soc: WelfordStat = field(default_factory=WelfordStat)
    temp: WelfordStat = field(default_factory=WelfordStat)
    current: WelfordStat = field(default_factory=WelfordStat)
    voltage: WelfordStat = field(default_factory=WelfordStat)
    charging_power: WelfordStat = field(default_factory=WelfordStat)
    charging_duration_s: float = 0.0
    energy_consumed_kwh: float = 0.0
    energy_charged_kwh: float = 0.0
    distance_km: float = 0.0
    event_count: int = 0

    def to_row(self) -> dict:
        return {
            "vehicle_id": self.vehicle_id,
            "window_start": self.window_start,
            "window_end": self.window_start + timedelta(minutes=5),
            "avg_soc": self.soc.avg, "min_soc": self.soc.min, "max_soc": self.soc.max,
            "avg_temp_c": self.temp.avg, "min_temp_c": self.temp.min, "max_temp_c": self.temp.max,
            "temp_variance": self.temp.variance,
            "avg_current_a": self.current.avg, "max_current_a": self.current.max,
            "avg_voltage_v": self.voltage.avg,
            "charging_duration_s": int(self.charging_duration_s),
            "avg_charging_power_kw": self.charging_power.avg,
            "max_charging_power_kw": self.charging_power.max,
            "energy_consumed_kwh": self.energy_consumed_kwh,
            "energy_charged_kwh": self.energy_charged_kwh,
            "distance_km": self.distance_km,
            "event_count": self.event_count,
        }


@dataclass
class HourlyBucket:
    vehicle_id: str
    hour_start: datetime
    soc: WelfordStat = field(default_factory=WelfordStat)
    energy_consumed_kwh: float = 0.0
    energy_charged_kwh: float = 0.0
    distance_km: float = 0.0
    charging_duration_s: float = 0.0
    high_temp_duration_s: float = 0.0
    max_temp_c: float | None = None
    charging_sessions: int = 0

    def to_row(self) -> dict:
        soc_range = (self.soc.max - self.soc.min) if self.soc.count else None
        return {
            "vehicle_id": self.vehicle_id,
            "hour_start": self.hour_start,
            "energy_consumed_kwh": self.energy_consumed_kwh,
            "energy_charged_kwh": self.energy_charged_kwh,
            "distance_km": self.distance_km,
            "charging_duration_s": int(self.charging_duration_s),
            "high_temp_duration_s": int(self.high_temp_duration_s),
            "max_temp_c": self.max_temp_c,
            "avg_soc": self.soc.avg,
            "soc_range": soc_range,
            "charging_sessions": self.charging_sessions,
        }


@dataclass
class DailyBucket:
    vehicle_id: str
    date: datetime
    distance_km: float = 0.0
    energy_consumed_kwh: float = 0.0
    energy_charged_kwh: float = 0.0
    charging_sessions: int = 0
    fast_charge_sessions: int = 0
    deep_discharge_events: int = 0
    high_temp_hours: float = 0.0
    max_temp_c: float | None = None

    def to_row(self, soh_pct: float | None, equivalent_full_cycles: float | None) -> dict:
        return {
            "vehicle_id": self.vehicle_id,
            "date": self.date,
            "distance_km": self.distance_km,
            "energy_consumed_kwh": self.energy_consumed_kwh,
            "energy_charged_kwh": self.energy_charged_kwh,
            "charging_sessions": self.charging_sessions,
            "fast_charge_sessions": self.fast_charge_sessions,
            "deep_discharge_events": self.deep_discharge_events,
            "high_temp_hours": self.high_temp_hours,
            "max_temp_c": self.max_temp_c,
            "equivalent_full_cycles": equivalent_full_cycles or 0,
            "soh_pct": soh_pct,
        }


class WindowedAggregator:
    def __init__(self, postgres_writer: PostgresWriter | None = None):
        self._pg = postgres_writer
        # add_event() runs on the Kafka consume thread, flush_closed_windows() runs on a separate
        # periodic flush thread (see kafka_consumer.py). Both read/mutate the bucket dicts below, so
        # every access to them must hold this lock -- without it, the flush thread's dict iteration
        # races against the consume thread inserting new keys mid-iteration
        # ("RuntimeError: dictionary changed size during iteration").
        self._lock = threading.Lock()
        self.buckets_5min: dict[tuple[str, datetime], FiveMinBucket] = {}
        self.buckets_hourly: dict[tuple[str, datetime], HourlyBucket] = {}
        self.buckets_daily: dict[tuple[str, datetime], DailyBucket] = {}
        self._last_event: dict[str, dict] = {}  # vehicle_id -> {timestamp, odometer_km}
        self._watermark: dict[str, datetime] = {}
        self.late_event_count = 0
        self.max_seen_timestamp: datetime | None = None

    def add_event(self, event: dict, ts: datetime):
        with self._lock:
            self._add_event_locked(event, ts)

    def _add_event_locked(self, event: dict, ts: datetime):
        vehicle_id = event["vehicle_id"]

        if self.max_seen_timestamp is None or ts > self.max_seen_timestamp:
            self.max_seen_timestamp = ts

        watermark = self._watermark.get(vehicle_id)
        is_late = watermark is not None and (watermark - ts).total_seconds() > config.GRACE_PERIOD_SECONDS
        if is_late:
            self.late_event_count += 1
        else:
            self._watermark[vehicle_id] = ts if watermark is None else max(watermark, ts)

        last = self._last_event.get(vehicle_id)
        dt_hours = 0.0
        distance_delta = 0.0
        if last is not None and ts > last["timestamp"]:
            dt_hours = (ts - last["timestamp"]).total_seconds() / 3600.0
            distance_delta = max(0.0, event["odometer_km"] - last["odometer_km"])
        self._last_event[vehicle_id] = {"timestamp": ts, "odometer_km": event["odometer_km"]}

        is_charging = bool(event["is_charging"])
        energy_charged = event["charging_power_kw"] * dt_hours if is_charging else 0.0
        energy_consumed = (
            0.0 if is_charging else abs(event["current_a"]) * event["voltage_v"] / 1000.0 * dt_hours
        )
        above_thermal = event["temperature_c"] > THERMAL_THRESHOLD_C
        thermal_seconds = dt_hours * 3600.0 if above_thermal else 0.0

        key_5min = (vehicle_id, floor_to(ts, 300))
        bucket5 = self.buckets_5min.setdefault(key_5min, FiveMinBucket(vehicle_id, key_5min[1]))
        bucket5.soc.update(event["soc_pct"])
        bucket5.temp.update(event["temperature_c"])
        bucket5.current.update(event["current_a"])
        bucket5.voltage.update(event["voltage_v"])
        if is_charging:
            bucket5.charging_power.update(event["charging_power_kw"])
            bucket5.charging_duration_s += dt_hours * 3600.0
        bucket5.energy_consumed_kwh += energy_consumed
        bucket5.energy_charged_kwh += energy_charged
        bucket5.distance_km += distance_delta
        bucket5.event_count += 1

        key_hour = (vehicle_id, floor_to(ts, 3600))
        bucket_h = self.buckets_hourly.setdefault(key_hour, HourlyBucket(vehicle_id, key_hour[1]))
        bucket_h.soc.update(event["soc_pct"])
        bucket_h.energy_consumed_kwh += energy_consumed
        bucket_h.energy_charged_kwh += energy_charged
        bucket_h.distance_km += distance_delta
        if is_charging:
            bucket_h.charging_duration_s += dt_hours * 3600.0
        bucket_h.high_temp_duration_s += thermal_seconds
        bucket_h.max_temp_c = (
            event["temperature_c"] if bucket_h.max_temp_c is None else max(bucket_h.max_temp_c, event["temperature_c"])
        )

        key_day = (vehicle_id, ts.date())
        bucket_d = self.buckets_daily.setdefault(key_day, DailyBucket(vehicle_id, ts.date()))
        bucket_d.energy_consumed_kwh += energy_consumed
        bucket_d.energy_charged_kwh += energy_charged
        bucket_d.distance_km += distance_delta
        bucket_d.high_temp_hours += thermal_seconds / 3600.0
        bucket_d.max_temp_c = (
            event["temperature_c"] if bucket_d.max_temp_c is None else max(bucket_d.max_temp_c, event["temperature_c"])
        )

    def record_charging_session_closed(self, vehicle_id: str, ts: datetime, is_fast: bool):
        with self._lock:
            key_hour = (vehicle_id, floor_to(ts, 3600))
            if key_hour in self.buckets_hourly:
                self.buckets_hourly[key_hour].charging_sessions += 1
            key_day = (vehicle_id, ts.date())
            bucket_d = self.buckets_daily.get(key_day)
            if bucket_d:
                bucket_d.charging_sessions += 1
                if is_fast:
                    bucket_d.fast_charge_sessions += 1

    def record_deep_discharge(self, vehicle_id: str, ts: datetime):
        with self._lock:
            key_day = (vehicle_id, ts.date())
            bucket_d = self.buckets_daily.get(key_day)
            if bucket_d:
                bucket_d.deep_discharge_events += 1

    def flush_closed_windows(self):
        if self._pg is None or self.max_seen_timestamp is None:
            logger.info(
                "flush skipped: pg=%s max_seen_timestamp=%s",
                self._pg is not None, self.max_seen_timestamp,
            )
            return
        # Pop the closed buckets out under the lock (fast, no I/O), then do the Postgres writes
        # outside the lock so a slow DB write never blocks the consume thread's add_event() calls.
        with self._lock:
            pending_daily = len(self.buckets_daily)
            oldest_daily_date = min((b.date for b in self.buckets_daily.values()), default=None)
        flushed_5min = flushed_hourly = flushed_daily = 0
        while batch := self._pop_closed_5min():
            rows = [bucket.to_row() for _, bucket in batch]
            try:
                self._pg.upsert_agg_5min(rows)
            except Exception:
                logger.exception("failed to batch flush %d 5min buckets", len(batch))
                for key, bucket in batch:
                    try:
                        self._pg.upsert_agg_5min(bucket.to_row())
                    except Exception:
                        logger.exception("failed to flush 5min bucket %s", key)
            flushed_5min += len(batch)

        while batch := self._pop_closed_hourly():
            rows = [bucket.to_row() for _, bucket in batch]
            try:
                self._pg.upsert_agg_hourly(rows)
            except Exception:
                logger.exception("failed to batch flush %d hourly buckets", len(batch))
                for key, bucket in batch:
                    try:
                        self._pg.upsert_agg_hourly(bucket.to_row())
                    except Exception:
                        logger.exception("failed to flush hourly bucket %s", key)
            flushed_hourly += len(batch)

        while batch := self._pop_closed_daily():
            rows = []
            for _, bucket in batch:
                soh_pct, cycles = None, None
                try:
                    soh_pct = self._pg.latest_soh_pct(bucket.vehicle_id, bucket.date)
                except Exception:
                    logger.exception("failed to read ground-truth soh_pct for %s", bucket.vehicle_id)
                rows.append(bucket.to_row(soh_pct, cycles))
            try:
                self._pg.upsert_agg_daily(rows)
            except Exception:
                logger.exception("failed to batch flush %d daily buckets", len(batch))
                for (key, bucket), row in zip(batch, rows):
                    try:
                        self._pg.upsert_agg_daily(row)
                    except Exception:
                        logger.exception("failed to flush daily bucket %s", key)
            flushed_daily += len(batch)

        logger.info(
            "flush cycle: max_seen=%s pending_daily_buckets=%d oldest_daily_date=%s "
            "flushed(5m=%d,h=%d,d=%d)",
            self.max_seen_timestamp, pending_daily, oldest_daily_date,
            flushed_5min, flushed_hourly, flushed_daily,
        )

    def _pop_closed_5min(self, limit: int = 4096) -> list[tuple[tuple[str, datetime], FiveMinBucket]]:
        with self._lock:
            cutoff = self.max_seen_timestamp - timedelta(seconds=config.GRACE_PERIOD_SECONDS)
            closed_keys = []
            for k, b in self.buckets_5min.items():
                if (b.window_start + timedelta(minutes=5)) < cutoff:
                    closed_keys.append(k)
                    if len(closed_keys) >= limit:
                        break
            return [(k, self.buckets_5min.pop(k)) for k in closed_keys]

    def _pop_closed_hourly(self, limit: int = 4096) -> list[tuple[tuple[str, datetime], HourlyBucket]]:
        with self._lock:
            cutoff = self.max_seen_timestamp - timedelta(seconds=config.GRACE_PERIOD_SECONDS)
            closed_keys = []
            for k, b in self.buckets_hourly.items():
                if (b.hour_start + timedelta(hours=1)) < cutoff:
                    closed_keys.append(k)
                    if len(closed_keys) >= limit:
                        break
            return [(k, self.buckets_hourly.pop(k)) for k in closed_keys]

    def _pop_closed_daily(self, limit: int = 4096) -> list[tuple[tuple[str, datetime], DailyBucket]]:
        with self._lock:
            cutoff_date = (self.max_seen_timestamp - timedelta(seconds=config.GRACE_PERIOD_SECONDS)).date()
            closed_keys = []
            for k, b in self.buckets_daily.items():
                if b.date < cutoff_date:
                    closed_keys.append(k)
                    if len(closed_keys) >= limit:
                        break
            return [(k, self.buckets_daily.pop(k)) for k in closed_keys]
