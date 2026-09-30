"""Reconstruct missing daily SOH labels from aggregate features and the simulator formula.

This is a recovery path for runs where ingestion flushed agg_daily only after
simulation_vehicle_state had been overwritten with its final snapshot. These labels
are reconstructed estimates, not the simulator's persisted historical ground truth.
"""
from sqlalchemy import text

from src.db import SessionLocal


def backfill(session):
    simulation = session.execute(
        text("SELECT start_time FROM simulations ORDER BY created_at DESC LIMIT 1")
    ).fetchone()
    if simulation is None:
        raise RuntimeError("No simulation is available to anchor the aggregate dates")
    start_time = simulation[0]

    vehicles = session.execute(text(
        """
        SELECT v.vehicle_id, v.archetype, v.manufacture_date,
               b.nominal_capacity_kwh, c.calendar_factor, c.cycle_factor
        FROM vehicles v
        JOIN batteries b ON b.battery_id = v.battery_id
        JOIN battery_chemistries c ON c.chemistry_id = b.chemistry_id
        WHERE EXISTS (SELECT 1 FROM agg_daily ad WHERE ad.vehicle_id = v.vehicle_id)
        ORDER BY v.vehicle_id
        """
    )).fetchall()

    multipliers = {"HEALTHY": 0.6, "NORMAL": 1.0, "STRESSED": 1.5, "BAD": 2.2}
    updated = 0
    for vehicle_id, archetype, manufacture_date, capacity, cal_factor, cycle_factor in vehicles:
        age_days = max(0, (start_time.date() - manufacture_date).days)
        soh = max(90.0, 100.0 - (age_days / 365.0) * 0.8)
        rows = session.execute(text(
            """
            SELECT date, COALESCE(energy_consumed_kwh, 0), COALESCE(energy_charged_kwh, 0),
                   COALESCE(high_temp_hours, 0), COALESCE(fast_charge_sessions, 0),
                   COALESCE(deep_discharge_events, 0)
            FROM agg_daily WHERE vehicle_id = :vehicle_id ORDER BY date
            """
        ), {"vehicle_id": vehicle_id}).fetchall()

        for day, consumed, charged, hot_hours, fast_charges, deep_discharges in rows:
            efc = (float(consumed) + float(charged)) / (2.0 * float(capacity))
            degradation = (
                0.0008 * float(cal_factor)
                + 0.015 * float(cycle_factor) * efc
                + multipliers[archetype] * (
                    0.004 * float(hot_hours)
                    + 0.01 * int(fast_charges)
                    + 0.008 * int(deep_discharges)
                )
            )
            soh = max(60.0, soh - min(0.05, max(0.0, degradation)))
            session.execute(text(
                """
                UPDATE agg_daily
                SET soh_pct = :soh, equivalent_full_cycles = :efc
                WHERE vehicle_id = :vehicle_id AND date = :day
                """
            ), {"soh": soh, "efc": efc, "vehicle_id": vehicle_id, "day": day})
            updated += 1

    session.commit()
    return updated


if __name__ == "__main__":
    with SessionLocal() as session:
        print(f"Reconstructed SOH labels for {backfill(session)} agg_daily rows.")
