# ingestionMaster.md — Ingestion / Persistence / Aggregation Service (Part B)

**Read `CONTRACT.md` first.** You will NOT have access to data-gen, ML, or API code. Build this as a
standalone Kafka consumer service. It is done and correct when it can be tested against a small hand-written
producer script (given below) that mimics data-gen's output shape, without data-gen itself needing to exist.

Deliverable: complete `services/ingestion/` folder, brought up by `docker compose up ingestion` as the
`ingestion` container. Exposes only a health/metrics HTTP surface on port 8002 (no business endpoints — this
is a background worker, not a request/response API).

---

## 1. Purpose

Consumes `telemetry.raw` from Kafka. Does four things with every valid event, in this order:
1. Deduplicates (drop if `event_id` already seen).
2. Writes raw copy to MongoDB (`raw_telemetry`).
3. Updates Redis latest-state hash + runs real-time threshold alert rules.
4. Feeds an in-memory windowed aggregator that flushes 5-min/hourly/daily rollups to Postgres, and detects
   charging-session boundaries for the `charging_sessions` table.

**Per-event I/O must be batched/pipelined, not synchronous-per-message (found the hard way during
integration testing):** a naive implementation doing one Mongo `insert_one` plus one Redis `hset` plus
two Redis `srem` per event (4 network round trips per message, single-threaded consume loop) caps
throughput around ~400 events/sec against data-gen's ~1200+ events/sec production rate during a
`LONGITUDINAL` run — meaning consumer lag never drains and daily aggregates effectively never catch
up. At the real run's scale (tens of millions of events), that ceiling turns a 10-30 minute run into
many hours. Fix, required from the start:
- **Mongo**: buffer raw docs and flush with one `insert_many(docs, ordered=False)` per batch (e.g. every
  ~200 events, plus a time-based flush on the periodic aggregator-flush tick so a partial batch at the
  tail of a run doesn't sit unflushed) instead of `insert_one` per event.
- **Redis**: queue this event's state-hash update and alert-flag set/clear operations onto one
  `pipeline(transaction=False)` and call `.execute()` once per event, instead of separate round trips
  for each op.
- **Dedup consequence of batching Mongo**: once Mongo writes are batched, you no longer get a
  synchronous "was this a duplicate" answer before deciding whether to feed the event into the
  aggregator/segmenter. Accept this — the Bloom filter (§4 below) is the real synchronous dedup gate;
  Mongo's unique index on `event_id` is only a rare backstop for whatever the Bloom filter's
  false-negative rate lets through, and at this event-ID space size that's negligible. Count whatever
  duplicate-key errors a batch flush reports and fold them into `events_deduplicated_total` after the
  fact, rather than gating per-event processing on them.

This service is Kafka broker + plain consumer only — no Flink/Spark/Kafka Streams (explicit scope decision,
see CONTRACT.md §1).

---

## 2. Inner architecture

```
services/ingestion/
├── Dockerfile
├── requirements.txt
├── src/
│   ├── main.py                  # FastAPI app for /health + /metrics only; runs consumer as background task
│   ├── config.py
│   ├── kafka_consumer.py         # confluent-kafka Consumer, consumer group "ingestion-group"
│   ├── validation.py              # schema/range validation per CONTRACT.md §5, quarantine on failure
│   ├── dedup.py                   # event_id dedup (see §4)
│   ├── mongo_writer.py             # raw_telemetry inserts (batched)
│   ├── redis_writer.py             # latest-state hash updates
│   ├── alert_rules.py               # real-time threshold rules -> alerts table + Redis flag set
│   ├── windowed_aggregator.py       # 5-min/hourly/daily in-memory windowing (see §5)
│   ├── session_segmenter.py         # charging session interval detection (see §6)
│   ├── postgres_writer.py            # flush aggregates + sessions to Postgres
│   └── metrics.py                    # in-process counters exposed at /metrics
├── tests/
│   ├── test_dedup.py
│   ├── test_windowed_aggregator.py
│   ├── test_session_segmenter.py
│   └── test_validation.py
├── tools/
│   └── fake_producer.py              # standalone script, produces sample events matching CONTRACT.md §5, for testing this service before data-gen exists
└── README.md
```

requirements.txt (minimum):
```
fastapi
uvicorn[standard]
confluent-kafka
pymongo
redis
psycopg2-binary
sqlalchemy
pydantic
```

Dockerfile: `python:3.11-slim`, `CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8002"]`.

---

## 3. Inputs

- Kafka topic `telemetry.raw`, consumer group `ingestion-group`, `auto.offset.reset=earliest` (so a fresh
  container replays history rather than missing events — important for the "replay" requirement and for
  rebuilding after a reset).
- No HTTP inputs besides `/health` and `/metrics` (GET, no body).

### 3.1 `tools/fake_producer.py` (build this FIRST, before data-gen exists, to unblock your own testing)

A ~40-line script: connects to Kafka using the same bootstrap servers env var, produces N events matching
CONTRACT.md §5 exactly (a handful of hardcoded vehicle_ids, smoothly varying soc/temp across a loop, one
deliberate duplicate `event_id`, one deliberate out-of-order `sequence_no`, one deliberate malformed event
with `soc_pct: 999`). Run it manually (`python tools/fake_producer.py`) to generate a local test stream and
verify this service's behavior end to end without waiting on data-gen.

---

## 4. Deduplication (`dedup.py`)

Two-layer defense, both required:
1. **Fast in-process layer:** a bounded LRU set (or a Bloom filter — `pip install pybloom-live` — either is
   fine; a Bloom filter is the better answer for the "streaming algorithms" grading section, use it and say
   so explicitly in the write-up) of the last ~500K `event_id`s seen, checked before any DB write. This
   satisfies the "streaming dedup with Bloom filters" example explicitly named in the problem statement's
   Algorithms & Data Structures section — cite this in your Solution Document.
2. **Durable backstop:** MongoDB's unique index on `event_id` (CONTRACT.md §4) — if the in-memory filter false
   -positives or the process restarted and lost its in-memory state, the Mongo insert throws a duplicate-key
   error, which is caught and treated as "already processed, skip downstream steps" rather than a crash.

A Bloom filter has false positives (may skip a genuinely new event) but never false negatives (never lets a
real duplicate through) if sized correctly — acceptable trade for this use case, document the trade-off
explicitly rather than treating it as a hidden flaw.

---

## 5. Windowed aggregation (`windowed_aggregator.py`) — the "simple, no Flink" implementation

This is the core piece replacing a real stream-processing framework. Implement it exactly like this:

```
State kept in memory (a plain Python dict, keyed by (vehicle_id, window_start)):
  buckets_5min: Dict[(vehicle_id, window_start_ts), RunningStats]
  buckets_hourly: Dict[(vehicle_id, hour_start_ts), RunningStats]
  buckets_daily: Dict[(vehicle_id, date), RunningStats]

RunningStats accumulates incrementally per event (no need to store raw values — use streaming/online
formulas so memory stays O(1) per bucket regardless of event count):
  count, sum, sum_sq (for variance via Welford's algorithm or sum/sum_sq), min, max
  -- for energy/distance: accumulate deltas between consecutive events for that vehicle (needs last-seen
     odometer_km and a running energy integral: energy += power_kw * dt_hours)

On every valid, deduplicated event:
  1. compute window_start for 5-min bucket = floor(event.timestamp to nearest 5 min)
     compute hour_start, date similarly
  2. update (or create) the three RunningStats objects for this vehicle+window
  3. if this event's window_start is OLDER than the current "watermark" for that vehicle by more than a
     grace period (e.g. 2 minutes of simulated time) -> this is a very late out-of-order event; still fold
     it into the bucket's stats (correctness), but do NOT reopen an already-flushed bucket in Postgres --
     instead log it as a late-data metric (counts toward the "handles out-of-order data" grading point
     without needing full correction-write complexity)

Flush trigger (runs on a periodic background tick, e.g. every simulated-time-equivalent of 30 real seconds,
AND on graceful shutdown):
  for each bucket whose window has closed (window_end < current max-seen-timestamp - grace_period):
    UPSERT the row into agg_5min / agg_hourly / agg_daily (Postgres, ON CONFLICT DO UPDATE so re-flushing a
    bucket that gets a late correction just overwrites cleanly)
    remove the bucket from the in-memory dict (bounded memory - this is essential, do not let buckets
    accumulate forever)

Daily bucket ALSO gets its `soh_pct` field populated by reading the latest `simulation_vehicle_state.soh_pct`
for that vehicle+date from Postgres at flush time (ingestion doesn't compute SOH, data-gen owns that — this
is just copying the ground truth into the daily rollup for ML's convenience).
```

This gives you real windowed aggregation with bounded memory and periodic flush — a legitimate "streaming
algorithm" for the grading section (cite Welford's online variance + sliding window bucketing explicitly),
without needing Flink/Spark/Kafka Streams.

**Thread-safety requirement (found the hard way during integration testing — implement this from the start):**
event consumption and periodic flushing run on two separate threads (a Kafka consume loop thread and a
background flush-timer thread). Both read AND mutate the same `buckets_5min`/`buckets_hourly`/`buckets_daily`
dicts. Without a lock around every access, the flush thread's iteration over a bucket dict races against the
consume thread inserting a new key mid-iteration, raising `RuntimeError: dictionary changed size during
iteration` on every flush cycle — which means `agg_5min`/`agg_hourly`/`agg_daily` silently stay empty forever
while raw Mongo ingestion keeps working fine (a misleading symptom: it looks like a Postgres problem, it's
actually a threading bug in the aggregator). Fix: guard the bucket dicts with a single `threading.Lock`,
acquired by `add_event()` and by the bucket-popping step of the flush. To avoid holding the lock across a slow
Postgres write (which would stall the consume thread), pop the closed buckets out under the lock into a plain
list first, release the lock, then do the actual `UPSERT` calls outside the lock.

**Known limitation to declare honestly in Risks/ADRs:** if this container crashes between flushes, in-flight
(not-yet-flushed) bucket data since the last flush is lost from memory. Recovery path: Kafka retains the raw
events (replay from `auto.offset.reset=earliest` on a fresh consumer group, or manually reset offsets) so
aggregates ARE recomputable, just not instantly. This is a deliberate trade for build speed vs a real
framework's checkpointing — say so, don't hide it.

---

## 6. Charging session segmentation (`session_segmenter.py`) — the "Algorithms & Data Structures" deliverable

Per-vehicle, track `is_charging` transitions as a simple state machine (this is the interval-segmentation /
streaming algorithm to cite in the Solution Document's Algorithms section):

```
State per vehicle: current_session (None or {started_at, start_soc, energy_accum, max_power_seen})

On each event for that vehicle:
  if event.is_charging and current_session is None:
      open a new session: {started_at: event.timestamp, start_soc: event.soc_pct, energy_accum: 0, max_power_seen: 0}
  if event.is_charging and current_session is not None:
      current_session.energy_accum += event.charging_power_kw * dt_hours_since_last_event
      current_session.max_power_seen = max(current_session.max_power_seen, event.charging_power_kw)
  if not event.is_charging and current_session is not None:
      close the session: ended_at = previous event's timestamp, end_soc = previous event's soc_pct,
      charging_type = 'FAST' if max_power_seen > 0.6 * battery.rated max else 'NORMAL'
      INSERT into charging_sessions (Postgres)
      current_session = None
```

This is a classic single-pass interval-merge/segmentation over a stream — an O(1)-per-event, O(1)-memory
streaming algorithm. Frame it this way explicitly in the write-up (satisfies "trip segmentation" style
requirement from the problem statement's algorithms table, adapted to charging sessions since this project
scoped out location/trip data).

Also increment the vehicle's running `fast_charge_sessions` / `deep_discharge_events` counters used by
`agg_daily` (deep discharge = a similar single-pass state machine watching for `soc_pct` crossing below 20%
and back above it).

---

## 7. Real-time alert rules (`alert_rules.py`)

Simple threshold rules evaluated per event (no ML involved — these are the "operational" real-time alerts,
distinct from the ML-driven predictive alerts the API/ML services create later):

| Rule | Condition | alert_type | severity |
|---|---|---|---|
| High temperature | `temperature_c > 45` | `HIGH_TEMP` | CRITICAL |
| Deep discharge | `soc_pct < 15` | `DEEP_DISCHARGE` | WARNING |
| Malformed event rate spike | (metrics-based, not per-event) >5% of events in last minute failed validation | `INGESTION_QUALITY` | WARNING |

On trigger: INSERT into `alerts` (Postgres, `source='REALTIME_RULE'`), and add the alert type to
`vehicle:alert_flags:<vehicle_id>` (Redis SET, CONTRACT.md §6) so the dashboard can show it instantly without
a DB round-trip. Debounce: don't re-fire the same `(vehicle_id, alert_type)` combination more than once per 10
simulated minutes (track last-fired time in the same in-memory structure as the aggregator, or a small
separate dict).

---

## 8. Validation (`validation.py`)

Reject (do not persist, but DO increment a `rejected_events_total` metric and log at WARNING level) any event
that:
- fails JSON parse
- is missing a required field from CONTRACT.md §5
- has any field outside the range table in CONTRACT.md §5
- has `contract_version` mismatched with this service's own `CONTRACT_VERSION` env var (log at ERROR — this
  indicates a real integration bug, not expected test noise)

This is what proves the "schema-validated, back-pressure-aware intake" requirement — the malformed events
data-gen deliberately injects should show up here as rejections, which becomes a demo talking point.

---

## 9. Outputs

1. `raw_telemetry` documents in MongoDB.
2. `vehicle:state:*` and `vehicle:alert_flags:*` keys in Redis.
3. `agg_5min`, `agg_hourly`, `agg_daily`, `charging_sessions`, `alerts` rows in Postgres.
4. `/metrics` (plain JSON, not full Prometheus format unless you have time) exposing at minimum:
   `events_consumed_total`, `events_deduplicated_total`, `events_rejected_total`, `consumer_lag` (from Kafka
   consumer group offsets vs high-water mark), `current_watermark_timestamp`. This is your "observability"
   deliverable — the dashboard's Observability section (dashboardMaster.md) pulls from here.

---

## 10. What this service assumes about the rest of the system

- Kafka topic `telemetry.raw` already exists and has events matching CONTRACT.md §5 (from data-gen, or from
  `tools/fake_producer.py` during standalone testing).
- Postgres schema already applied.
- This service NEVER writes to `vehicles`, `fleets`, `simulations`, or `simulation_vehicle_state` — those are
  data-gen's tables. It only reads `simulation_vehicle_state.soh_pct` (for daily aggregate enrichment) and
  writes to the aggregate/session/alert tables it owns.

---

## 11. Dependents

ML reads `agg_daily` (and optionally `agg_hourly`) to build feature vectors — do not change those table shapes
without updating CONTRACT.md §3. API reads Redis latest-state, `alerts`, `agg_*` for dashboard queries, and
this service's `/metrics` for the observability page.

---

## 12. How to run and verify standalone

```bash
docker compose up kafka postgres mongo redis ingestion
python services/ingestion/tools/fake_producer.py   # run from host, or add a one-off container
curl localhost:8002/metrics
```

Then check:
```sql
SELECT * FROM agg_5min ORDER BY window_start DESC LIMIT 5;
SELECT * FROM charging_sessions ORDER BY started_at DESC LIMIT 5;
SELECT * FROM alerts ORDER BY created_at DESC LIMIT 5;
```
and in Mongo: `db.raw_telemetry.find().sort({timestamp:-1}).limit(5)`, and confirm the deliberately-duplicated
`event_id` from the fake producer appears only once.

---

## 13. Tests to include

- `test_dedup.py`: feed the same `event_id` twice, assert second is dropped before any DB write is attempted
  (mock the writers).
- `test_windowed_aggregator.py`: feed a synthetic sequence of events across a bucket boundary, assert the
  flushed row's avg/min/max/count match hand-computed expected values; feed one deliberately out-of-order
  event and assert it's folded into the correct historical bucket without crashing.
- `test_session_segmenter.py`: feed a charging-start/charging-end event sequence, assert exactly one session
  is created with correct `start_soc`/`end_soc`/`energy_delivered_kwh`.
- `test_validation.py`: assert each CONTRACT.md §5 range violation is rejected, and a fully valid event
  passes.

---

## 14. Chaos test hook (for the "Compliance and Chaos" testing evidence)

Document (in this service's README, not code) a manual chaos demo: `docker kill <ingestion_container>` mid-run,
restart it, and show that (a) the consumer group resumes from the last committed offset (no gap), and (b)
`consumer_lag` in `/metrics` recovers to near-zero within a short window. This is your chaos-recovery evidence
— a screenshot/terminal recording of this sequence is sufficient, no need to automate it.
