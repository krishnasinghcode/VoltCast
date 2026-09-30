#!/usr/bin/env python3
"""Standalone producer mimicking data-gen's output shape (CONTRACT.md §5), for testing ingestion
without data-gen existing yet. Emits one deliberate duplicate event_id, one deliberate
out-of-order sequence_no, and one deliberate malformed event (soc_pct: 999)."""
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from confluent_kafka import Producer

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "telemetry.raw")
CONTRACT_VERSION = os.getenv("CONTRACT_VERSION", "1.0.0")

VEHICLE_IDS = ["EV-000000001", "EV-000000002", "EV-000000003"]
SIMULATION_ID = str(uuid.uuid4())

N_EVENTS = int(os.getenv("N_EVENTS", "60"))


def make_event(vehicle_id: str, seq: int, ts: datetime, soc_pct: float | None = None) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "vehicle_id": vehicle_id,
        "simulation_id": SIMULATION_ID,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "sequence_no": seq,
        "soc_pct": soc_pct if soc_pct is not None else 50 + 20 * ((seq % 20) / 20),
        "voltage_v": 380 + (seq % 10),
        "current_a": -50 if (seq % 4 == 0) else 40,
        "temperature_c": 25 + (seq % 15),
        "is_charging": seq % 4 == 0,
        "charging_power_kw": 40.0 if seq % 4 == 0 else 0.0,
        "speed_kmh": 0 if seq % 4 == 0 else 60,
        "odometer_km": 10000 + seq * 0.5,
        "ambient_temperature_c": 22.0,
        "contract_version": CONTRACT_VERSION,
    }


def main():
    producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    start = datetime.now(timezone.utc)

    duplicate_event = None
    for i in range(N_EVENTS):
        vehicle_id = VEHICLE_IDS[i % len(VEHICLE_IDS)]
        ts = start + timedelta(seconds=i * 10)
        event = make_event(vehicle_id, i, ts)

        if i == 10:
            duplicate_event = event  # remember to re-send unchanged later

        producer.produce(TOPIC, key=vehicle_id.encode(), value=json.dumps(event).encode())
        print(f"produced {event['event_id']} vehicle={vehicle_id} seq={i}")
        time.sleep(0.05)

    # deliberate duplicate (same event_id re-sent, at-least-once delivery simulation)
    if duplicate_event:
        producer.produce(
            TOPIC, key=duplicate_event["vehicle_id"].encode(), value=json.dumps(duplicate_event).encode()
        )
        print(f"produced DUPLICATE {duplicate_event['event_id']}")

    # deliberate out-of-order event (old sequence_no/timestamp arriving late)
    out_of_order = make_event(VEHICLE_IDS[0], 5, start + timedelta(seconds=5))
    producer.produce(TOPIC, key=VEHICLE_IDS[0].encode(), value=json.dumps(out_of_order).encode())
    print(f"produced OUT-OF-ORDER {out_of_order['event_id']}")

    # deliberate malformed event (soc_pct out of range)
    malformed = make_event(VEHICLE_IDS[0], N_EVENTS + 1, start + timedelta(seconds=N_EVENTS * 10), soc_pct=999)
    producer.produce(TOPIC, key=VEHICLE_IDS[0].encode(), value=json.dumps(malformed).encode())
    print(f"produced MALFORMED {malformed['event_id']}")

    producer.flush()
    print("done")


if __name__ == "__main__":
    main()
