"""Seeds fake fleets/vehicles/batteries/agg_daily rows so the ml service is independently
testable without data-gen/ingestion running (mlMaster.md ss10)."""
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, "..")
from sqlalchemy import text

from src.db import SessionLocal

random.seed(42)

CHEMISTRIES = [("NMC", 1.0, 1.0), ("LFP", 0.7, 0.8)]
ARCHETYPES = ["HEALTHY", "NORMAL", "STRESSED", "BAD"]
ARCHETYPE_MULT = {"HEALTHY": 0.6, "NORMAL": 1.0, "STRESSED": 1.5, "BAD": 2.2}

N_VEHICLES = 20
DURATION_DAYS = 90


def seed(session):
    fleet_id = session.execute(
        text("INSERT INTO fleets (name, region) VALUES ('Seed Fleet', 'US-WEST') RETURNING fleet_id")
    ).fetchone()[0]

    model_id = session.execute(
        text(
            "INSERT INTO vehicle_models (manufacturer, model_name, model_year) "
            "VALUES ('SeedCo', 'SeedModel', 2024) RETURNING model_id"
        )
    ).fetchone()[0]

    chem_ids = {}
    for name, cal, cyc in CHEMISTRIES:
        row = session.execute(
            text(
                "INSERT INTO battery_chemistries (name, calendar_factor, cycle_factor) "
                "VALUES (:name, :cal, :cyc) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
                "RETURNING chemistry_id"
            ),
            {"name": name, "cal": cal, "cyc": cyc},
        ).fetchone()
        chem_ids[name] = row[0]

    simulation_id = session.execute(
        text(
            "INSERT INTO simulations (seed, mode, start_time, duration_days, simulation_speed, status) "
            "VALUES (42, 'LONGITUDINAL', now() - interval '90 days', :duration, 1440, 'COMPLETED') "
            "RETURNING simulation_id"
        ),
        {"duration": DURATION_DAYS},
    ).fetchone()[0]

    for i in range(N_VEHICLES):
        vehicle_id = f"EV-{100000 + i:06d}"
        chemistry_name = random.choice(list(chem_ids.keys()))
        battery_id = session.execute(
            text(
                "INSERT INTO batteries (chemistry_id, nominal_capacity_kwh, rated_voltage_v, installation_date) "
                "VALUES (:chem, :cap, 400, :install_date) RETURNING battery_id"
            ),
            {"chem": chem_ids[chemistry_name], "cap": random.choice([60, 75, 90]),
             "install_date": date.today() - timedelta(days=400)},
        ).fetchone()[0]

        archetype = ARCHETYPES[i % len(ARCHETYPES)]
        session.execute(
            text(
                "INSERT INTO vehicles (vehicle_id, fleet_id, model_id, battery_id, archetype, "
                "manufacture_date, initial_odometer_km, status) "
                "VALUES (:vid, :fleet, :model, :battery, :archetype, :mfg_date, 0, 'ACTIVE')"
            ),
            {"vid": vehicle_id, "fleet": fleet_id, "model": model_id, "battery": battery_id,
             "archetype": archetype, "mfg_date": date.today() - timedelta(days=400)},
        )

        _seed_agg_daily(session, vehicle_id, archetype, chemistry_name)

    session.commit()
    print(f"Seeded simulation_id={simulation_id} with {N_VEHICLES} vehicles over {DURATION_DAYS} days.")


def _seed_agg_daily(session, vehicle_id, archetype, chemistry_name):
    cal_factor, cyc_factor = (1.0, 1.0) if chemistry_name == "NMC" else (0.7, 0.8)
    mult = ARCHETYPE_MULT[archetype]
    cumulative_degradation = 0.0
    start_date = date.today() - timedelta(days=DURATION_DAYS)

    for day_offset in range(DURATION_DAYS):
        day = start_date + timedelta(days=day_offset)
        efc_today = random.uniform(0.2, 1.2) * (mult / 1.5)
        high_temp_hours = random.uniform(0, 3) * mult
        fast_charges = random.choices([0, 1, 2], weights=[6, 3, 1])[0]
        deep_discharges = random.choices([0, 1], weights=[8, 2])[0]
        distance_km = random.uniform(20, 200)
        energy_consumed = distance_km * random.uniform(0.15, 0.22)
        energy_charged = energy_consumed * random.uniform(0.9, 1.1)

        daily_degradation = (
            0.0008 * cal_factor
            + 0.015 * cyc_factor * efc_today
            + mult * (0.004 * high_temp_hours + 0.01 * fast_charges + 0.008 * deep_discharges)
        )
        daily_degradation = min(max(daily_degradation, 0.0), 0.05)
        cumulative_degradation += daily_degradation
        soh_pct = max(100.0 - cumulative_degradation, 60.0)

        session.execute(
            text(
                """
                INSERT INTO agg_daily
                    (vehicle_id, date, distance_km, energy_consumed_kwh, energy_charged_kwh,
                     charging_sessions, fast_charge_sessions, deep_discharge_events,
                     high_temp_hours, max_temp_c, equivalent_full_cycles, soh_pct)
                VALUES
                    (:vid, :date, :distance, :energy_consumed, :energy_charged,
                     :charging_sessions, :fast_charges, :deep_discharges,
                     :high_temp_hours, :max_temp, :efc, :soh)
                ON CONFLICT (vehicle_id, date) DO NOTHING
                """
            ),
            {
                "vid": vehicle_id, "date": day, "distance": distance_km,
                "energy_consumed": energy_consumed, "energy_charged": energy_charged,
                "charging_sessions": 1 if fast_charges or random.random() < 0.5 else 0,
                "fast_charges": fast_charges, "deep_discharges": deep_discharges,
                "high_temp_hours": high_temp_hours, "max_temp": 20 + high_temp_hours * 5,
                "efc": efc_today, "soh": soh_pct,
            },
        )


if __name__ == "__main__":
    session = SessionLocal()
    try:
        seed(session)
    finally:
        session.close()
