from unittest.mock import MagicMock

from src.dedup import Deduplicator


def test_second_occurrence_flagged_duplicate():
    dedup = Deduplicator(capacity=100, error_rate=0.001)
    assert dedup.is_probably_duplicate("event-1") is False
    assert dedup.is_probably_duplicate("event-1") is True


def test_distinct_ids_not_flagged():
    dedup = Deduplicator(capacity=100, error_rate=0.001)
    assert dedup.is_probably_duplicate("a") is False
    assert dedup.is_probably_duplicate("b") is False


def test_duplicate_dropped_before_any_db_write():
    dedup = Deduplicator(capacity=100, error_rate=0.001)
    mongo_writer = MagicMock()

    def handle(event_id):
        if dedup.is_probably_duplicate(event_id):
            return "skipped"
        mongo_writer.insert_raw({"event_id": event_id})
        return "written"

    assert handle("dup-1") == "written"
    assert handle("dup-1") == "skipped"
    mongo_writer.insert_raw.assert_called_once_with({"event_id": "dup-1"})
