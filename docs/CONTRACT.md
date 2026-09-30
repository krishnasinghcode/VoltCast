# CONTRACT.md — Connected Vehicle Intelligence Platform
## Shared Schema, Ports, and Integration Contract (v1.0)

**This file is the single source of truth for every cross-service interface.**
Every module (`dataGenMaster.md`, `ingestionMaster.md`, `mlMaster.md`, `dashboardMaster.md`) is built by a
**separate AI instance with zero access to the other modules' code.** If any two modules disagree, THIS FILE
wins. If you (the builder of a module) need to change something here, you cannot — flag it in your module's
README as an "assumption/deviation" instead of silently changing the contract. `INTEGRATION.md` is the only
place allowed to reconcile deviations across modules after all four are built.

Every module doc embeds a **copy** of the relevant sections of this contract inline (so that module can be
built with zero other files open). If a module's embedded copy and this file ever disagree after independent
edits, THIS FILE is authoritative and the module must be patched to match it.

Version tag for this contract: `CONTRACT_VERSION = "1.0.0"`. Every Kafka event, every Postgres migration file,
and every service's `/health` response MUST embed this string. A mismatch anywhere is a hard integration bug,
not a warning.

---

## 0. Repository layout (canonical — every module builder uses this exact tree)

```
cvi-platform/
├── docker-compose.yml
├── .env.example
├── infra/
│   ├── postgres/
│   │   └── init.sql              # full DDL from section 3, run on first Postgres boot
│   ├── k6/
│   │   └── burst_load_test.js    # load test script, hits API + optionally raw Kafka producer
│   └── grafana/ (optional, skip if out of time)
├── services/
│   ├── data-gen/                 # Part A — owns docs/dataGenMaster.md
│   ├── ingestion/                # Part B — owns docs/ingestionMaster.md
│   ├── ml/                       # Part C — owns docs/mlMaster.md
│   └── dashboard/                # Part D — owns docs/dashboardMaster.md
│       ├── api/                  # backend
│       └── frontend/             # React app
│       # NOTE: actual build placed both under services/dashboard/ instead of the originally
│       # specced top-level services/api/ + frontend/. docker-compose.yml, .github/workflows/ci.yml
│       # and both Dockerfiles were updated consistently to match — this is the real, current layout.
├── docs/
│   ├── CONTRACT.md               # this file
│   ├── dataGenMaster.md
│   ├── ingestionMaster.md
│   ├── mlMaster.md
│   ├── dashboardMaster.md
│   └── INTEGRATION.md
└── tests/
    └── integration/              # cross-service tests, added last
```

Each `services/<name>/` folder is a **self-contained deliverable**: its own `Dockerfile`, `requirements.txt`,
`src/`, `tests/`, and `README.md`. A module builder must ONLY touch its own `services/<name>/` (or
`frontend/`) folder plus, if genuinely necessary, append (never rewrite) to `.env.example` and
`docker-compose.yml` under a clearly-commented section for that service. Do not edit another service's folder.

---

## 1. Tech stack (locked — do not substitute)

| Concern | Choice | Why |
|---|---|---|
| All backend services | Python 3.11+ | one language across data-gen/ingestion/ml/api — minimizes cross-module integration friction when built by different AI instances |
| HTTP framework | FastAPI | async, auto OpenAPI docs, fast to build |
| Kafka client | `confluent-kafka` (Python) | stable, widely documented, supports manual partitioning by key |
| Kafka broker | `bitnami/kafka` (KRaft mode, no Zookeeper) | single-container simplicity |
| Relational DB | PostgreSQL 16 | ACID core |
| Document DB | MongoDB 7 | raw telemetry firehose |
| Cache / fast state | Redis 7 | latest vehicle state, rate-limit counters |
| ORM / DB access | SQLAlchemy 2.x (Postgres), `pymongo` (Mongo), `redis-py` (Redis) | standard |
| ML | scikit-learn | GradientBoostingRegressor, trains in seconds on this data size |
| Frontend | React 18 + Vite + TypeScript | fast dev loop |
| Charts | Recharts or Chart.js | either fine, pick one and stay consistent |
| Load testing | k6 | explicitly named in problem statement |
| Container orchestration | Docker Compose only | no Kubernetes — out of scope by decision |
| CI | GitHub Actions | lint + unit tests on push |
| Auth | JWT (HS256, shared secret via env var) | minimal-viable, documented as a scoped-down NFR |

---

## 2. Service map (ports, hostnames inside Docker network `cvi-net`)

| Service | Container hostname | Port (host:container) | Purpose |
|---|---|---|---|
| Kafka | `kafka` | 9092:9092 | event backbone |
| PostgreSQL | `postgres` | 5432:5432 | relational core |
| MongoDB | `mongo` | 27017:27017 | raw telemetry |
| Redis | `redis` | 6379:6379 | latest state + cache + rate limiting |
| data-gen | `data-gen` | 8001:8001 | simulation control API + Kafka producer |
| ingestion | `ingestion` | 8002:8002 | Kafka consumer(s), health/metrics only |
| ml | `ml` | 8003:8003 | training + prediction service |
| api | `api` | 8000:8000 | main REST API, JWT auth, SQL sandbox, serves as backend for frontend |
| frontend | `frontend` | 5173:5173 (dev) / 3000:80 (prod nginx) | React dashboard |

Every service reads its peer addresses from environment variables (see §7), never hardcodes hostnames beyond
what's in `.env.example`.

---

## 3. PostgreSQL schema (run once as `infra/postgres/init.sql`)

```sql
-- CONTRACT_VERSION 1.0.0

CREATE TABLE fleets (
    fleet_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    region          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE vehicle_models (
    model_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manufacturer    TEXT NOT NULL,
    model_name      TEXT NOT NULL,
    model_year      INT NOT NULL
);

CREATE TABLE battery_chemistries (
    chemistry_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL UNIQUE,          -- e.g. 'NMC', 'LFP'
    calendar_factor NUMERIC NOT NULL,               -- multiplier, see mlMaster/dataGenMaster degradation model
    cycle_factor    NUMERIC NOT NULL
);

CREATE TABLE batteries (
    battery_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chemistry_id        UUID NOT NULL REFERENCES battery_chemistries(chemistry_id),
    nominal_capacity_kwh NUMERIC NOT NULL,
    rated_voltage_v      NUMERIC NOT NULL,
    installation_date    DATE NOT NULL
);

CREATE TABLE vehicles (
    vehicle_id          TEXT PRIMARY KEY,           -- e.g. 'EV-000038291', generated by data-gen, NOT a UUID (must be human-scannable for search/demo)
    fleet_id            UUID NOT NULL REFERENCES fleets(fleet_id),
    model_id            UUID NOT NULL REFERENCES vehicle_models(model_id),
    battery_id          UUID NOT NULL REFERENCES batteries(battery_id),
    archetype           TEXT NOT NULL CHECK (archetype IN ('HEALTHY','NORMAL','STRESSED','BAD')),
    manufacture_date    DATE NOT NULL,
    initial_odometer_km NUMERIC NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_vehicles_fleet ON vehicles(fleet_id);
CREATE INDEX idx_vehicles_archetype ON vehicles(archetype);

CREATE TABLE simulations (
    simulation_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    seed                BIGINT NOT NULL,
    mode                TEXT NOT NULL CHECK (mode IN ('SHORT_BURST','LONGITUDINAL','DEMO')),
    start_time          TIMESTAMPTZ NOT NULL,        -- simulated epoch start
    duration_days       INT NOT NULL,
    simulation_speed    NUMERIC NOT NULL,             -- e.g. 1440 = 1 sim-day per real-minute
    status              TEXT NOT NULL DEFAULT 'CREATED' CHECK (status IN ('CREATED','RUNNING','PAUSED','COMPLETED','FAILED','RESET')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE simulation_vehicle_state (
    simulation_id       UUID NOT NULL REFERENCES simulations(simulation_id),
    vehicle_id          TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    sim_timestamp        TIMESTAMPTZ NOT NULL,
    soc_pct              NUMERIC NOT NULL,
    usable_capacity_kwh  NUMERIC NOT NULL,
    soh_pct              NUMERIC NOT NULL,
    odometer_km           NUMERIC NOT NULL,
    equivalent_full_cycles NUMERIC NOT NULL DEFAULT 0,
    battery_temp_c        NUMERIC NOT NULL,
    thermal_exposure_hours NUMERIC NOT NULL DEFAULT 0,   -- cumulative hours above thermal threshold
    fast_charge_count      INT NOT NULL DEFAULT 0,
    deep_discharge_count   INT NOT NULL DEFAULT 0,
    PRIMARY KEY (simulation_id, vehicle_id)
);

-- Aggregates: derived from Kafka stream by the ingestion service, NOT independently simulated.
CREATE TABLE agg_5min (
    vehicle_id        TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    window_start       TIMESTAMPTZ NOT NULL,
    window_end         TIMESTAMPTZ NOT NULL,
    avg_soc            NUMERIC, min_soc NUMERIC, max_soc NUMERIC,
    avg_temp_c         NUMERIC, min_temp_c NUMERIC, max_temp_c NUMERIC, temp_variance NUMERIC,
    avg_current_a      NUMERIC, max_current_a NUMERIC,
    avg_voltage_v      NUMERIC,
    charging_duration_s INT DEFAULT 0,
    avg_charging_power_kw NUMERIC, max_charging_power_kw NUMERIC,
    energy_consumed_kwh NUMERIC DEFAULT 0, energy_charged_kwh NUMERIC DEFAULT 0,
    distance_km          NUMERIC DEFAULT 0,
    event_count           INT NOT NULL DEFAULT 0,
    PRIMARY KEY (vehicle_id, window_start)
);

CREATE TABLE agg_hourly (
    vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    hour_start TIMESTAMPTZ NOT NULL,
    energy_consumed_kwh NUMERIC DEFAULT 0, energy_charged_kwh NUMERIC DEFAULT 0,
    distance_km NUMERIC DEFAULT 0,
    charging_duration_s INT DEFAULT 0,
    high_temp_duration_s INT DEFAULT 0,     -- seconds spent above thermal threshold this hour
    max_temp_c NUMERIC,
    avg_soc NUMERIC, soc_range NUMERIC,
    charging_sessions INT DEFAULT 0,
    PRIMARY KEY (vehicle_id, hour_start)
);

CREATE TABLE agg_daily (
    vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    date DATE NOT NULL,
    distance_km NUMERIC DEFAULT 0,
    energy_consumed_kwh NUMERIC DEFAULT 0, energy_charged_kwh NUMERIC DEFAULT 0,
    charging_sessions INT DEFAULT 0,
    fast_charge_sessions INT DEFAULT 0,       -- charging_power_kw > 0.6 * battery max -> fast charge
    deep_discharge_events INT DEFAULT 0,      -- soc dropped below 20%
    high_temp_hours NUMERIC DEFAULT 0,        -- hours above thermal threshold (35C default)
    max_temp_c NUMERIC,
    equivalent_full_cycles NUMERIC DEFAULT 0,
    soh_pct NUMERIC,                          -- snapshot of SOH at end of this day, copied from simulation_vehicle_state
    PRIMARY KEY (vehicle_id, date)
);
CREATE INDEX idx_agg_daily_vehicle_date ON agg_daily(vehicle_id, date);

CREATE TABLE charging_sessions (
    session_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id             TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    started_at              TIMESTAMPTZ NOT NULL,
    ended_at                TIMESTAMPTZ,
    start_soc                NUMERIC NOT NULL,
    end_soc                  NUMERIC,
    energy_delivered_kwh     NUMERIC,
    max_charging_power_kw    NUMERIC,
    charging_type             TEXT CHECK (charging_type IN ('NORMAL','FAST'))
);
CREATE INDEX idx_charging_sessions_vehicle ON charging_sessions(vehicle_id, started_at);

CREATE TABLE battery_predictions (
    prediction_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id                     TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    predicted_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    current_soh                     NUMERIC NOT NULL,
    predicted_degradation_30d_pct   NUMERIC NOT NULL,
    predicted_soh_30d               NUMERIC NOT NULL,
    risk_level                       TEXT NOT NULL CHECK (risk_level IN ('LOW','MEDIUM','HIGH')),
    horizon_days                     INT NOT NULL DEFAULT 30,
    model_version                    TEXT NOT NULL
);
CREATE INDEX idx_predictions_vehicle ON battery_predictions(vehicle_id, predicted_at DESC);

CREATE TABLE alerts (
    alert_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vehicle_id      TEXT NOT NULL REFERENCES vehicles(vehicle_id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    alert_type        TEXT NOT NULL,           -- e.g. 'HIGH_TEMP', 'DEEP_DISCHARGE', 'HIGH_RISK_PREDICTION'
    severity           TEXT NOT NULL CHECK (severity IN ('INFO','WARNING','CRITICAL')),
    status              TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
    source               TEXT NOT NULL CHECK (source IN ('REALTIME_RULE','ML_PREDICTION')),
    prediction_id         UUID REFERENCES battery_predictions(prediction_id)
);
CREATE INDEX idx_alerts_vehicle ON alerts(vehicle_id, created_at DESC);
CREATE INDEX idx_alerts_status ON alerts(status);

-- Minimal auth: single-table user store, no external IdP.
CREATE TABLE users (
    user_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    password_hash    TEXT NOT NULL,
    role              TEXT NOT NULL CHECK (role IN ('ADMIN','FLEET_MANAGER','VIEWER')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Notes:
- `vehicle_id` is a human-readable string (`EV-######`), not a UUID — required for the dashboard's vehicle
  search to be demoable ("search EV-000038291").
- `agg_5min` / `agg_hourly` / `agg_daily` are written ONLY by the ingestion service. ML and API only read them.
- `simulation_vehicle_state` is written ONLY by data-gen (it owns ground-truth SOH/SOC). Everyone else treats
  it as read-only ground truth, used by ML to build training labels (see mlMaster.md).

---

## 4. MongoDB — raw telemetry document (collection: `raw_telemetry`)

```json
{
  "event_id": "uuid-string",
  "vehicle_id": "EV-000038291",
  "simulation_id": "uuid-string",
  "timestamp": "2026-09-28T10:21:31.120Z",
  "sequence_no": 839201,
  "battery": {
    "soc_pct": 72.4,
    "voltage_v": 391.8,
    "current_a": 48.2,
    "temperature_c": 34.7
  },
  "charging": {
    "is_charging": true,
    "charging_power_kw": 42.5
  },
  "vehicle": {
    "speed_kmh": 52.4,
    "odometer_km": 48231.6
  },
  "environment": {
    "ambient_temperature_c": 31.2
  },
  "ingested_at": "2026-09-28T10:21:31.400Z"
}
```

Indexes: compound `(vehicle_id, timestamp)` (primary access pattern), single `event_id` unique index (dedup
enforcement at the DB layer as a second line of defense after the ingestion service's own dedup — see
ingestionMaster.md).

Retention: keep it simple — a scheduled deletion job (or none, given hackathon dataset size, this is
survivable) removing documents older than `RAW_RETENTION_DAYS` (env var, default 14). Documented as a
simplification of the "hot/warm/cold" requirement; declare in the Solution Doc.

---

## 5. Kafka — event contract

- **Topic:** `telemetry.raw` (single topic, single partition scheme — do not create multiple topics per OEM,
  out of scope)
- **Partitions:** 12 (enough to demonstrate partitioning without over-engineering for local Docker)
- **Partition key:** `vehicle_id` (ensures all events for one vehicle are ordered on one partition —
  required for correct windowed aggregation and for the "avoid hotspots" requirement; 100K vehicles over 12
  partitions is evenly distributable by hash)
- **Value format:** JSON (not Avro/Protobuf — explicitly cut for time; document this as a declared
  simplification, schema validation is done in application code instead of a schema registry)
- **Message value schema** (produced by data-gen, consumed by ingestion):

```json
{
  "event_id": "uuid-string",
  "vehicle_id": "EV-000038291",
  "simulation_id": "uuid-string",
  "timestamp": "2026-09-28T10:21:31.120Z",
  "sequence_no": 839201,
  "soc_pct": 72.4,
  "voltage_v": 391.8,
  "current_a": 48.2,
  "temperature_c": 34.7,
  "is_charging": true,
  "charging_power_kw": 42.5,
  "speed_kmh": 52.4,
  "odometer_km": 48231.6,
  "ambient_temperature_c": 31.2,
  "contract_version": "1.0.0"
}
```

Field ranges (ingestion MUST reject/quarantine events outside these — see ingestionMaster.md §validation):

| Field | Type | Range | Notes |
|---|---|---|---|
| soc_pct | float | 0–100 | |
| voltage_v | float | 250–450 | typical EV pack range |
| current_a | float | -400–400 | positive = discharge, negative = charge (SOT convention) |
| temperature_c | float | -20–80 | battery pack temp |
| charging_power_kw | float | 0–350 | 0 when `is_charging` false |
| speed_kmh | float | 0–220 | |
| odometer_km | float | ≥ 0, monotonically non-decreasing per vehicle | |
| ambient_temperature_c | float | -30–55 | |

**Idempotency / dedup contract:** `event_id` is the dedup key. Producer (data-gen) intentionally re-sends a
small percentage of events unchanged (same `event_id`) in `SHORT_BURST` mode to simulate at-least-once
delivery, plus deliberately emits some events out of order (their `timestamp`/`sequence_no` older than the
last seen). Consumer (ingestion) MUST de-duplicate by `event_id` (see ingestionMaster.md for exact mechanism)
and MUST tolerate out-of-order `sequence_no` without crashing (aggregation buckets by event `timestamp`, not
arrival order).

---

## 6. Redis key namespace

| Key pattern | Type | TTL | Written by | Read by |
|---|---|---|---|---|
| `vehicle:state:<vehicle_id>` | HASH (latest soc, temp, soh, is_charging, odometer, last_updated) | none (overwritten each event) | ingestion | api |
| `vehicle:alert_flags:<vehicle_id>` | SET of active alert types | none | ingestion (real-time rules) | api |
| `sim:status:<simulation_id>` | STRING (JSON status blob) | none | data-gen | api, ml |
| `ratelimit:<user_id_or_ip>:<endpoint>` | STRING (counter) | 60s sliding window | api | api |
| `cache:fleet_summary` | STRING (JSON) | 10s | api | api |

---

## 7. Environment variables (`.env.example` — every service reads the subset it needs)

```
CONTRACT_VERSION=1.0.0

# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
KAFKA_TOPIC=telemetry.raw

# Postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=cvi
POSTGRES_USER=cvi_app
POSTGRES_PASSWORD=changeme_local_only
# Read-only role, used ONLY by the SQL sandbox feature in the api service
POSTGRES_READONLY_USER=cvi_readonly
POSTGRES_READONLY_PASSWORD=changeme_local_only_readonly

# Mongo
MONGO_HOST=mongo
MONGO_PORT=27017
MONGO_DB=cvi_telemetry

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Service URLs (for inter-service HTTP calls)
DATA_GEN_URL=http://data-gen:8001
INGESTION_URL=http://ingestion:8002
ML_URL=http://ml:8003
API_URL=http://api:8000

# Auth
JWT_SECRET=changeme_local_only_jwt_secret
JWT_EXPIRY_MINUTES=120

# Misc
RAW_RETENTION_DAYS=14
```

`.env` (the real file with actual secrets) is gitignored. `.env.example` above is committed with placeholder
values — this satisfies the "no secrets committed" repository hygiene checklist item.

---

## 8. HTTP conventions (every service's HTTP API follows this)

- All JSON, all endpoints under `/api/v1/...`.
- Standard error shape:
```json
{ "error": { "code": "VALIDATION_ERROR", "message": "human readable", "details": {} } }
```
- Every service exposes `GET /health` returning `{"status": "ok", "contract_version": "1.0.0"}`.
- Pagination (on any list endpoint): keyset-based, query params `?limit=50&after=<cursor>`, response includes
  `"next_cursor"` field (never offset-based — offset pagination degrades on large tables, and using keyset is
  an explicit grading callout in the problem statement).
- Auth: `Authorization: Bearer <JWT>` header on every `api` service endpoint except `/health` and
  `/api/v1/auth/login`. JWT claims: `{"sub": "<user_id>", "role": "ADMIN|FLEET_MANAGER|VIEWER", "exp": ...}`.

---

## 9. Degradation ground-truth model (locked constants — data-gen owns computing this, everyone else only
reads the resulting `soh_pct` / `agg_daily` values, nobody else re-derives it)

This section exists in the contract (not just dataGenMaster.md) because the ML module's whole training
approach depends on knowing that SOH is a **simulator-controlled ground truth**, not something ML infers from
scratch.

```
SOH(t) = 100 - cumulative_degradation_pct(t)

daily_degradation_pct =
      calendar_term
    + cycle_term
    + archetype_multiplier * (thermal_term + fastcharge_term + deepdischarge_term)

calendar_term       = k_cal * chemistry.calendar_factor                          # small constant per day
cycle_term           = k_cyc * chemistry.cycle_factor * equivalent_full_cycles_today
thermal_term         = k_therm * hours_above_35C_today
fastcharge_term      = k_fast * fast_charge_sessions_today
deepdischarge_term   = k_deep * deep_discharge_events_today

archetype_multiplier: HEALTHY=0.6, NORMAL=1.0, STRESSED=1.5, BAD=2.2

daily_degradation_pct is clipped to [0, 0.05] before being added to cumulative_degradation_pct
SOH is floored at 60.0 (a battery this degraded would already be flagged/replaced in reality)

Default constants (calibrate in data-gen if the resulting SOH curves look unrealistic over 90 days):
k_cal = 0.0008
k_cyc = 0.015
k_therm = 0.004
k_fast = 0.01
k_deep = 0.008
```

Full detail and exact per-tick derivation lives in `dataGenMaster.md`. This section is the minimum any other
module needs to know: SOH is deterministic given the day's stress inputs and archetype, computed once per
simulated day by data-gen, and stored in `simulation_vehicle_state` / copied into `agg_daily.soh_pct`.
