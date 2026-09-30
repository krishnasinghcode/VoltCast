"""Two-layer event dedup: in-process Bloom filter (fast path) + Mongo unique index (durable backstop).

Bloom filter never false-negatives (never lets a real duplicate through) when sized correctly,
but can false-positive (skip a genuinely new event). That trade is accepted here and documented
in README — the Mongo unique-index duplicate-key error is the durable backstop layer that catches
anything the filter missed after a process restart wipes its in-memory state.
"""
from __future__ import annotations

import logging

from pybloom_live import ScalableBloomFilter

from . import config

logger = logging.getLogger("ingestion.dedup")


class Deduplicator:
    def __init__(self, capacity: int = config.BLOOM_CAPACITY, error_rate: float = config.BLOOM_ERROR_RATE):
        self._filter = ScalableBloomFilter(initial_capacity=capacity, error_rate=error_rate)
        self.bloom_hits = 0
        self.checked_total = 0

    def is_probably_duplicate(self, event_id: str) -> bool:
        """True => probably already seen (skip). False => definitely new."""
        self.checked_total += 1
        if event_id in self._filter:
            self.bloom_hits += 1
            return True
        self._filter.add(event_id)
        return False
