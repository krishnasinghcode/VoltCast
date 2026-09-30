"""Wraps confluent-kafka Producer. Partitions by vehicle_id key. Relies on librdkafka's
internal batching (linger.ms/batch.num.messages) to hit the 100K events/sec SHORT_BURST
target — the actual throughput lever, not application-level threading tricks."""
from __future__ import annotations

import json
import logging
from typing import Optional

from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic

from src.config import KAFKA_BOOTSTRAP_SERVERS, KAFKA_PARTITIONS, KAFKA_TOPIC

logger = logging.getLogger(__name__)


def ensure_topic_exists(bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS,
                         topic: str = KAFKA_TOPIC, partitions: int = KAFKA_PARTITIONS) -> None:
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    existing = admin.list_topics(timeout=10).topics
    if topic in existing:
        return
    new_topic = NewTopic(topic, num_partitions=partitions, replication_factor=1)
    futures = admin.create_topics([new_topic])
    for t, future in futures.items():
        try:
            future.result()
            logger.info("created topic %s", t)
        except Exception as exc:  # topic may already exist due to a race, or auto-create is on
            logger.warning("topic creation for %s: %s", t, exc)


def _delivery_callback(err, msg) -> None:
    if err is not None:
        logger.error("delivery failed for %s: %s", msg.key(), err)


class TelemetryProducer:
    def __init__(self, bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS, topic: str = KAFKA_TOPIC):
        self._topic = topic
        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",
            "linger.ms": 5,
            "batch.num.messages": 10000,
            "queue.buffering.max.messages": 500000,
            "queue.buffering.max.kbytes": 1048576,
        })

    def send(self, vehicle_id: str, event: dict) -> None:
        try:
            self._producer.produce(
                self._topic,
                key=vehicle_id.encode("utf-8"),
                value=json.dumps(event).encode("utf-8"),
                callback=_delivery_callback,
            )
        except BufferError:
            self._producer.poll(0.1)
            self._producer.produce(
                self._topic,
                key=vehicle_id.encode("utf-8"),
                value=json.dumps(event).encode("utf-8"),
                callback=_delivery_callback,
            )

    def poll(self, timeout: float = 0) -> None:
        self._producer.poll(timeout)

    def flush(self, timeout: Optional[float] = None) -> int:
        if timeout is None:
            return self._producer.flush()
        return self._producer.flush(timeout)
