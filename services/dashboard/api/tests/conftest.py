"""Fixture DB for router tests (dashboardMaster.md §13): an in-memory SQLite copy of the CONTRACT.md §3 tables
the API reads, filled with a handful of fake rows, plus fakeredis. No Postgres/Redis/peer services needed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import fakeredis
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("WS_PUSH_INTERVAL_SECONDS", "0.2")
# Point peers at a closed port so any accidental network call fails fast.
for _name in ("DATA_GEN_URL", "INGESTION_URL", "ML_URL"):
    os.environ.setdefault(_name, "http://127.0.0.1:9")

from fastapi.testclient import TestClient  # noqa: E402

from src import db  # noqa: E402
from src.auth import hash_password  # noqa: E402
from src.main import app  # noqa: E402

FLEET_A = "11111111-1111-1111-1111-111111111111"
FLEET_B = "22222222-2222-2222-2222-222222222222"
SIM_ID = "99999999-9999-9999-9999-999999999999"
PASSWORDS = {"admin@cvi.local": "admin123", "manager@cvi.local": "manager123", "viewer@cvi.local": "viewer123"}

SCHEMA = """
CREATE TABLE fleets (fleet_id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL,
                     created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE vehicle_models (model_id TEXT PRIMARY KEY, manufacturer TEXT, model_name TEXT, model_year INT);
CREATE TABLE battery_chemistries (chemistry_id TEXT PRIMARY KEY, name TEXT UNIQUE, calendar_factor NUMERIC,
                                  cycle_factor NUMERIC);
CREATE TABLE batteries (battery_id TEXT PRIMARY KEY, chemistry_id TEXT, nominal_capacity_kwh NUMERIC,
                        rated_voltage_v NUMERIC, installation_date TEXT);
CREATE TABLE vehicles (vehicle_id TEXT PRIMARY KEY, fleet_id TEXT NOT NULL, model_id TEXT NOT NULL,
                       battery_id TEXT NOT NULL, archetype TEXT NOT NULL, manufacture_date TEXT NOT NULL,
                       initial_odometer_km NUMERIC DEFAULT 0, status TEXT DEFAULT 'ACTIVE',
                       created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE simulation_vehicle_state (simulation_id TEXT, vehicle_id TEXT, sim_timestamp TEXT, soc_pct NUMERIC,
    usable_capacity_kwh NUMERIC, soh_pct NUMERIC, odometer_km NUMERIC, equivalent_full_cycles NUMERIC,
    battery_temp_c NUMERIC, thermal_exposure_hours NUMERIC, fast_charge_count INT, deep_discharge_count INT,
    PRIMARY KEY (simulation_id, vehicle_id));
CREATE TABLE agg_5min (vehicle_id TEXT, window_start TEXT, window_end TEXT, avg_soc NUMERIC, min_soc NUMERIC,
    max_soc NUMERIC, avg_temp_c NUMERIC, min_temp_c NUMERIC, max_temp_c NUMERIC, temp_variance NUMERIC,
    avg_current_a NUMERIC, max_current_a NUMERIC, avg_voltage_v NUMERIC, charging_duration_s INT,
    avg_charging_power_kw NUMERIC, max_charging_power_kw NUMERIC, energy_consumed_kwh NUMERIC,
    energy_charged_kwh NUMERIC, distance_km NUMERIC, event_count INT, PRIMARY KEY (vehicle_id, window_start));
CREATE TABLE agg_hourly (vehicle_id TEXT, hour_start TEXT, energy_consumed_kwh NUMERIC, energy_charged_kwh NUMERIC,
    distance_km NUMERIC, charging_duration_s INT, high_temp_duration_s INT, max_temp_c NUMERIC, avg_soc NUMERIC,
    soc_range NUMERIC, charging_sessions INT, PRIMARY KEY (vehicle_id, hour_start));
CREATE TABLE agg_daily (vehicle_id TEXT, date TEXT, distance_km NUMERIC, energy_consumed_kwh NUMERIC,
    energy_charged_kwh NUMERIC, charging_sessions INT, fast_charge_sessions INT, deep_discharge_events INT,
    high_temp_hours NUMERIC, max_temp_c NUMERIC, equivalent_full_cycles NUMERIC, soh_pct NUMERIC,
    PRIMARY KEY (vehicle_id, date));
CREATE TABLE charging_sessions (session_id TEXT PRIMARY KEY, vehicle_id TEXT, started_at TEXT, ended_at TEXT,
    start_soc NUMERIC, end_soc NUMERIC, energy_delivered_kwh NUMERIC, max_charging_power_kw NUMERIC,
    charging_type TEXT);
CREATE TABLE battery_predictions (prediction_id TEXT PRIMARY KEY, vehicle_id TEXT, predicted_at TEXT,
    current_soh NUMERIC, predicted_degradation_30d_pct NUMERIC, predicted_soh_30d NUMERIC, risk_level TEXT,
    horizon_days INT DEFAULT 30, model_version TEXT);
CREATE TABLE alerts (alert_id TEXT PRIMARY KEY, vehicle_id TEXT, created_at TEXT, alert_type TEXT,
    severity TEXT, status TEXT DEFAULT 'OPEN', source TEXT, prediction_id TEXT);
CREATE TABLE users (user_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    role TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
"""

# 12 vehicles: EV-000000001..EV-000000012. Odd -> fleet A, even -> fleet B.
ARCHETYPES = ["HEALTHY", "NORMAL", "STRESSED", "BAD"]
RISK = {1: "LOW", 2: "LOW", 3: "HIGH", 4: "HIGH", 5: "MEDIUM", 6: "HIGH"}  # 7..12 unpredicted


def vid(i: int) -> str:
    return f"EV-{i:09d}"


def _seed(conn) -> None:
    for stmt in SCHEMA.strip().split(";"):
        if stmt.strip():
            conn.execute(text(stmt))
    conn.execute(text("INSERT INTO fleets VALUES (:id, 'Alpha Logistics', 'EU-West', '2026-01-01T00:00:00')"),
                 {"id": FLEET_A})
    conn.execute(text("INSERT INTO fleets VALUES (:id, 'Beta Rideshare', 'US-East', '2026-01-02T00:00:00')"),
                 {"id": FLEET_B})
    conn.execute(text("INSERT INTO vehicle_models VALUES ('m1', 'Voltara', 'V1', 2025)"))
    conn.execute(text("INSERT INTO battery_chemistries VALUES ('c1', 'NMC', 1.0, 1.0)"))
    for i in range(1, 13):
        conn.execute(text("INSERT INTO batteries VALUES (:b, 'c1', 75, 400, '2025-01-01')"), {"b": f"b{i}"})
        conn.execute(text("""INSERT INTO vehicles (vehicle_id, fleet_id, model_id, battery_id, archetype,
                             manufacture_date) VALUES (:v, :f, 'm1', :b, :a, '2025-01-01')"""),
                     {"v": vid(i), "f": FLEET_A if i % 2 else FLEET_B, "b": f"b{i}", "a": ARCHETYPES[i % 4]})
        conn.execute(text("""INSERT INTO simulation_vehicle_state VALUES (:s, :v, '2026-03-31T00:00:00', 55.0, 70.0,
                             :soh, 12000, 40, 30.5, 12, 3, 1)"""),
                     {"s": SIM_ID, "v": vid(i), "soh": 100 - i})
        for d in range(1, 6):
            conn.execute(text("""INSERT INTO agg_daily (vehicle_id, date, distance_km, energy_consumed_kwh,
                                 energy_charged_kwh, charging_sessions, fast_charge_sessions, deep_discharge_events,
                                 high_temp_hours, max_temp_c, equivalent_full_cycles, soh_pct)
                                 VALUES (:v, :d, 100, 18, 20, 1, 0, 0, 0.5, 36, 0.3, :soh)"""),
                         {"v": vid(i), "d": f"2026-03-{d:02d}", "soh": 100 - i - d * 0.01})
    for i, risk in RISK.items():
        # an older prediction with a different risk proves "latest wins"
        conn.execute(text("""INSERT INTO battery_predictions VALUES (:p, :v, '2026-03-01T00:00:00', 99, 0.1, 98.9,
                             'LOW', 30, 'v0')"""), {"p": f"old-{i}", "v": vid(i)})
        conn.execute(text("""INSERT INTO battery_predictions VALUES (:p, :v, '2026-03-30T00:00:00', :soh, :deg,
                             :psoh, :r, 30, 'v1')"""),
                     {"p": f"new-{i}", "v": vid(i), "soh": 100 - i, "deg": i * 0.5, "psoh": 100 - i - i * 0.5,
                      "r": risk})
    alerts = [
        ("aaaaaaaa-0000-0000-0000-000000000001", vid(1), "2026-03-30T10:00:00", "HIGH_TEMP", "WARNING", "OPEN"),
        ("aaaaaaaa-0000-0000-0000-000000000002", vid(1), "2026-03-30T11:00:00", "DEEP_DISCHARGE", "INFO", "OPEN"),
        ("aaaaaaaa-0000-0000-0000-000000000003", vid(3), "2026-03-30T12:00:00", "HIGH_TEMP", "CRITICAL", "OPEN"),
        ("aaaaaaaa-0000-0000-0000-000000000004", vid(4), "2026-03-30T12:00:00", "HIGH_TEMP", "WARNING", "RESOLVED"),
    ]
    for a in alerts:
        conn.execute(text("INSERT INTO alerts VALUES (:id, :v, :t, :ty, :sev, :st, 'REALTIME_RULE', NULL)"),
                     dict(zip(("id", "v", "t", "ty", "sev", "st"), a)))
    roles = {"admin@cvi.local": "ADMIN", "manager@cvi.local": "FLEET_MANAGER", "viewer@cvi.local": "VIEWER"}
    for n, (email, pw) in enumerate(PASSWORDS.items()):
        conn.execute(text("INSERT INTO users (user_id, email, password_hash, role) VALUES (:id, :e, :h, :r)"),
                     {"id": f"00000000-0000-0000-0000-00000000000{n}", "e": email, "h": hash_password(pw),
                      "r": roles[email]})


_ENGINE = None


def _engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        with _ENGINE.begin() as conn:
            _seed(conn)
    return _ENGINE


@pytest.fixture()
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture()
def client(redis_client):
    engine = _engine()
    # reset mutable rows each test
    with engine.begin() as conn:
        conn.execute(text("UPDATE alerts SET status = 'OPEN' WHERE alert_id != 'aaaaaaaa-0000-0000-0000-000000000004'"))
    db.set_engine(engine)
    db.set_redis(redis_client)
    redis_client.hset(f"vehicle:state:{vid(1)}", mapping={
        "soc_pct": 72.4, "temperature_c": 34.7, "is_charging": 1, "odometer_km": 48231.6,
        "last_updated": "2026-03-31T10:21:31.120Z"})
    redis_client.sadd(f"vehicle:alert_flags:{vid(1)}", "HIGH_TEMP")
    with TestClient(app) as c:
        yield c


def login(client, email: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORDS[email]})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.fixture()
def viewer_headers(client):
    return {"Authorization": f"Bearer {login(client, 'viewer@cvi.local')}"}


@pytest.fixture()
def manager_headers(client):
    return {"Authorization": f"Bearer {login(client, 'manager@cvi.local')}"}


@pytest.fixture()
def admin_headers(client):
    return {"Authorization": f"Bearer {login(client, 'admin@cvi.local')}"}
