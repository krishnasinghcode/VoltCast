"""Postgres access layer. Writes fleet config (vehicles/batteries/etc) and
simulation_vehicle_state. This service owns these tables — ingestion never writes to them.

Schema is CONTRACT.md §3 (idempotent CREATE TABLE IF NOT EXISTS here so this service is fully
standalone-runnable without any other module's files present, per dataGenMaster.md §12)."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterable, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.config import DATABASE_URL

_engine: Optional[Engine] = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
    return _engine


DDL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS fleets (
    fleet_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    region          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vehicle_models (
    model_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manufacturer    TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    model_year      INT NOT NULL
);

CREATE TABLE IF NOT EXISTS battery_chemistries (
    chemistry_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL UNIQUE,
    calendar_factor NUMERIC NOT NULL,
    cycle_factor    NUMERIC NOT NULL
);

CREATE TABLE IF NOT EXISTS batteries (
    battery_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chemistry_id        UUID NOT NULL REFERENCES battery_chemistries(chemistry_id),
    nominal_capacity_kwh NUMERIC NOT NULL,
    rated_voltage_v      NUMERIC NOT NULL,
    installation_date    DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id          TEXT PRIMARY KEY,
    fleet_id            UUID NOT NULL REFERENCES fleets(fleet_id),
    model_id            UUID NOT NULL REFERENCES vehicle_models(model_id),
    battery_id          UUID NOT NULL REFERENCES batteries(battery_id),
    archetype           TEXT NOT NULL CHECK (archetype IN ('HEALTHY','NORMAL','STRESSED','BAD')),
    manufacture_date    DATE NOT NULL,
    initial_odometer_km NUMERIC NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_vehicles_fleet ON vehicles(fleet_id);
CREATE INDEX IF NOT EXISTS idx_vehicles_archetype ON vehicles(archetype);

CREATE TABLE IF NOT EXISTS simulations (
    simulation_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seed                BIGINT NOT NULL,
    mode                TEXT NOT NULL CHECK (mode IN ('SHORT_BURST','LONGITUDINAL','DEMO')),
    start_time          TIMESTAMPTZ NOT NULL,
    duration_days       INT NOT NULL,
    simulation_speed    NUMERIC NOT NULL,
    status              TEXT NOT NULL DEFAULT 'CREATED' CHECK (status IN ('CREATED','RUNNING','PAUSED','COMPLETED','FAILED','RESET')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS simulation_vehicle_state (
    simulation_id       UUID NOT NULL REFERENCES simulations(simulation_id),
    vehicle_id          TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    sim_timestamp        TIMESTAMPTZ NOT NULL,
    soc_pct              NUMERIC NOT NULL,
    usable_capacity_kwh  NUMERIC NOT NULL,
    soh_pct              NUMERIC NOT NULL,
    odometer_km           NUMERIC NOT NULL,
    equivalent_full_cycles NUMERIC NOT NULL DEFAULT 0,
    battery_temp_c        NUMERIC NOT NULL,
    thermal_exposure_hours NUMERIC NOT NULL DEFAULT 0,
    fast_charge_count      INT NOT NULL DEFAULT 0,
    deep_discharge_count   INT NOT NULL DEFAULT 0,
    PRIMARY KEY (simulation_id, vehicle_id)
);
"""


def init_db() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        for statement in DDL.split(";\n\n"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement + ";"))


@contextmanager
def session():
    engine = get_engine()
    conn = engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fleet_population_exists(vehicle_count: int) -> bool:
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM vehicles")).scalar()
        return (result or 0) >= vehicle_count


def insert_fleets(conn, fleets: list[dict]) -> None:
    conn.execute(
        text("INSERT INTO fleets (fleet_id, name, region) VALUES (:fleet_id, :name, :region)"),
        fleets,
    )


def insert_vehicle_models(conn, models: list[dict]) -> None:
    conn.execute(
        text(
            "INSERT INTO vehicle_models (model_id, manufacturer, model_name, model_year) "
            "VALUES (:model_id, :manufacturer, :model_name, :model_year)"
        ),
        models,
    )


def insert_battery_chemistries(conn, chemistries: list[dict]) -> None:
    conn.execute(
        text(
            "INSERT INTO battery_chemistries (chemistry_id, name, calendar_factor, cycle_factor) "
            "VALUES (:chemistry_id, :name, :calendar_factor, :cycle_factor) ON CONFLICT (name) DO NOTHING"
        ),
        chemistries,
    )


def insert_batteries(conn, batteries: list[dict]) -> None:
    conn.execute(
        text(
            "INSERT INTO batteries (battery_id, chemistry_id, nominal_capacity_kwh, rated_voltage_v, installation_date) "
            "VALUES (:battery_id, :chemistry_id, :nominal_capacity_kwh, :rated_voltage_v, :installation_date)"
        ),
        batteries,
    )


def insert_vehicles(conn, vehicles: list[dict]) -> None:
    conn.execute(
        text(
            "INSERT INTO vehicles (vehicle_id, fleet_id, model_id, battery_id, archetype, manufacture_date, initial_odometer_km) "
            "VALUES (:vehicle_id, :fleet_id, :model_id, :battery_id, :archetype, :manufacture_date, :initial_odometer_km)"
        ),
        vehicles,
    )


def upsert_simulation_vehicle_state(conn, rows: list[dict]) -> None:
    if not rows:
        return
    conn.execute(
        text(
            """
            INSERT INTO simulation_vehicle_state (
                simulation_id, vehicle_id, sim_timestamp, soc_pct, usable_capacity_kwh, soh_pct,
                odometer_km, equivalent_full_cycles, battery_temp_c, thermal_exposure_hours,
                fast_charge_count, deep_discharge_count
            ) VALUES (
                :simulation_id, :vehicle_id, :sim_timestamp, :soc_pct, :usable_capacity_kwh, :soh_pct,
                :odometer_km, :equivalent_full_cycles, :battery_temp_c, :thermal_exposure_hours,
                :fast_charge_count, :deep_discharge_count
            )
            ON CONFLICT (simulation_id, vehicle_id) DO UPDATE SET
                sim_timestamp = EXCLUDED.sim_timestamp,
                soc_pct = EXCLUDED.soc_pct,
                usable_capacity_kwh = EXCLUDED.usable_capacity_kwh,
                soh_pct = EXCLUDED.soh_pct,
                odometer_km = EXCLUDED.odometer_km,
                equivalent_full_cycles = EXCLUDED.equivalent_full_cycles,
                battery_temp_c = EXCLUDED.battery_temp_c,
                thermal_exposure_hours = EXCLUDED.thermal_exposure_hours,
                fast_charge_count = EXCLUDED.fast_charge_count,
                deep_discharge_count = EXCLUDED.deep_discharge_count
            """
        ),
        rows,
    )


def insert_simulation(conn, simulation_id: str, seed: int, mode: str, start_time: datetime,
                       duration_days: int, simulation_speed: float) -> None:
    conn.execute(
        text(
            "INSERT INTO simulations (simulation_id, seed, mode, start_time, duration_days, simulation_speed) "
            "VALUES (:simulation_id, :seed, :mode, :start_time, :duration_days, :simulation_speed)"
        ),
        {
            "simulation_id": simulation_id,
            "seed": seed,
            "mode": mode,
            "start_time": start_time,
            "duration_days": duration_days,
            "simulation_speed": simulation_speed,
        },
    )


def update_simulation_status(conn, simulation_id: str, status: str) -> None:
    conn.execute(
        text("UPDATE simulations SET status = :status WHERE simulation_id = :simulation_id"),
        {"status": status, "simulation_id": simulation_id},
    )


def get_simulation(conn, simulation_id: str):
    return conn.execute(
        text("SELECT * FROM simulations WHERE simulation_id = :simulation_id"),
        {"simulation_id": simulation_id},
    ).mappings().first()


def list_simulations(conn, limit: int, after: Optional[str]):
    if after:
        rows = conn.execute(
            text(
                "SELECT * FROM simulations WHERE created_at < (SELECT created_at FROM simulations WHERE simulation_id = :after) "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"after": after, "limit": limit},
        ).mappings().all()
    else:
        rows = conn.execute(
            text("SELECT * FROM simulations ORDER BY created_at DESC LIMIT :limit"),
            {"limit": limit},
        ).mappings().all()
    return rows


def clear_simulation_vehicle_state(conn, simulation_id: str) -> None:
    conn.execute(
        text("DELETE FROM simulation_vehicle_state WHERE simulation_id = :simulation_id"),
        {"simulation_id": simulation_id},
    )


def get_all_vehicles(conn) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT v.vehicle_id, v.fleet_id, v.model_id, v.battery_id, v.archetype,
                   v.manufacture_date, v.initial_odometer_km,
                   b.chemistry_id, b.nominal_capacity_kwh, b.rated_voltage_v,
                   c.name AS chemistry_name, c.calendar_factor, c.cycle_factor
            FROM vehicles v
            JOIN batteries b ON b.battery_id = v.battery_id
            JOIN battery_chemistries c ON c.chemistry_id = b.chemistry_id
            ORDER BY v.vehicle_id
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]
