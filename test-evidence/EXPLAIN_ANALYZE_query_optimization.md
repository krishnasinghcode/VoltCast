# EXPLAIN ANALYZE Before/After — SQL Optimization Evidence — Full Runbook

Purpose: the problem statement explicitly asks for "EXPLAIN ANALYZE before and after for your slowest
queries: composite and partial indexes, materialised views, keyset pagination, removing ORM N+1 queries."
This runbook finds a genuinely slow query against real data, proves it's slow with a real query plan, fixes
it with an index, and proves the fix with a second real query plan. Every command below is exact — run them
in order.

---

## 0. Prerequisites

- Docker Desktop running.
- This project's code present, `docker compose up -d` already run (or run it now).
- **Real data loaded** — this test is meaningless against a tiny dataset (a sequential scan over 200 rows is
  fast regardless of indexing, so it won't show a real before/after difference). Use whichever substantial
  dataset is already on this machine:
  - If this is the laptop with the real trained dataset (the 200-vehicle/90-day run, or larger): use it as-is,
    no extra step needed.
  - If this is the laptop that just ran the `SHORT_BURST_100k_throughput_test.md` runbook and still has the
    100,000-vehicle fleet loaded (before you've restored the backup): even better for the vehicle-search query
    specifically (100K rows makes a sequential scan vs. index scan difference much more dramatic and easier
    to demonstrate convincingly). Do this step BEFORE restoring that runbook's backup, if both tests are being
    run in the same session.
  - If neither applies, restore any available Postgres dump first (see the backup/restore commands in the
    main `README.md`'s "Day-to-day operations" section).

## 1. Open a psql session

```powershell
docker exec -it krishna-postgres-1 psql -U cvi_app -d cvi
```

Everything below runs inside this `psql` prompt (you'll see a `cvi=>` prompt). `\timing on` makes psql print
wall-clock time for every query too, as a second data point alongside `EXPLAIN ANALYZE`'s own reported time.

```sql
\timing on
```

## 2. Check real table sizes first (so you know what you're testing against)

```sql
SELECT 'vehicles' AS t, count(*) FROM vehicles
UNION ALL SELECT 'agg_daily', count(*) FROM agg_daily
UNION ALL SELECT 'battery_predictions', count(*) FROM battery_predictions
UNION ALL SELECT 'alerts', count(*) FROM alerts;
```

Note these numbers down — they go in the write-up (§5 below) as context for how meaningful the before/after
actually is.

## 3. Run EXPLAIN ANALYZE on each candidate query — find the real slowest one

Try all three below exactly as written (substitute nothing yet). Candidate queries are picked because they
mirror what the dashboard's API actually runs (`vehicle_router.py`'s search endpoint, and the history/risk
list endpoints):

**Candidate A — vehicle search (substring match on vehicle_id, mirrors `GET /vehicles?search=...`):**
```sql
EXPLAIN ANALYZE
SELECT vehicle_id, fleet_id, archetype, status
FROM vehicles
WHERE vehicle_id LIKE '%000123%'
ORDER BY vehicle_id
LIMIT 50;
```

**Candidate B — vehicle history date-range scan (mirrors `GET /vehicles/{id}/history`):**
```sql
EXPLAIN ANALYZE
SELECT date, soh_pct, distance_km, energy_consumed_kwh
FROM agg_daily
WHERE vehicle_id = (SELECT vehicle_id FROM vehicles LIMIT 1)
  AND date >= (CURRENT_DATE - INTERVAL '30 days')
ORDER BY date DESC;
```

**Candidate C — high-risk vehicle list, joined (mirrors `GET /risk/high`):**
```sql
EXPLAIN ANALYZE
SELECT v.vehicle_id, v.archetype, bp.predicted_degradation_30d_pct, bp.predicted_at
FROM battery_predictions bp
JOIN vehicles v ON v.vehicle_id = bp.vehicle_id
WHERE bp.risk_level = 'HIGH'
ORDER BY bp.predicted_at DESC;
```

**Whichever of these three shows `Seq Scan` on the largest table (instead of `Index Scan`) AND the highest
`actual time=...` in its output is your "slowest query" for the write-up.** Copy that query's full
`EXPLAIN ANALYZE` output — this is your "before" evidence.

## 4. Add the index that fixes it

Pick the fix matching whichever candidate turned out slowest:

**If Candidate A was slowest** (vehicle search): a `LIKE '%...%'` pattern (leading wildcard) can't use a plain
B-tree index — it needs a trigram index:
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_vehicles_vehicle_id_trgm ON vehicles USING gin (vehicle_id gin_trgm_ops);
```

**If Candidate B was slowest** (history date range): a composite index matching the exact filter+sort pattern:
```sql
CREATE INDEX idx_agg_daily_vehicle_date_desc ON agg_daily (vehicle_id, date DESC);
```
(Note: `CONTRACT.md`'s schema already defines the primary key as `(vehicle_id, date)` — if this composite
index already effectively exists via the PK, explicitly check with `\d agg_daily` first; if the PK's column
order doesn't match the query's access pattern as well as a dedicated index would, a partial or reordered
index can still help — document what you find, including if the PK already covers it well and a new index
makes little difference, that's a legitimate honest finding too.)

**If Candidate C was slowest** (risk join): a partial index on just the HIGH rows, since that's the only
subset this query ever filters to:
```sql
CREATE INDEX idx_predictions_high_risk ON battery_predictions (predicted_at DESC) WHERE risk_level = 'HIGH';
```

## 5. Re-run the exact same EXPLAIN ANALYZE query from step 3

Run the identical query again (same one that was slow). Copy this output too — this is your "after" evidence.
Confirm: `Seq Scan` should now show as `Index Scan` (or `Bitmap Index Scan`), and `actual time=...` should be
measurably lower.

## 6. Record the result

Fill this in with your real numbers (replace the bracketed placeholders), then save it — this becomes the
actual evidence that goes into the Solution Document's §5.3 and §6.5:

```
Query: [which candidate — A/B/C, and the exact SQL]
Table size at test time: [row count from step 2]

BEFORE (step 3 output):
  Plan: [Seq Scan / Index Scan / etc]
  Execution Time: [X.XXX ms]

AFTER (step 5 output, same query, after the index from step 4):
  Plan: [Seq Scan / Index Scan / etc]
  Execution Time: [Y.YYY ms]

Improvement: [(X-Y)/X as a percentage, or "Nx faster"]
Index added: [exact CREATE INDEX statement from step 4]
```

## 7. Clean up (optional)

If this was run against a temporary 100K-vehicle burst fleet (per the note in §0) and you still need to
restore your real dataset afterward, follow the restore steps in `SHORT_BURST_100k_throughput_test.md` §8 or
the main `README.md`'s backup/restore commands — don't leave the burst fleet's data in place if it isn't the
dataset you want the rest of the project (dashboard, demo video) to show.

---

## Why this matters for the Solution Document

This is the one concrete, hands-on piece of evidence the problem statement explicitly asks for by name
("EXPLAIN ANALYZE before and after"). A real before/after pair — even a modest one — is stronger evidence of
rigor than a larger claimed number with no plan to back it up. If a candidate query turns out to already be
fast (e.g. the existing primary key already covers the access pattern well), that is also a legitimate,
honest finding — say so rather than forcing an artificial slow case.
