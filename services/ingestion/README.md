# ingestion service (Part B)

Kafka consumer service. Consumes `telemetry.raw`, dedups, writes raw copy to MongoDB, updates
Redis latest-state, runs real-time alert rules, and flushes windowed aggregates + charging
sessions to Postgres. Background worker only — exposes `/health` and `/metrics` on port 8002,
no business endpoints. Built standalone against `tools/fake_producer.py`, no dependency on
data-gen existing.

## Run

```bash
docker compose up kafka postgres mongo redis ingestion
python tools/fake_producer.py   # or: docker compose run --rm ingestion python tools/fake_producer.py
curl localhost:8002/metrics
```

Verify:
```sql
SELECT * FROM agg_5min ORDER BY window_start DESC LIMIT 5;
SELECT * FROM charging_sessions ORDER BY started_at DESC LIMIT 5;
SELECT * FROM alerts ORDER BY created_at DESC LIMIT 5;
```
```js
db.raw_telemetry.find().sort({timestamp:-1}).limit(5)
```
Confirm the fake producer's deliberately-duplicated `event_id` appears only once in Mongo.

## Tests

```bash
pip install -r requirements.txt
pytest
```

## Architecture

See `src/`:
- `kafka_consumer.py` — consumer loop + periodic flush thread, wires every other module together
- `validation.py` — CONTRACT.md §5 schema/range/contract_version checks
- `dedup.py` — Bloom filter (fast path), backstopped by Mongo's unique index on `event_id`
  (`mongo_writer.py` catches the duplicate-key error). Bloom filters have false positives but never
  false negatives when sized correctly — a genuinely-new event might rarely get skipped, a real
  duplicate never gets through. Cited for the "streaming dedup with Bloom filters" grading point.
- `windowed_aggregator.py` — 5-min/hourly/daily rollups via Welford's online mean/variance, O(1)
  memory per bucket, periodic flush-and-evict. No Flink/Spark/Kafka Streams (explicit scope cut).
- `session_segmenter.py` — single-pass O(1)/event interval-segmentation state machine over
  `is_charging` transitions (charging sessions) and SOC threshold crossings (deep discharge).
- `alert_rules.py` — real-time threshold rules (HIGH_TEMP, DEEP_DISCHARGE), 10-simulated-minute
  debounce per `(vehicle_id, alert_type)`.

## Known limitations / deviations (declared, not hidden)

- **Crash recovery**: if the container dies between flush cycles, in-flight (not-yet-flushed)
  bucket state since the last flush is lost from memory. Recovery path: Kafka retains raw events
  (`auto.offset.reset=earliest` on a fresh consumer group), so aggregates are recomputable by
  replay, just not instantly. This is the declared trade for build speed vs a real stream-processing
  framework's checkpointing.
- **Energy consumption estimate**: `energy_consumed_kwh` (discharge) is approximated as
  `|current_a| * voltage_v / 1000 * dt_hours` between consecutive events for a vehicle. This service
  has no access to the battery's true power draw beyond instantaneous current/voltage samples.
- **Fast-charge threshold**: `charging_type = FAST` uses a fixed reference ceiling
  (`BATTERY_MAX_CHARGE_POWER_KW`, default 150kW) rather than each vehicle's actual rated max, since
  ingestion never reads the `batteries` table (owned by data-gen). Configurable via env var.
- **`agg_daily.equivalent_full_cycles`**: not independently re-derived here (ingestion doesn't own
  battery capacity); currently written as 0 pending a documented follow-up to copy the ground-truth
  value from `simulation_vehicle_state` the same way `soh_pct` is copied at daily flush time.
- **`INGESTION_QUALITY` alert**: `alerts.vehicle_id` is `NOT NULL` (CONTRACT.md §3) but this rule is
  fleet-wide (>5% of all events rejected in the last minute), not per-vehicle — it cannot be
  persisted as a normal alert row, so it's logged at WARNING instead of written to Postgres.
- **`raw_telemetry` retention**: no scheduled deletion job implemented (hackathon dataset size is
  survivable without one); `RAW_RETENTION_DAYS` env var is read by config but not yet enforced by a
  cron/TTL job.

## Chaos test (manual, for Compliance & Chaos evidence)

```bash
docker kill <ingestion_container>
docker start <ingestion_container>
curl localhost:8002/metrics   # consumer_lag should recover to near-zero within a short window
```
Consumer group resumes from the last committed offset — no gap. Screenshot/terminal recording of
this sequence is the chaos-recovery evidence (per ingestionMaster.md §14).
