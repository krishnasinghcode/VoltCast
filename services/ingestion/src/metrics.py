"""In-process counters exposed at /metrics."""
from __future__ import annotations

import threading
import time
from collections import deque


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.events_consumed_total = 0
        self.events_deduplicated_total = 0
        self.events_rejected_total = 0
        self.late_events_total = 0
        self.current_watermark_timestamp: str | None = None
        # sliding window of (epoch_seconds, was_rejected) for the last minute, used by the
        # INGESTION_QUALITY alert rule (>5% rejected in the last minute)
        self._recent_outcomes: deque[tuple[float, bool]] = deque()

    def inc_consumed(self):
        with self._lock:
            self.events_consumed_total += 1

    def inc_deduplicated(self, by: int = 1):
        with self._lock:
            self.events_deduplicated_total += by

    def inc_rejected(self):
        with self._lock:
            self.events_rejected_total += 1
            self._record_outcome(rejected=True)

    def inc_late(self):
        with self._lock:
            self.late_events_total += 1

    def record_accepted(self):
        with self._lock:
            self._record_outcome(rejected=False)

    def _record_outcome(self, rejected: bool, window_seconds: float = 60.0):
        now = time.monotonic()
        self._recent_outcomes.append((now, rejected))
        cutoff = now - window_seconds
        while self._recent_outcomes and self._recent_outcomes[0][0] < cutoff:
            self._recent_outcomes.popleft()

    def recent_reject_rate(self) -> float:
        with self._lock:
            if not self._recent_outcomes:
                return 0.0
            rejected = sum(1 for _, r in self._recent_outcomes if r)
            return rejected / len(self._recent_outcomes)

    def set_watermark(self, ts_iso: str):
        with self._lock:
            self.current_watermark_timestamp = ts_iso

    def snapshot(self, consumer_lag: int | None = None) -> dict:
        with self._lock:
            return {
                "events_consumed_total": self.events_consumed_total,
                "events_deduplicated_total": self.events_deduplicated_total,
                "events_rejected_total": self.events_rejected_total,
                "late_events_total": self.late_events_total,
                "consumer_lag": consumer_lag,
                "current_watermark_timestamp": self.current_watermark_timestamp,
            }


metrics = Metrics()
