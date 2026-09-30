from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.windowed_aggregator import WindowedAggregator, floor_to


def make_event(vehicle_id, soc, temp, current, voltage, odometer, is_charging=False, power=0.0):
    return {
        "vehicle_id": vehicle_id,
        "soc_pct": soc,
        "temperature_c": temp,
        "current_a": current,
        "voltage_v": voltage,
        "odometer_km": odometer,
        "is_charging": is_charging,
        "charging_power_kw": power,
    }


def test_5min_bucket_stats_match_hand_computed():
    agg = WindowedAggregator(postgres_writer=None)
    base = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)

    events = [
        (make_event("EV-1", 50.0, 20.0, 10.0, 380.0, 1000.0), base),
        (make_event("EV-1", 60.0, 30.0, 20.0, 390.0, 1005.0), base + timedelta(seconds=60)),
        (make_event("EV-1", 40.0, 25.0, 15.0, 385.0, 1010.0), base + timedelta(seconds=120)),
    ]
    for event, ts in events:
        agg.add_event(event, ts)

    key = ("EV-1", floor_to(base, 300))
    bucket = agg.buckets_5min[key]

    assert bucket.event_count == 3
    assert bucket.soc.avg == (50.0 + 60.0 + 40.0) / 3
    assert bucket.soc.min == 40.0
    assert bucket.soc.max == 60.0
    assert bucket.distance_km == (1005.0 - 1000.0) + (1010.0 - 1005.0)


def test_out_of_order_event_folded_without_crash():
    agg = WindowedAggregator(postgres_writer=None)
    base = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)

    agg.add_event(make_event("EV-1", 50.0, 20.0, 10.0, 380.0, 1000.0), base)
    agg.add_event(make_event("EV-1", 55.0, 22.0, 12.0, 382.0, 1002.0), base + timedelta(minutes=2))
    # out-of-order: earlier timestamp (still same 5-min window) arriving after a later one
    agg.add_event(make_event("EV-1", 48.0, 19.0, 9.0, 379.0, 999.0), base + timedelta(seconds=30))

    key = ("EV-1", floor_to(base, 300))
    bucket = agg.buckets_5min[key]
    assert bucket.event_count == 3  # folded into the same bucket, no crash


def test_flush_upserts_and_evicts_closed_bucket():
    pg = MagicMock()
    pg.latest_soh_pct.return_value = 95.0
    agg = WindowedAggregator(postgres_writer=pg)
    base = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)

    agg.add_event(make_event("EV-1", 50.0, 20.0, 10.0, 380.0, 1000.0), base)
    # advance watermark well past the grace period so the first bucket closes
    agg.add_event(make_event("EV-1", 55.0, 22.0, 12.0, 382.0, 1002.0), base + timedelta(hours=2))

    agg.flush_closed_windows()

    assert pg.upsert_agg_5min.called
    key = ("EV-1", floor_to(base, 300))
    assert key not in agg.buckets_5min
