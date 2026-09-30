"""raw_telemetry inserts into MongoDB (CONTRACT.md §4).

Batched, not per-event: `insert_one` per message was the dominant consumer-throughput bottleneck
during integration testing (a full network round trip per event, single-threaded consume loop,
capped throughput around ~400 events/sec against data-gen's ~1200 events/sec production rate,
which would make a real 60-90 day / multi-million-event run take hours instead of minutes).
`enqueue()` buffers; `flush()` does one `insert_many(ordered=False)` round trip per batch.

Duplicate detection: the Bloom filter in dedup.py is the primary, synchronous gate (checked before
`enqueue()` is ever called) — this Mongo unique index is only a rare backstop for whatever the Bloom
filter's false-negative rate lets through. Because writes are now batched, we can no longer tell the
caller synchronously "this exact event was a duplicate" before deciding whether to feed it to the
aggregator/segmenter — that per-event gate is accepted as removed (documented deviation from the
original design) given the Bloom filter already catches effectively all real duplicates at this scale.
Duplicates caught by this backstop are still counted (via BulkWriteError) and folded into the
`events_deduplicated_total` metric after each flush, just not synchronously per-event.
"""
from __future__ import annotations

import logging

from pymongo import ASCENDING, MongoClient
from pymongo.errors import BulkWriteError

from . import config

logger = logging.getLogger("ingestion.mongo_writer")


class MongoWriter:
    def __init__(self, uri: str = config.MONGO_URI, db_name: str = config.MONGO_DB, batch_size: int = 200):
        self._client = MongoClient(uri)
        self._db = self._client[db_name]
        self._collection = self._db["raw_telemetry"]
        self._ensure_indexes()
        self._batch_size = batch_size
        self._buffer: list[dict] = []

    def _ensure_indexes(self):
        self._collection.create_index(
            [("vehicle_id", ASCENDING), ("timestamp", ASCENDING)], name="vehicle_id_timestamp"
        )
        self._collection.create_index("event_id", unique=True, name="event_id_unique")

    def enqueue(self, doc: dict) -> int:
        """Buffers doc for the next flush. Returns duplicate-key count if this call triggered an
        auto-flush (buffer hit batch_size), else 0 -- caller folds this into metrics."""
        self._buffer.append(doc)
        if len(self._buffer) >= self._batch_size:
            return self.flush()
        return 0

    def flush(self) -> int:
        """Writes the buffered batch in one round trip. Returns count of duplicate-key rejections."""
        if not self._buffer:
            return 0
        batch, self._buffer = self._buffer, []
        try:
            self._collection.insert_many(batch, ordered=False)
            return 0
        except BulkWriteError as exc:
            write_errors = exc.details.get("writeErrors", [])
            dup_count = sum(1 for err in write_errors if err.get("code") == 11000)
            non_dup_errors = [err for err in write_errors if err.get("code") != 11000]
            if non_dup_errors:
                logger.error("mongo bulk insert had %d non-duplicate errors: %s", len(non_dup_errors), non_dup_errors[:3])
            return dup_count

    def close(self):
        self.flush()
        self._client.close()
