# SHORT_BURST 100K-Vehicle Throughput Test — Full Runbook

Purpose: produce the real, measured ingest-throughput number for the problem statement's NFR
("Sustain 100,000+ events/sec") — separate from the k6 test, which measures API read latency, not ingest
throughput. This is real computation (100K vehicles, vectorized physics, near-100K events/sec Kafka
produce/consume) — expect this to actually load a machine; running it on a beefier laptop is reasonable.

Run this on a laptop that either has no existing data worth preserving, or whose existing data you've
already safely extracted (e.g. a trained model + Postgres dump already pulled off it) — see step 1.

---

## 0. Prerequisites

- Docker Desktop installed and running on the target laptop.
- This project's code present (the whole `krishna` folder, via zip transfer or git clone).
- `.env` present (copy from `.env.example` if this is a fresh checkout — placeholder values are fine for
  local-only use).
- Enough free disk: raw Mongo telemetry at 100K vehicles even for a short burst can add up quickly
  (roughly vehicle_count x burst_seconds events, ~1KB each) — a 90-120s burst at 100K vehicles/sec is on the
  order of 10-15M events, a few GB. Fine for a short test; don't run this for many minutes back to back
  without clearing Mongo between runs.

## 1. Decide whether you need to preserve existing data on this laptop first

Building a fresh 100,000-vehicle fleet REQUIRES the `vehicles` table to be empty first — `build_or_load_fleet()`
only knows "have enough already -> reuse" or "table empty -> build fresh from vehicle index 0." Requesting a
bigger fleet than what's already persisted collides on the primary key and crashes (a known, documented
limitation in `docs/dataGenMaster.md`).

If this laptop already has a real trained dataset (e.g. the 1,500-vehicle/90-day LONGITUDINAL run + trained
model used for the actual submission) that you have NOT already backed up elsewhere:

```powershell
docker compose exec postgres pg_dump -U cvi_app -d cvi -Fc -f /tmp/cvi_backup_before_burst_test.dump
docker compose cp postgres:/tmp/cvi_backup_before_burst_test.dump .\backups\cvi_backup_before_burst_test.dump
Get-Item .\backups\cvi_backup_before_burst_test.dump | Select-Object Name, Length
```

(Confirm the file size looks real/non-trivial before proceeding — should be tens of MB at minimum for a real
dataset.) If you already pulled everything valuable off this laptop (trained model + dump already copied
elsewhere), you can skip this and just accept the existing data gets wiped in the next step.

## 2. Bring the stack up

```powershell
cd <project folder>
docker compose up -d kafka postgres mongo redis data-gen ingestion
docker compose ps
```

Confirm all 6 show `Up`. (No need for `ml`/`api`/`frontend` for this test.)

## 3. Truncate the fleet tables (clears the way for a fresh 100K fleet)

```powershell
docker exec -it krishna-postgres-1 psql -U cvi_app -d cvi -c "TRUNCATE simulation_vehicle_state, battery_predictions, alerts, charging_sessions, agg_5min, agg_hourly, agg_daily, simulations, vehicles, batteries, battery_chemistries, vehicle_models, fleets CASCADE;"
```

Should print `TRUNCATE TABLE`.

## 4. Create and start the 100K-vehicle SHORT_BURST simulation

```powershell
curl.exe -X POST http://localhost:8001/api/v1/simulation -H "Content-Type: application/json" -d '{\"seed\":300,\"mode\":\"SHORT_BURST\",\"vehicle_count\":100000,\"active_vehicle_count\":100000,\"duration_days\":1,\"simulation_speed\":1,\"start_time\":\"2026-12-01T00:00:00Z\",\"archetype_distribution\":{\"HEALTHY\":0.35,\"NORMAL\":0.45,\"STRESSED\":0.15,\"BAD\":0.05},\"burst\":{\"enabled\":true,\"duration_seconds\":60,\"target_events_per_sec\":100000},\"fault_injection\":{\"duplicate_rate\":0.01,\"out_of_order_rate\":0.02,\"malformed_rate\":0.005}}'
```

**This step can take a real while before it responds** — building 100,000 vehicles is a genuinely large
batched-insert operation (chunks of 5000 rows at a time). Don't assume it's stuck; just wait.

It returns a `simulation_id` with `status: CREATED`. Copy it, then start it:

```powershell
curl.exe -X POST http://localhost:8001/api/v1/simulation/<paste-id>/start
```

**Important — this does NOT auto-stop after `burst.duration_seconds`.** That field exists in the config
schema but isn't actually wired to a stop condition in this codebase (confirmed by reading
`simulation_runner.py` — the real stop condition is `duration_days`, set to `1` here = 86,400 seconds of
real-time-speed running, i.e. 24 hours if left alone). You MUST manually stop it yourself after the
measurement window (step 6) — don't wait for `status: COMPLETED`.

## 5. Watch it ramp up and measure throughput

In a second terminal, poll ingestion's metrics every few seconds:

```powershell
curl.exe http://localhost:8002/metrics
```

Watch `events_consumed_total` across two samples taken ~10-30 seconds apart once it's been running for at
least 60-90 seconds (to let it past any initial ramp-up). The real throughput number is:

```
events_per_sec = (events_consumed_total at t2 - events_consumed_total at t1) / (t2 - t1)
```

Take 2-3 samples like this to get a stable reading, not just one noisy snapshot. Also note `consumer_lag` —
if it's growing unboundedly, that tells you ingestion can't keep up with production at this scale (a real,
honest finding worth reporting, not something to hide).

Also check data-gen's own production-side number for comparison:

```powershell
curl.exe http://localhost:8001/api/v1/simulation/<the-id>
```

`events_produced` lets you compute the production-side events/sec the same way, so you can report BOTH
"data-gen produced at X events/sec" and "ingestion consumed at Y events/sec" — these may differ, and that gap
IS the interesting finding (where's the bottleneck).

## 6. Stop the simulation manually

Once you have 2-3 good throughput samples (after ~90-120 seconds total), stop it:

```powershell
curl.exe -X POST http://localhost:8001/api/v1/simulation/<the-id>/pause
```

(`/reset` also works and additionally clears its `simulation_vehicle_state` rows, if you'd rather not leave
it paused.)

## 7. Record the result

Write down (or paste back for a report file to be generated):
- Measured events/sec (consumption side, the real NFR number)
- Measured events/sec (production side, for comparison)
- Whether `consumer_lag` was stable or growing during the window
- Vehicle count used (100,000), burst window duration actually measured

## 8. Clean up / restore

If you backed up existing data in step 1 and want it back:

```powershell
docker compose cp .\backups\cvi_backup_before_burst_test.dump postgres:/tmp/cvi_backup_before_burst_test.dump
docker compose exec postgres pg_restore -U cvi_app -d cvi --clean --if-exists /tmp/cvi_backup_before_burst_test.dump
```

Also worth clearing the burst's raw Mongo telemetry if you're not keeping it (it was only needed for the
throughput measurement, not for anything downstream):

```powershell
docker exec -it krishna-mongo-1 mongosh cvi_telemetry --eval "db.raw_telemetry.deleteMany({})"
```

## Why this number matters for the Solution Document

The problem statement's NFR table asks for "sustain 100,000+ events/sec; survive a 3x burst for 5 minutes
without data loss." Whatever real number comes out of this test — even if well under 100K/sec — is more
credible evidence than an unverified claim, especially given this runs on commodity laptop hardware with
Docker Desktop's virtualization overhead, a single Kafka broker, and a single-threaded Python consumer. If the
number is well under 100K/sec, the honest and strong answer for the Solution Document is: report the real
number, and explain the horizontal-scaling path (more Kafka partitions already provisioned — 12 — plus
multiple parallel consumer instances, one per partition subset) that would close the gap in a real deployment,
rather than claiming a number this specific setup cannot actually sustain.
