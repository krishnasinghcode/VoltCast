from src import config
from src.risk_rules import risk_level

# Tests are written relative to the configured thresholds, not hardcoded numbers -- risk_rules.py
# ss6.3 / mlMaster.md ss6.3 explicitly expects these to be recalibrated once real predicted-value
# data exists (they were: 2.5/1.0 placeholders -> 0.45/0.20 after a real 200-vehicle run showed
# nothing could ever cross the old thresholds). Hardcoded test inputs silently stop meaning
# anything the next time the thresholds are tuned -- this is exactly how the previous version of
# this file ended up asserting against stale 2.5/1.0 boundaries after config.py had already moved
# on to 0.45/0.20.

MEDIUM = config.RISK_THRESHOLD_MEDIUM
HIGH = config.RISK_THRESHOLD_HIGH


def test_below_medium_threshold_is_low():
    assert risk_level(MEDIUM - 0.01) == "LOW"


def test_at_medium_boundary_is_medium():
    assert risk_level(MEDIUM) == "MEDIUM"


def test_between_medium_and_high_is_medium():
    midpoint = (MEDIUM + HIGH) / 2
    assert risk_level(midpoint) == "MEDIUM"


def test_at_high_boundary_is_high():
    assert risk_level(HIGH) == "HIGH"


def test_well_above_high_is_high():
    assert risk_level(HIGH * 10) == "HIGH"
