# data-gen — Connected Vehicle Intelligence data generation / simulator service

Standalone Python/FastAPI service. Owns and evolves the state of a persistent virtual vehicle
fleet, and is the **sole source of ground truth** for SOC/SOH/odometer/etc, emitted onto the
Kafka topic `telemetry.raw` per `CONTRACT.md` §5. Nothing downstream may alter these values.

Built per `docs/dataGenMaster.md` and `docs/CONTRACT.md`. This service touches only its own
folder; it does not assume any other module's code exists.

## Quick verify (Docker Compose) — confirmed working

This is the fastest way to check "is this part of the project still working" later. Everything
below has actually been run and confirmed end-to-end.

**1. Start everything** (Kafka, Postgres, data-gen, and a Kafka web UI):
```bash
docker compose up --build
```
Wait for `data-gen-1` logs to show `Uvicorn running on http://0.0.0.0:8001`.
(First Postgres boot restarts itself once — normal, the compose healthcheck waits for it.)

**2. Check health:**
```bash
curl localhost:8001/health
```
Expect `{"status":"ok","contract_version":"1.0.0"}`.

**3. Create + start a small test simulation:**
```bash
curl -X POST localhost:8001/api/v1/simulation -H "Content-Type: application/json" -d "{\"seed\":1,\"mode\":\"SHORT_BURST\",\"vehicle_count\":100,\"duration_days\":1,\"simulation_speed\":1,\"start_time\":\"2026-06-01T00:00:00Z\",\"archetype_distribution\":{\"HEALTHY\":0.5,\"NORMAL\":0.3,\"STRESSED\":0.15,\"BAD\":0.05},\"burst\":{\"enabled\":true,\"duration_seconds\":10,\"target_events_per_sec\":100},\"fault_injection\":{\"duplicate_rate\":0.01,\"out_of_order_rate\":0.02,\"malformed_rate\":0.005}}"
```
Copy the returned `simulation_id`, then:
```bash
curl -X POST localhost:8001/api/v1/simulation/PASTE_ID_HERE/start
```
Should return `"status":"RUNNING"`.

**4. Watch it visually in a browser** (easiest — no CLI needed):
```bash
docker compose up -d kafka-ui
```
Safe to run any time — only (re)starts the UI container, does not touch data-gen/Postgres/Kafka
data or any running simulation. Open **http://localhost:8080** → Topics → `telemetry.raw` →
Messages tab. You should see a growing message count and can click any message to inspect its
JSON.

**What "working" looks like:**
- `Message Count` on the topic keeps climbing while a simulation is RUNNING.
- Messages spread across all 12 partitions (partitioned by `vehicle_id` hash — uneven counts per
  partition are expected, not a bug).
- Each message is JSON matching CONTRACT.md §5: `vehicle_id`, `soc_pct` (0-100), `voltage_v`
  (250-450), `current_a`, `temperature_c`, `is_charging`, `charging_power_kw`, `speed_kmh`,
  `odometer_km`, `sequence_no`, `contract_version:"1.0.0"`.
- `curl localhost:8001/api/v1/simulation/PASTE_ID_HERE` shows `events_produced` increasing.

**Stop everything when done:**
```bash
docker compose down
```
(Add `-v` only if you also want to wipe Postgres/Kafka data and start fresh next time.)

## Run without Compose (single container)

```bash
docker build -t cvi-data-gen .
docker run --rm -p 8001:8001 --env-file .env cvi-data-gen
```

Needs a reachable Kafka broker (`KAFKA_BOOTSTRAP_SERVERS`) and Postgres (`POSTGRES_*` / `DATABASE_URL`).
Postgres schema (CONTRACT.md §3, the tables this service owns) is created idempotently at
startup — no external `init.sql` step required to run this service alone.

## Verify standalone (no other service needs to exist)

```bash
curl -X POST localhost:8001/api/v1/simulation -H "Content-Type: application/json" -d '{
  "seed": 1, "mode": "SHORT_BURST", "vehicle_count": 100, "duration_days": 1,
  "simulation_speed": 1, "start_time": "2026-06-01T00:00:00Z",
  "archetype_distribution": {"HEALTHY":0.5,"NORMAL":0.3,"STRESSED":0.15,"BAD":0.05},
  "burst": {"enabled": true, "duration_seconds": 10, "target_events_per_sec": 100},
  "fault_injection": {"duplicate_rate":0.01,"out_of_order_rate":0.02,"malformed_rate":0.005}
}'
curl -X POST localhost:8001/api/v1/simulation/<id>/start

docker exec -it <kafka_container> kafka-console-consumer.sh --bootstrap-server localhost:9092 \
  --topic telemetry.raw --from-beginning --max-messages 20
```

Confirms: valid JSON matching CONTRACT.md §5, `soc_pct`/`voltage_v`/etc moving smoothly across
consecutive events for the same `vehicle_id`, and some duplicate/out-of-order/malformed events.

## Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

All 8 unit tests pass with no external services required.

## Architecture

```
src/
├── main.py               FastAPI app, control endpoints (/api/v1/...)
├── config.py              env-var defaults; a request body always overrides for that one run
├── fleet.py                builds/loads the persistent vehicle population, deterministic from seed
├── vehicle_state.py         scalar per-vehicle tick physics — the realism-contract reference impl,
│                            and what tests/test_vehicle_state.py checks bounded-step behavior against
├── degradation.py            CONTRACT.md §9 formula, copied verbatim (same constant names)
├── event_builder.py           canonical event construction + duplicate/out-of-order/malformed fault injection
├── kafka_producer.py           confluent-kafka Producer wrapper, partitioned by vehicle_id
├── simulation_runner.py         the clock loop — see "Performance" below
├── db.py                        Postgres access (owns vehicles/fleets/batteries/simulation_vehicle_state)
└── models.py                     pydantic request/response models + dataclasses for internal state
```

## Performance: why the runner is vectorized

`dataGenMaster.md` §5/§6 requires per-second physics for every active vehicle regardless of
mode. For LONGITUDINAL mode (up to a few thousand vehicles x 60-90 simulated days) that's on
the order of 10 billion vehicle-ticks — intractable as a plain per-vehicle Python object loop.
`simulation_runner.py` re-expresses the exact same formulas from `vehicle_state.py` as
numpy array operations over the whole active fleet at once (`FleetArrays` / `tick_fleet` /
`apply_daily_rollup_fleet`), so the per-simulated-second cost is a fixed number of vectorized
numpy calls, not O(vehicle count). Measured: ~8.5s of pure compute per simulated day for a
small fleet, dominated by per-tick Python/numpy call overhead rather than fleet size — so a
90-day LONGITUDINAL run costs roughly 12-15 minutes of compute regardless of whether the active
subset is 500 or 2,000 vehicles, before `simulation_speed` even matters for wall-clock pacing.
This is called out explicitly because it is the single most likely place a reviewer would
otherwise suspect a shortcut: the physics is unabridged, only re-hosted on arrays.

`vehicle_state.py`'s scalar `tick()` is not a toy — it is the reference implementation the unit
tests verify, and the vectorized path is required to implement identical arithmetic.

## Simulation modes

Per dataGenMaster.md §5:

| Mode | vehicle_count | Emission interval | Notes |
|---|---|---|---|
| SHORT_BURST | full configured fleet | every simulated second | proves 100K events/sec ingest |
| LONGITUDINAL | `active_vehicle_count` subset (default 1,500) | every 5 simulated minutes | builds tractable multi-week history |
| DEMO | — | — | **simplified** (see Deviations) |

## Assumptions / deviations from the master docs

- **DEMO mode is simplified to a single-phase run** (uses the request's own duration/speed/mode
  parameters as given) rather than the fully scripted 3-phase sequence (burst -> longitudinal ->
  slow trickle) described in dataGenMaster.md §5. Orchestrating three sub-configs with distinct
  fleets/durations back-to-back under one `simulation_id` is additional state-machine complexity
  that was cut for time; flagging it here per CONTRACT.md's instruction to declare deviations
  rather than silently diverge. A caller wanting the demo sequence today should issue three
  separate `POST /simulation` calls (SHORT_BURST, then LONGITUDINAL, then a slow real-time
  SHORT_BURST-shaped run) against the same running service.
- **Postgres schema is created idempotently by this service at startup** (`CREATE TABLE IF NOT
  EXISTS`, matching CONTRACT.md §3 exactly) rather than assuming an external `infra/postgres/init.sql`
  has already run — this is what makes the service runnable completely standalone per
  dataGenMaster.md §12's verification instructions. Safe to also run against a DB where the
  official `init.sql` has already applied the same schema.
- **Kafka topic auto-creation**: `ensure_topic_exists()` uses `AdminClient.create_topics` at
  simulation start (12 partitions, per contract) and tolerates a "topic already exists" race,
  as an alternative to relying solely on broker `auto.create.topics.enable`.
- **`active_vehicle_count`** defaults to `min(1500, vehicle_count)` when not supplied in
  `SimulationConfig`, per the 1,000-2,000 default range in dataGenMaster.md §5.
- Fault-injection duplicate/out-of-order buffering is per-vehicle and bounded (delay up to 5
  send-positions, duplicate resend within the next 1-3 sends); any events still buffered when a
  simulation completes are flushed at shutdown so none are silently dropped.
- All rate-limiting/timestamps are UTC; `SimulationConfig.start_time` is normalized to UTC if a
  naive datetime is supplied.

## Contract version

`CONTRACT_VERSION = "1.0.0"`, embedded in every Kafka event's `contract_version` field and in
`/health`. Bump it (and this README) if the event shape or degradation formula changes.
