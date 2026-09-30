# dataGenMaster.md — Data Generation / Simulator Service (Part A)

**Read `CONTRACT.md` first — this doc assumes it.** You (the AI building this) will NOT have access to the
ingestion, ML, or API services' code. Build this as a fully standalone Python service. It is done and correct
when it can run alone, produce valid events onto the Kafka topic defined in the contract, and be verified with
nothing more than a throwaway Kafka console consumer.

Deliverable: a complete `services/data-gen/` folder (Dockerfile, requirements.txt, src/, tests/, README.md)
that a `docker compose up` brings alive as the `data-gen` container, exposing the control API on port 8001.

---

## 1. Purpose

Owns and evolves the state of 100,000 persistent virtual vehicles. Produces the canonical telemetry event
(per CONTRACT.md §5) onto Kafka. Is the **sole source of ground truth** for SOC/SOH/odometer/etc — nothing
downstream is allowed to alter these values, only read/derive from the emitted events.

---

## 2. Inner architecture

```
services/data-gen/
├── Dockerfile
├── requirements.txt
├── src/
│   ├── main.py                # FastAPI app, control endpoints
│   ├── config.py               # loads env vars + request-supplied SimulationConfig
│   ├── fleet.py                 # builds/loads the persistent 100K-vehicle population
│   ├── vehicle_state.py         # per-vehicle dynamic state + per-tick physics update
│   ├── degradation.py            # implements CONTRACT.md §9 formula exactly
│   ├── event_builder.py          # builds the canonical event dict, injects noise/duplicates/out-of-order
│   ├── kafka_producer.py         # wraps confluent-kafka Producer, partitions by vehicle_id
│   ├── simulation_runner.py      # the clock loop: advances sim time, ticks vehicles, calls producer
│   ├── db.py                     # writes fleet config (vehicles/batteries/etc) + simulation_vehicle_state to Postgres
│   └── models.py                 # dataclasses/pydantic models for internal state
├── tests/
│   ├── test_degradation.py
│   ├── test_event_builder.py
│   └── test_vehicle_state.py
└── README.md
```

requirements.txt (minimum):
```
fastapi
uvicorn[standard]
confluent-kafka
psycopg2-binary
sqlalchemy
pydantic
numpy
python-dotenv
```

Dockerfile: standard `python:3.11-slim` base, `pip install -r requirements.txt`, `CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]`, `EXPOSE 8001`.

---

## 3. Inputs

### 3.1 Control API (this service's own HTTP surface, port 8001)

All under `/api/v1/`. No auth required on this service directly (it sits behind the `api` service in
production topology, but for the hackathon it's reachable directly for operator/demo convenience — document
this as a simplification).

| Method | Path | Body | Effect |
|---|---|---|---|
| POST | `/simulation` | `SimulationConfig` (below) | creates a new simulation run, writes row to Postgres `simulations`, returns `simulation_id` |
| POST | `/simulation/{id}/start` | — | begins the clock loop (background task), starts producing to Kafka |
| POST | `/simulation/{id}/pause` | — | pauses the clock loop, no event production |
| POST | `/simulation/{id}/resume` | — | resumes |
| POST | `/simulation/{id}/reset` | — | stops loop, clears `simulation_vehicle_state` rows for this simulation_id, restores initial state, sets status back to CREATED |
| GET | `/simulation/{id}` | — | status, current sim time, events produced count, vehicles count, `progress_pct` (0-100), `elapsed_seconds`, `eta_seconds` (ETA computed from the actual observed tick rate so far, not the configured `simulation_speed` — stays accurate even if real throughput differs from the target; `null` before the run has started, `0` once `COMPLETED`) |
| GET | `/simulations` | — | list, keyset paginated per CONTRACT.md §8 |
| GET | `/health` | — | `{"status":"ok","contract_version":"1.0.0"}` |

### 3.2 SimulationConfig (request body for `POST /simulation`)

```json
{
  "seed": 42,
  "mode": "LONGITUDINAL",
  "vehicle_count": 100000,
  "duration_days": 90,
  "simulation_speed": 2880,
  "start_time": "2026-06-01T00:00:00Z",
  "archetype_distribution": { "HEALTHY": 0.35, "NORMAL": 0.45, "STRESSED": 0.15, "BAD": 0.05 },
  "burst": { "enabled": false, "duration_seconds": 60, "target_events_per_sec": 100000 },
  "fault_injection": {
    "duplicate_rate": 0.01,
    "out_of_order_rate": 0.02,
    "malformed_rate": 0.005
  }
}
```

- `mode`: one of `SHORT_BURST`, `LONGITUDINAL`, `DEMO` (see §5).
- `simulation_speed`: simulated-seconds-per-real-second. `2880` means 1 simulated day (86400s) passes in 30
  real seconds — tune so a 90-day `LONGITUDINAL` run finishes in a few real minutes. This is the single most
  important number to get right for your own iteration speed; start conservative (finish in under 10 minutes)
  and increase if it's too slow.
- `archetype_distribution` must sum to 1.0; validate and reject otherwise (400 error, contract §8 error
  shape).
- `fault_injection` only applies meaningfully in `SHORT_BURST`/`DEMO` modes — see §6.

### 3.3 Config precedence

Defaults come from `.env` (fleet size default, default seed, etc). A request body always overrides env
defaults for that one simulation run. Never mutate global env-derived defaults from a request.

---

## 4. Fleet population (built once, reused/reset across simulations of the same `vehicle_count`)

On first `POST /simulation` with a given `vehicle_count`, if `vehicles` table is empty, build the persistent
population **deterministically from `seed`** (use `numpy.random.default_rng(seed)` everywhere, never bare
`random`, so two runs with the same seed produce an identical fleet):

**Known limitation (found during integration testing, not fixed — document, don't silently paper over):**
`build_or_load_fleet()`'s logic is only "have >= vehicle_count already → load and reuse" or else "build fresh
from `i=0`." It does NOT support growing an existing smaller fleet to a larger `vehicle_count` — vehicle IDs
always start at `EV-000000000`, so requesting a larger `vehicle_count` than what's already persisted attempts
to re-insert IDs that already exist, violating the `vehicles.vehicle_id` primary key and crashing the request.
Practical consequence: if you smoke-tested with 500 vehicles and now want to run the real 1,000-2,000 vehicle
job, you must `TRUNCATE` the fleet tables first (`vehicles`, `batteries`, and everything that references them
— see INTEGRATION.md) so it builds fresh at the new target size, rather than expecting it to top up the
existing fleet.

For each vehicle `i` in `range(vehicle_count)`:
1. `vehicle_id = f"EV-{i:09d}"`
2. Assign `fleet_id` — distribute vehicles across ~20 synthetic fleets (just insert 20 `fleets` rows once,
   round-robin or weighted-random assignment).
3. Assign `model_id` — from a small fixed set of ~8 synthetic `vehicle_models` rows (invent plausible
   manufacturer/model/year combos, doesn't need to be real cars).
4. Assign `archetype` by weighted draw from `archetype_distribution`.
5. Build a `battery`: pick a `chemistry_id` from ~2 seeded `battery_chemistries` rows (e.g. NMC with
   `calendar_factor=1.0, cycle_factor=1.0`, LFP with `calendar_factor=0.7, cycle_factor=0.6` — LFP degrades
   slower, a realistic detail worth having), `nominal_capacity_kwh` drawn from a reasonable range (40–100),
   `rated_voltage_v` correlated with capacity (larger pack → higher voltage, e.g. 350–420V).
6. `manufacture_date` / `initial_odometer_km` randomized plausibly (e.g. manufacture 0–5 years before
   `start_time`; higher age → higher initial odometer, correlated, not independent).
7. Insert `vehicles`, `batteries` rows. Initialize `simulation_vehicle_state` row: `soc_pct` random 20-90,
   `soh_pct=100.0` minus a small age-correlated head start (older vehicles start with slightly lower SOH, e.g.
   `100 - age_years * 0.8`, clipped ≥ 90), `usable_capacity_kwh = nominal_capacity_kwh * soh_pct/100`,
   `battery_temp_c` = ambient default (e.g. 25), `equivalent_full_cycles` estimated from age, all exposure
   counters at 0.

**This is the single most important realism rule in the whole generator: nothing here is drawn independently.**
Age correlates with odometer, odometer correlates with cycle count, archetype correlates with future stress
exposure. This is what makes the resulting dataset look like a real fleet instead of random noise (this
directly answers the "does the data look real" concern from project planning).

**Timezone requirement (found the hard way during integration testing):** `start_time` arrives from the
`SimulationConfig` as a timezone-aware `datetime` (parsed from an ISO string like `"2026-07-01T00:00:00Z"`).
Every `manufacture_date` computed anywhere in this service — both on first build (fresh fleet, derived from
`start_time - age`) AND on every subsequent run that loads the already-persisted fleet back out of Postgres
(a `DATE` column, which comes back from the DB driver as a naive `date`/`datetime` with no tzinfo) — must be
made timezone-aware (`tzinfo=timezone.utc`) before it's ever subtracted from `start_time` anywhere (age
calculations in `fleet.py`, `simulation_runner.py`, etc). Mixing a naive and an aware datetime in a subtraction
raises `TypeError: can't subtract offset-naive and offset-aware datetimes`. This bug is easy to miss because
the FIRST simulation run (fresh fleet build) works fine — the failure only appears on the SECOND run onward,
once the fleet is being loaded back from Postgres instead of freshly generated, which makes it easy to
mistake for a flaky/intermittent issue instead of the deterministic load-path bug it actually is.

---

## 5. Simulation modes

IMPORTANT scale rule: `SHORT_BURST` and `LONGITUDINAL` are NOT the same tick loop at different speeds.
Emitting one event per simulated second for the full 100K-vehicle fleet across 90 simulated days is roughly
777 billion events -- explicitly the thing CONTRACT.md's design principles say to avoid ("100K events/sec is
a platform/ingestion benchmark, not a requirement to generate and permanently store 100K events/sec for 90
simulated days"). `simulation_speed` only compresses wall-clock time between ticks; it does not reduce event
count, so accelerating the clock alone does not fix the volume problem. The two modes differ in BOTH vehicle
count AND emission interval, not just speed:

| Mode | vehicle_count | Emission interval (internal physics still updates every simulated second; only EMISSION to Kafka is throttled) | Duration | Purpose |
|---|---|---|---|---|
| SHORT_BURST | Full configured fleet (up to 100,000) | 1 simulated second (every tick emitted) | burst.duration_seconds (30-120s), real-time | Proves 100K events/sec ingest throughput -- the NFR/scale evidence, kept deliberately short. |
| LONGITUDINAL | A representative subset (default 1,000-2,000 vehicles, config field active_vehicle_count, must be <= vehicle_count) | 5 simulated minutes (1 event per vehicle per 5-min window, not every second) | duration_days (60-90), accelerated via simulation_speed | Builds real multi-week history for aggregates + ML training at tractable volume: e.g. 1,500 vehicles x 90 days x 288 intervals/day = ~39M events total, not billions. |
| DEMO | Scripted sequence: (1) SHORT_BURST ~30s on the full fleet for throughput, (2) LONGITUDINAL ~14 days on the subset for fast history accumulation, (3) slow real-time trickle (1 simulated second per real second, small subset, full per-second emission) so the dashboard visibly "lives" during a recording. |

The 100,000-vehicle requirement is satisfied by (a) the persistent Postgres `vehicles` population always being
generated at full configured size regardless of mode, and (b) SHORT_BURST proving the platform ingests all
100,000 of them concurrently at full 1Hz rate. LONGITUDINAL's smaller active subset is a deliberate, declared
scope decision for build-time tractability -- state this explicitly in the Solution Document rather than
letting it look like an oversight; it does not weaken the scale claim since scale is proven by SHORT_BURST
independently.

Implementation note: still one tick loop internally (physics updates every simulated second regardless of
mode, so degradation/state stay accurate), but a per-mode `emit_interval_seconds` config gates which ticks
actually get built into a Kafka event and sent. DEMO is a scripted sequence of three sub-configs run back to
back by `simulation_runner.py`.

---

## 6. Per-tick vehicle physics update (`vehicle_state.py`) — THE REALISM CONTRACT

This is the part that prevents "SOC=10 then 5 then 15 next second" garbage. State evolves by **bounded delta
from previous state**, never resampled independently. Called once per simulated second per vehicle.

```
Inputs available each tick: previous state, archetype, battery spec, time-of-day, ambient_temperature_c
  (derive ambient from a smooth daily sine curve + small seeded regional offset, NOT random per tick)

1. Decide behavior mode for this tick (driving / charging / idle), using a per-vehicle Markov-ish schedule:
   - each vehicle has a seeded "daily routine" (e.g. commute windows) so transitions are not memoryless
     random noise; within a routine window, sample transitions with sticky probabilities (e.g. 98% chance
     stay in current mode each tick, 2% chance transition) so states last minutes, not single ticks.

2. If DRIVING:
   speed_kmh moves toward a target speed by a bounded step (e.g. +/- 5 km/h per tick), never teleports.
   current_a = discharge current derived from speed + a small archetype-driven "aggressiveness" factor
     (STRESSED/BAD archetypes draw higher current for the same speed -> more thermal stress, more cycles)
   odometer_km += speed_kmh / 3600  (per-second distance)
   soc_pct -= current_draw_to_soc_delta(current_a, capacity_kwh)   -- bounded small decrement
   is_charging = false, charging_power_kw = 0

3. If CHARGING:
   is_charging = true
   charging_power_kw moves toward a target charger power by a bounded step (ramps up/down, doesn't jump)
   current_a = negative value derived from charging_power_kw and voltage_v
   soc_pct += charge power converted to soc delta, capped so soc_pct never exceeds 100
   speed_kmh = 0

4. If IDLE:
   current_a decays toward 0 by a bounded step (small parasitic draw ~0.5A)
   soc_pct essentially flat (tiny self-discharge decrement)
   speed_kmh = 0, is_charging = false

5. voltage_v: correlated with soc_pct and current_a (higher SOC -> higher OCV; higher discharge current ->
   voltage sag). Use a simple linear/quadratic relation, e.g.:
     base_voltage = rated_voltage_v * (0.85 + 0.15 * soc_pct/100)
     voltage_v = base_voltage - (current_a * internal_resistance_ohm)   # sag under load, rise under charge (current_a negative)
   Add small bounded gaussian noise (std ~0.3% of value), not big jumps.

6. temperature_c: moves toward an equilibrium driven by |current_a| and ambient_temperature_c, bounded step
   per tick (thermal mass -> can't change fast):
     target_temp = ambient_temperature_c + k_heat * abs(current_a)
     temperature_c += clip(target_temp - temperature_c, -max_step, +max_step)   # e.g. max_step = 0.05 C/tick

7. All deltas above are "bounded step toward a target" — this single pattern (exponential-approach-to-target
   with a max per-tick step, plus small bounded noise) is what must be used for EVERY continuous field. Do
   not use `random.uniform(min, max)` to directly set any physical field's next value.

8. Once per simulated DAY boundary (not every tick):
   - compute equivalent_full_cycles_today, hours_above_35C_today, fast_charge_sessions_today (charging_power_kw
     > 0.6 * battery max power observed during a session), deep_discharge_events_today (soc_pct crossed below
     20% and back), from the ticks accumulated that day
   - call degradation.py's compute_daily_degradation() per CONTRACT.md §9 formula
   - soh_pct -= daily_degradation_pct (floor at 60.0)
   - usable_capacity_kwh = nominal_capacity_kwh * soh_pct / 100
   - write a `simulation_vehicle_state` row snapshot (this is the ground truth row ML will read)
```

`degradation.py` must implement CONTRACT.md §9 exactly (same constant names) — this is the one place where a
deviation between this doc and CONTRACT.md would silently break ML's label construction, so copy the formula
verbatim, don't paraphrase it.

---

## 7. Event construction & fault injection (`event_builder.py`)

For every tick of every vehicle, build the canonical event (CONTRACT.md §5 shape) with `sequence_no`
incrementing per-vehicle monotonically in TRUE generation order (fault injection reorders/duplicates what's
*sent*, never what's *generated* — sequence_no reflects true order so ingestion can detect out-of-order
arrivals correctly).

Fault injection (only meaningfully enabled when `fault_injection` rates > 0, i.e. `SHORT_BURST`/`DEMO`):
- **Duplicate:** with probability `duplicate_rate`, after sending an event, re-send the exact same event
  (same `event_id`) again within the next few sends.
- **Out-of-order:** with probability `out_of_order_rate`, hold an event back and send it 1–5 positions later
  than its natural order (buffer a small deque, delay insertion).
- **Malformed:** with probability `malformed_rate`, corrupt one field (e.g. `soc_pct: 150`, or drop a
  required field) — used to prove ingestion's schema validation/quarantine path works. Document these events
  as intentionally invalid so the demo can point at "N events rejected by validation" as a feature, not a bug.

---

## 8. Kafka producer (`kafka_producer.py`)

- `confluent-kafka` `Producer`, `key = vehicle_id.encode()`, `value = json.dumps(event).encode()`.
- Use `acks=all` for correctness in the write-up, but don't block on delivery per-message in the hot loop —
  use `produce()` with a delivery callback and call `poll(0)` periodically, `flush()` at shutdown/pause.
- Batch: rely on librdkafka's internal batching (`linger.ms=5`, `batch.num.messages=10000`) to hit the
  100K events/sec target during `SHORT_BURST` — this is the actual throughput lever, not application-level
  threading tricks.

---

## 9. Outputs

1. Kafka topic `telemetry.raw` — the primary output, per CONTRACT.md §5.
2. Postgres `vehicles`, `fleets`, `vehicle_models`, `batteries`, `battery_chemistries`, `simulations`,
   `simulation_vehicle_state` rows — written directly by this service (it owns these tables; ingestion never
   writes to them, only reads `vehicles`/`simulations` for joins if needed).
3. Control API responses (§3.1).

---

## 10. What this service assumes about the rest of the system (its copy of the shared schema)

- Kafka topic `telemetry.raw` exists with 12 partitions before this service starts producing (create it at
  container startup if missing — `confluent-kafka`'s `AdminClient` can do this, or rely on Kafka
  `auto.create.topics.enable=true` in the broker config for simplicity, and note that explicitly).
- Postgres schema from CONTRACT.md §3 already applied (`infra/postgres/init.sql` run on container boot via
  the official Postgres image's docker-entrypoint-initdb.d mechanism).
- `CONTRACT_VERSION` env var = `1.0.0`; this service embeds it in every event's `contract_version` field and
  in its `/health` response. If you change anything in this doc that affects the wire format, bump this and
  say so loudly in your README — every other module breaks silently otherwise.

---

## 11. Dependents

Everything downstream (ingestion, and transitively ML, API) depends on this service's Kafka output and
`simulation_vehicle_state` ground truth. Nothing depends on this service's internal code, only its two
outputs (Kafka events + those specific Postgres tables). If you change the physics model, the event shape, or
the degradation formula, you MUST update CONTRACT.md §5/§9 to match — don't let this doc and the contract
drift.

---

## 12. How to run and verify standalone (before ingestion exists)

```bash
cd services/data-gen
docker build -t cvi-data-gen .
docker run --rm -p 8001:8001 --env-file ../../.env cvi-data-gen
```

Or via the full `docker compose up data-gen kafka postgres` (only bring up its real dependencies).

Verify without ingestion existing at all:
```bash
# create + start a tiny simulation
curl -X POST localhost:8001/api/v1/simulation -d '{"seed":1,"mode":"SHORT_BURST","vehicle_count":100,"duration_days":1,"simulation_speed":1,"start_time":"2026-06-01T00:00:00Z","archetype_distribution":{"HEALTHY":0.5,"NORMAL":0.3,"STRESSED":0.15,"BAD":0.05},"burst":{"enabled":true,"duration_seconds":10,"target_events_per_sec":100},"fault_injection":{"duplicate_rate":0.01,"out_of_order_rate":0.02,"malformed_rate":0.005}}'
curl -X POST localhost:8001/api/v1/simulation/<id>/start

# watch raw events land on Kafka with a throwaway console consumer
docker exec -it <kafka_container> kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic telemetry.raw --from-beginning --max-messages 20
```

Confirms: events are valid JSON matching CONTRACT.md §5, `soc_pct`/`voltage_v`/etc move smoothly across
consecutive events for the same `vehicle_id` (spot-check by eye), and some duplicate/out-of-order/malformed
events appear as expected.

---

## 13. Tests to include (unit, no external services required)

- `test_degradation.py`: given fixed inputs, `compute_daily_degradation()` returns the expected value per the
  formula in CONTRACT.md §9; verify clipping at 0.05 max and SOH floor at 60.0.
- `test_vehicle_state.py`: given a starting state and 100 simulated ticks of "driving" then "charging", assert
  no field ever changes by more than its defined max-step-per-tick (this is the automated proof of the
  realism contract in §6 — write it as a hard assertion, not a visual check).
- `test_event_builder.py`: assert every built event matches the CONTRACT.md §5 field ranges; assert
  `sequence_no` is monotonic in true generation order even when fault injection reorders send order.
