"""Request counters for GET /api/v1/observability (dashboardMaster.md §8).

Each uvicorn worker keeps a ring buffer of its last N latencies plus counters — no Prometheus needed, the
dashboard's Observability page is the observability deliverable. Because the API runs several worker
processes, every worker publishes its raw state to Redis (`metrics:api:<host>:<pid>`, 15 s TTL) every couple
of seconds, and the observability endpoint merges all live workers. If Redis is unavailable it falls back to
the local worker's numbers.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from collections import deque
from typing import Optional

log = logging.getLogger(__name__)

WORKER_KEY_PREFIX = "metrics:api:"
WORKER_KEY_TTL_S = 15


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values) - 1, int(round(q * (len(sorted_values) - 1))))]


def summarise(state: dict) -> dict:
    lat = sorted(state["latencies"])
    total, errors = state["requests_total"], state["errors_total"]
    return {
        "requests_total": total,
        "errors_total": errors,
        "client_errors_total": state["client_errors_total"],
        "rate_limited_total": state["rate_limited_total"],
        "error_rate": round(errors / total, 4) if total else 0.0,
        "avg_latency_ms": round(sum(lat) / len(lat), 2) if lat else 0.0,
        "p95_latency_ms": round(_percentile(lat, 0.95), 2),
        "p99_latency_ms": round(_percentile(lat, 0.99), 2),
        "latency_sample_size": len(lat),
        "uptime_s": int(time.time() - state["started_at"]),
        "workers": state.get("workers", 1),
    }


class RequestMetrics:
    def __init__(self, window: int = 2000):
        self._lock = threading.Lock()
        self._latencies: deque[float] = deque(maxlen=window)
        self.started_at = time.time()
        self.requests_total = 0
        self.errors_total = 0          # 5xx
        self.client_errors_total = 0   # 4xx
        self.rate_limited_total = 0
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"

    def record(self, status_code: int, latency_ms: float) -> None:
        with self._lock:
            self.requests_total += 1
            if status_code >= 500:
                self.errors_total += 1
            elif status_code >= 400:
                self.client_errors_total += 1
            if status_code == 429:
                self.rate_limited_total += 1
            self._latencies.append(latency_ms)

    def state(self) -> dict:
        with self._lock:
            return {
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "client_errors_total": self.client_errors_total,
                "rate_limited_total": self.rate_limited_total,
                "latencies": [round(v, 2) for v in self._latencies],
                "started_at": self.started_at,
            }

    def snapshot(self) -> dict:
        """This worker only."""
        return summarise(self.state())

    def publish(self, redis_client) -> None:
        redis_client.set(WORKER_KEY_PREFIX + self.worker_id, json.dumps(self.state()), ex=WORKER_KEY_TTL_S)

    def cluster_snapshot(self, redis_client) -> dict:
        """All live workers merged (latency percentiles over the union of their recent samples)."""
        try:
            self.publish(redis_client)
            keys = list(redis_client.scan_iter(match=WORKER_KEY_PREFIX + "*", count=100))
            raw = redis_client.mget(keys) if keys else []
            states = [json.loads(r) for r in raw if r]
        except Exception as exc:
            log.warning("metrics aggregation via redis failed, using local worker only: %s", exc)
            return self.snapshot()
        if not states:
            return self.snapshot()
        merged = {
            "requests_total": sum(s["requests_total"] for s in states),
            "errors_total": sum(s["errors_total"] for s in states),
            "client_errors_total": sum(s["client_errors_total"] for s in states),
            "rate_limited_total": sum(s["rate_limited_total"] for s in states),
            "latencies": [v for s in states for v in s["latencies"]],
            "started_at": min(s["started_at"] for s in states),
            "workers": len(states),
        }
        return summarise(merged)


request_metrics = RequestMetrics()


def publish_safely(redis_client: Optional[object]) -> None:
    try:
        if redis_client is not None:
            request_metrics.publish(redis_client)
    except Exception as exc:
        log.debug("metrics publish failed: %s", exc)


def retract_safely(redis_client: Optional[object]) -> None:
    """On shutdown, drop this worker's key so a restarted container isn't double-counted for 15 s."""
    try:
        if redis_client is not None:
            redis_client.delete(WORKER_KEY_PREFIX + request_metrics.worker_id)
    except Exception as exc:
        log.debug("metrics retract failed: %s", exc)
