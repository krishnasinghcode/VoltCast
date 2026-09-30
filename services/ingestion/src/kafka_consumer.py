"""confluent-kafka Consumer wiring for telemetry.raw, consumer group ingestion-group."""
from __future__ import annotations

import logging
import threading
import time

from confluent_kafka import Consumer, KafkaError, TopicPartition

from . import config
from .alert_rules import AlertRules
from .dedup import Deduplicator
from .metrics import Metrics
from .mongo_writer import MongoWriter
from .postgres_writer import PostgresWriter
from .redis_writer import RedisWriter
from .session_segmenter import SessionSegmenter
from .validation import ValidationError, parse_event, parse_timestamp, validate_event
from .windowed_aggregator import WindowedAggregator

logger = logging.getLogger("ingestion.kafka_consumer")


class IngestionConsumer:
    def __init__(self):
        self.metrics = Metrics()
        self.mongo = MongoWriter()
        self.redis = RedisWriter()
        self.postgres = PostgresWriter()
        self.dedup = Deduplicator()
        self.aggregator = WindowedAggregator(self.postgres)
        self.segmenter = SessionSegmenter(self.postgres, self.aggregator)
        self.alert_rules = AlertRules(self.postgres, self.redis, self.metrics)

        self._consumer = Consumer(
            {
                "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
                "group.id": config.KAFKA_CONSUMER_GROUP,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": True,
            }
        )
        self._stop = threading.Event()
        self._consume_thread: threading.Thread | None = None
        self._flush_thread: threading.Thread | None = None

    def start(self):
        self._consumer.subscribe([config.KAFKA_TOPIC])
        self._consume_thread = threading.Thread(target=self._consume_loop, daemon=True)
        self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._consume_thread.start()
        self._flush_thread.start()

    def stop(self):
        self._stop.set()
        if self._consume_thread:
            self._consume_thread.join(timeout=10)
        if self._flush_thread:
            self._flush_thread.join(timeout=10)
        self.aggregator.flush_closed_windows()
        self._consumer.close()
        self.mongo.close()
        self.redis.close()
        self.postgres.dispose()

    def _consume_loop(self):
        while not self._stop.is_set():
            msg = self._consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logger.error("kafka consumer error: %s", msg.error())
                continue
            self._handle_message(msg.value())

    def _handle_message(self, raw: bytes):
        self.metrics.inc_consumed()
        try:
            event = parse_event(raw)
            validate_event(event)
        except ValidationError as exc:
            self.metrics.inc_rejected()
            level = logging.ERROR if exc.code == "CONTRACT_VERSION_MISMATCH" else logging.WARNING
            logger.log(level, "rejected event (%s): %s", exc.code, exc.reason)
            self.alert_rules.evaluate_quality()
            return

        if self.dedup.is_probably_duplicate(event["event_id"]):
            self.metrics.inc_deduplicated()
            return

        ts = parse_timestamp(event["timestamp"])
        doc = dict(event)
        doc["ingested_at"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        # Mongo write is now batched (see mongo_writer.py) -- no longer a synchronous per-event
        # duplicate gate here. The Bloom filter above is the sole synchronous dedup gate; whatever
        # rare duplicate the Mongo unique index still catches is counted after the batch flushes
        # (see _flush_batches below), not per-event.
        dup_count = self.mongo.enqueue(doc)
        if dup_count:
            self.metrics.inc_deduplicated(by=dup_count)

        self.metrics.record_accepted()
        self.metrics.set_watermark(event["timestamp"])

        # All of this event's Redis writes (state + alert flags) go on one pipeline -> one round
        # trip instead of up to three (see redis_writer.py).
        pipe = self.redis.pipeline()
        self.redis.queue_latest_state(pipe, event)
        self.alert_rules.evaluate(event, ts, pipe)
        pipe.execute()

        self.aggregator.add_event(event, ts)
        self.segmenter.process(event, ts)

    def _flush_loop(self):
        while not self._stop.is_set():
            self._stop.wait(timeout=config.FLUSH_INTERVAL_SECONDS)
            try:
                self.aggregator.flush_closed_windows()
            except Exception:
                logger.exception("flush cycle failed")
            try:
                # Time-based backstop for mongo_writer's batch_size-based auto-flush -- without
                # this, a buffered partial batch (fewer than batch_size docs) sits unflushed
                # indefinitely once production slows down or stops (e.g. at the tail of a run).
                dup_count = self.mongo.flush()
                if dup_count:
                    self.metrics.inc_deduplicated(by=dup_count)
            except Exception:
                logger.exception("periodic mongo flush failed")

    def consumer_lag(self) -> int | None:
        try:
            assignment = self._consumer.assignment()
            if not assignment:
                return None
            total_lag = 0
            for tp in assignment:
                low, high = self._consumer.get_watermark_offsets(tp, timeout=2.0, cached=True)
                committed = self._consumer.committed([TopicPartition(tp.topic, tp.partition)], timeout=2.0)
                position = committed[0].offset if committed and committed[0].offset >= 0 else low
                total_lag += max(0, high - position)
            return total_lag
        except Exception:
            logger.exception("failed to compute consumer lag")
            return None
