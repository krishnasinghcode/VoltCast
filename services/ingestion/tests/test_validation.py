import pytest

from src.validation import ValidationError, validate_event


def make_valid_event(**overrides):
    event = {
        "event_id": "e1",
        "vehicle_id": "EV-000000001",
        "simulation_id": "s1",
        "timestamp": "2026-09-28T10:00:00Z",
        "sequence_no": 1,
        "soc_pct": 50.0,
        "voltage_v": 380.0,
        "current_a": 10.0,
        "temperature_c": 25.0,
        "is_charging": False,
        "charging_power_kw": 0.0,
        "speed_kmh": 40.0,
        "odometer_km": 1000.0,
        "ambient_temperature_c": 20.0,
        "contract_version": "1.0.0",
    }
    event.update(overrides)
    return event


def test_valid_event_passes():
    validate_event(make_valid_event())  # should not raise


def test_missing_field_rejected():
    event = make_valid_event()
    del event["soc_pct"]
    with pytest.raises(ValidationError):
        validate_event(event)


@pytest.mark.parametrize(
    "field,value",
    [
        ("soc_pct", -1),
        ("soc_pct", 101),
        ("voltage_v", 100),
        ("voltage_v", 500),
        ("current_a", -500),
        ("current_a", 500),
        ("temperature_c", -100),
        ("temperature_c", 200),
        ("charging_power_kw", -1),
        ("charging_power_kw", 400),
        ("speed_kmh", -1),
        ("speed_kmh", 300),
        ("ambient_temperature_c", -100),
        ("ambient_temperature_c", 100),
    ],
)
def test_range_violation_rejected(field, value):
    event = make_valid_event(**{field: value})
    with pytest.raises(ValidationError):
        validate_event(event)


def test_contract_version_mismatch_rejected():
    event = make_valid_event(contract_version="0.9.0")
    with pytest.raises(ValidationError):
        validate_event(event)
