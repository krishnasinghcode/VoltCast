"""Latest-state hash + alert flag set updates in Redis (CONTRACT.md §6).

Per-event calls are pipelined (state hset + alert flag set/clear queued on one pipeline, executed as
a single round trip) -- three separate synchronous round trips per event was one of the two dominant
consumer-throughput bottlenecks found during integration testing (see mongo_writer.py for the other).
"""
from __future__ import annotations

import redis

from . import config


class RedisWriter:
    def __init__(self, host: str = config.REDIS_HOST, port: int = config.REDIS_PORT):
        self._client = redis.Redis(host=host, port=port, decode_responses=True)

    def pipeline(self):
        """Returns a new pipeline for the caller to queue this event's Redis ops onto, then execute
        once. transaction=False: these are independent per-key writes, no need for MULTI/EXEC."""
        return self._client.pipeline(transaction=False)

    def queue_latest_state(self, pipe, event: dict):
        key = f"vehicle:state:{event['vehicle_id']}"
        pipe.hset(
            key,
            mapping={
                "soc_pct": event["soc_pct"],
                "temperature_c": event["temperature_c"],
                "is_charging": int(bool(event["is_charging"])),
                "odometer_km": event["odometer_km"],
                "last_updated": event["timestamp"],
            },
        )

    def queue_add_alert_flag(self, pipe, vehicle_id: str, alert_type: str):
        pipe.sadd(f"vehicle:alert_flags:{vehicle_id}", alert_type)

    def queue_remove_alert_flag(self, pipe, vehicle_id: str, alert_type: str):
        pipe.srem(f"vehicle:alert_flags:{vehicle_id}", alert_type)

    # Non-pipelined variants kept for callers outside the per-event hot path (none currently, but
    # simpler than forcing every caller to manage a pipeline for a one-off write).
    def update_latest_state(self, event: dict):
        pipe = self.pipeline()
        self.queue_latest_state(pipe, event)
        pipe.execute()

    def add_alert_flag(self, vehicle_id: str, alert_type: str):
        self._client.sadd(f"vehicle:alert_flags:{vehicle_id}", alert_type)

    def remove_alert_flag(self, vehicle_id: str, alert_type: str):
        self._client.srem(f"vehicle:alert_flags:{vehicle_id}", alert_type)

    def close(self):
        self._client.close()
