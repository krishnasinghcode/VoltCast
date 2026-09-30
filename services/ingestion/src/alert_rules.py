"""Real-time threshold alert rules — ingestionMaster.md §7. Distinct from ML-driven predictive alerts."""
from __future__ import annotations

import logging
from datetime import datetime

from . import config
from .metrics import Metrics
from .postgres_writer import PostgresWriter
from .redis_writer import RedisWriter

HIGH_TEMP = "HIGH_TEMP"
DEEP_DISCHARGE = "DEEP_DISCHARGE"
INGESTION_QUALITY = "INGESTION_QUALITY"

logger = logging.getLogger("ingestion.alert_rules")


class AlertRules:
    def __init__(self, postgres_writer: PostgresWriter, redis_writer: RedisWriter, metrics: Metrics):
        self._pg = postgres_writer
        self._redis = redis_writer
        self._metrics = metrics
        self._last_fired: dict[tuple[str, str], datetime] = {}
        self._quality_alert_fired = False

    def _should_fire(self, vehicle_id: str, alert_type: str, ts: datetime) -> bool:
        key = (vehicle_id, alert_type)
        last = self._last_fired.get(key)
        if last is not None and (ts - last).total_seconds() < config.ALERT_DEBOUNCE_SECONDS:
            return False
        self._last_fired[key] = ts
        return True

    def _fire(self, pipe, vehicle_id: str, alert_type: str, severity: str):
        # insert_alert is a real, rare Postgres write (alerts don't fire every event) -- left
        # synchronous. Only the Redis flag set is queued onto the shared per-event pipeline.
        self._pg.insert_alert(
            {"vehicle_id": vehicle_id, "alert_type": alert_type, "severity": severity, "source": "REALTIME_RULE"}
        )
        self._redis.queue_add_alert_flag(pipe, vehicle_id, alert_type)

    def evaluate(self, event: dict, ts: datetime, pipe):
        """`pipe` is the caller's shared Redis pipeline for this event (see kafka_consumer.py) --
        every Redis op here queues onto it instead of making its own round trip."""
        vehicle_id = event["vehicle_id"]

        if event["temperature_c"] > config.HIGH_TEMP_THRESHOLD_C:
            if self._should_fire(vehicle_id, HIGH_TEMP, ts):
                self._fire(pipe, vehicle_id, HIGH_TEMP, "CRITICAL")
        else:
            self._redis.queue_remove_alert_flag(pipe, vehicle_id, HIGH_TEMP)

        if event["soc_pct"] < config.DEEP_DISCHARGE_THRESHOLD_PCT:
            if self._should_fire(vehicle_id, DEEP_DISCHARGE, ts):
                self._fire(pipe, vehicle_id, DEEP_DISCHARGE, "WARNING")
        else:
            self._redis.queue_remove_alert_flag(pipe, vehicle_id, DEEP_DISCHARGE)

    def evaluate_quality(self):
        """Metrics-based rule, not per-event: >5% of events in last minute failed validation."""
        rate = self._metrics.recent_reject_rate()
        if rate > config.INGESTION_QUALITY_REJECT_RATE:
            if not self._quality_alert_fired:
                # `alerts.vehicle_id` is NOT NULL (CONTRACT.md §3) and this rule is fleet-wide, not
                # per-vehicle, so it cannot be persisted as a normal alert row — logged instead,
                # documented as a deviation in README.
                logger.warning("INGESTION_QUALITY alert: reject rate %.1f%% over last minute", rate * 100)
                self._quality_alert_fired = True
        else:
            self._quality_alert_fired = False
