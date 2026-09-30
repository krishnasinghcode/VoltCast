"""Flush aggregates, charging sessions, and alerts to Postgres.

Ingestion NEVER writes to `vehicles`, `fleets`, `simulations`, or `simulation_vehicle_state`
(CONTRACT.md §3, ingestionMaster.md §10) — those are data-gen's tables, read-only here.
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from . import config

logger = logging.getLogger("ingestion.postgres_writer")


class PostgresWriter:
    def __init__(self, dsn: str = config.POSTGRES_DSN):
        self._engine: Engine = create_engine(dsn, pool_pre_ping=True)

    def upsert_agg_5min(self, row: dict | list[dict]):
        stmt = text(
            """
            INSERT INTO agg_5min (
                vehicle_id, window_start, window_end,
                avg_soc, min_soc, max_soc,
                avg_temp_c, min_temp_c, max_temp_c, temp_variance,
                avg_current_a, max_current_a, avg_voltage_v,
                charging_duration_s, avg_charging_power_kw, max_charging_power_kw,
                energy_consumed_kwh, energy_charged_kwh, distance_km, event_count
            ) VALUES (
                :vehicle_id, :window_start, :window_end,
                :avg_soc, :min_soc, :max_soc,
                :avg_temp_c, :min_temp_c, :max_temp_c, :temp_variance,
                :avg_current_a, :max_current_a, :avg_voltage_v,
                :charging_duration_s, :avg_charging_power_kw, :max_charging_power_kw,
                :energy_consumed_kwh, :energy_charged_kwh, :distance_km, :event_count
            )
            ON CONFLICT (vehicle_id, window_start) DO UPDATE SET
                window_end = EXCLUDED.window_end,
                avg_soc = EXCLUDED.avg_soc, min_soc = EXCLUDED.min_soc, max_soc = EXCLUDED.max_soc,
                avg_temp_c = EXCLUDED.avg_temp_c, min_temp_c = EXCLUDED.min_temp_c,
                max_temp_c = EXCLUDED.max_temp_c, temp_variance = EXCLUDED.temp_variance,
                avg_current_a = EXCLUDED.avg_current_a, max_current_a = EXCLUDED.max_current_a,
                avg_voltage_v = EXCLUDED.avg_voltage_v,
                charging_duration_s = EXCLUDED.charging_duration_s,
                avg_charging_power_kw = EXCLUDED.avg_charging_power_kw,
                max_charging_power_kw = EXCLUDED.max_charging_power_kw,
                energy_consumed_kwh = EXCLUDED.energy_consumed_kwh,
                energy_charged_kwh = EXCLUDED.energy_charged_kwh,
                distance_km = EXCLUDED.distance_km,
                event_count = EXCLUDED.event_count
            """
        )
        with self._engine.begin() as conn:
            conn.execute(stmt, row if isinstance(row, list) else [row])

    def upsert_agg_hourly(self, row: dict | list[dict]):
        stmt = text(
            """
            INSERT INTO agg_hourly (
                vehicle_id, hour_start, energy_consumed_kwh, energy_charged_kwh, distance_km,
                charging_duration_s, high_temp_duration_s, max_temp_c, avg_soc, soc_range,
                charging_sessions
            ) VALUES (
                :vehicle_id, :hour_start, :energy_consumed_kwh, :energy_charged_kwh, :distance_km,
                :charging_duration_s, :high_temp_duration_s, :max_temp_c, :avg_soc, :soc_range,
                :charging_sessions
            )
            ON CONFLICT (vehicle_id, hour_start) DO UPDATE SET
                energy_consumed_kwh = EXCLUDED.energy_consumed_kwh,
                energy_charged_kwh = EXCLUDED.energy_charged_kwh,
                distance_km = EXCLUDED.distance_km,
                charging_duration_s = EXCLUDED.charging_duration_s,
                high_temp_duration_s = EXCLUDED.high_temp_duration_s,
                max_temp_c = EXCLUDED.max_temp_c,
                avg_soc = EXCLUDED.avg_soc,
                soc_range = EXCLUDED.soc_range,
                charging_sessions = EXCLUDED.charging_sessions
            """
        )
        with self._engine.begin() as conn:
            conn.execute(stmt, row if isinstance(row, list) else [row])

    def upsert_agg_daily(self, row: dict | list[dict]):
        stmt = text(
            """
            INSERT INTO agg_daily (
                vehicle_id, date, distance_km, energy_consumed_kwh, energy_charged_kwh,
                charging_sessions, fast_charge_sessions, deep_discharge_events, high_temp_hours,
                max_temp_c, equivalent_full_cycles, soh_pct
            ) VALUES (
                :vehicle_id, :date, :distance_km, :energy_consumed_kwh, :energy_charged_kwh,
                :charging_sessions, :fast_charge_sessions, :deep_discharge_events, :high_temp_hours,
                :max_temp_c, :equivalent_full_cycles, :soh_pct
            )
            ON CONFLICT (vehicle_id, date) DO UPDATE SET
                distance_km = EXCLUDED.distance_km,
                energy_consumed_kwh = EXCLUDED.energy_consumed_kwh,
                energy_charged_kwh = EXCLUDED.energy_charged_kwh,
                charging_sessions = EXCLUDED.charging_sessions,
                fast_charge_sessions = EXCLUDED.fast_charge_sessions,
                deep_discharge_events = EXCLUDED.deep_discharge_events,
                high_temp_hours = EXCLUDED.high_temp_hours,
                max_temp_c = EXCLUDED.max_temp_c,
                equivalent_full_cycles = EXCLUDED.equivalent_full_cycles,
                soh_pct = COALESCE(EXCLUDED.soh_pct, agg_daily.soh_pct)
            """
        )
        with self._engine.begin() as conn:
            conn.execute(stmt, row if isinstance(row, list) else [row])

    def latest_soh_pct(self, vehicle_id: str, as_of_date) -> float | None:
        """Read-only lookup into data-gen's simulation_vehicle_state table."""
        stmt = text(
            """
            SELECT soh_pct FROM simulation_vehicle_state
            WHERE vehicle_id = :vehicle_id AND sim_timestamp::date <= :as_of_date
            ORDER BY sim_timestamp DESC LIMIT 1
            """
        )
        with self._engine.connect() as conn:
            result = conn.execute(stmt, {"vehicle_id": vehicle_id, "as_of_date": as_of_date}).fetchone()
            return float(result[0]) if result else None

    def insert_charging_session(self, row: dict):
        stmt = text(
            """
            INSERT INTO charging_sessions (
                vehicle_id, started_at, ended_at, start_soc, end_soc,
                energy_delivered_kwh, max_charging_power_kw, charging_type
            ) VALUES (
                :vehicle_id, :started_at, :ended_at, :start_soc, :end_soc,
                :energy_delivered_kwh, :max_charging_power_kw, :charging_type
            )
            """
        )
        with self._engine.begin() as conn:
            conn.execute(stmt, row)

    def insert_alert(self, row: dict):
        stmt = text(
            """
            INSERT INTO alerts (vehicle_id, alert_type, severity, source)
            VALUES (:vehicle_id, :alert_type, :severity, :source)
            """
        )
        with self._engine.begin() as conn:
            conn.execute(stmt, row)

    def dispose(self):
        self._engine.dispose()
