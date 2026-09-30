from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.session_segmenter import SessionSegmenter


def make_event(vehicle_id, is_charging, soc, power=0.0):
    return {
        "vehicle_id": vehicle_id,
        "is_charging": is_charging,
        "soc_pct": soc,
        "charging_power_kw": power,
    }


def test_charging_start_end_creates_one_session():
    pg = MagicMock()
    agg = MagicMock()
    segmenter = SessionSegmenter(pg, agg)
    base = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)

    segmenter.process(make_event("EV-1", True, 40.0, power=50.0), base)
    segmenter.process(make_event("EV-1", True, 55.0, power=60.0), base + timedelta(minutes=10))
    segmenter.process(make_event("EV-1", False, 70.0), base + timedelta(minutes=20))

    assert pg.insert_charging_session.call_count == 1
    row = pg.insert_charging_session.call_args[0][0]
    assert row["vehicle_id"] == "EV-1"
    assert row["start_soc"] == 40.0
    assert row["end_soc"] == 55.0  # end_soc is the last *charging* event's soc, not the stop event's
    assert row["energy_delivered_kwh"] > 0
    assert row["max_charging_power_kw"] == 60.0
    agg.record_charging_session_closed.assert_called_once()
