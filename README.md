# Connected Vehicle Intelligence — EV Battery Health & Charging Intelligence

A connected-vehicle data platform with one deep intelligence use case: given a vehicle's recent telemetry
history, predict how much its battery will degrade over the next 30 days, and surface that as actionable
fleet risk. Built for the Motorq/Talenciaglobal Connected Vehicle Intelligence Hackathon.

Full architecture and design rationale: [`docs/CONTRACT.md`](docs/CONTRACT.md) (shared schema/contract),
[`docs/ADRs.md`](docs/ADRs.md) (key decisions), [`docs/STRIDE_threat_model.md`](docs/STRIDE_threat_model.md),
[`docs/DECLARATIONS.md`](docs/DECLARATIONS.md), and the four module docs (`docs/*Master.md`).

## The problem, in one line

Given a connected vehicle's recent driving/charging history, how much battery degradation is expected over
the next 30 days — and which vehicles in a fleet need attention now, not after they've already failed.

## Architecture (high level)

Five services, one Docker Compose stack:

```
data-gen  --(Kafka: telemetry.raw)-->  ingestion  --(Postgres + Mongo + Redis)-->  ml  -->  battery_predictions
                                                                                    |
                                                                                    v
                                                                            dashboard (api + frontend)
```

- **data-gen** — simulates a persistent fleet of virtual EVs (deterministic, seeded), produces realistic
  telemetry to Kafka. Three modes: `SHORT_BURST` (throughput proof, full fleet, 1Hz), `LONGITUDINAL`
  (accelerated multi-week history for ML training), `DEMO` (scripted sequence of both).
- **ingestion** — consumes Kafka, deduplicates (Bloom filter + Mongo unique-index backstop), validates,
  writes raw telemetry to MongoDB, latest state to Redis, derives 5-min/hourly/daily aggregates into
  PostgreSQL via a bounded in-memory windowed aggregator (no Flink/Spark — a deliberate scope decision, see
  ADR-3).
- **ml** — builds leakage-safe training features/labels from Postgres aggregates, trains a
  `GradientBoostingRegressor` against a naive baseline, derives LOW/MEDIUM/HIGH risk from the prediction.
- **dashboard** (`api` + `frontend`) — JWT-authenticated REST + WebSocket API, rate-limited, with a guarded
  read-only SQL sandbox; React frontend (Fleet Overview, Vehicle Search/Detail, Alerts, Risk & ML,
  Simulation Control, Observability, SQL Sandbox).

Polyglot storage rationale (PostgreSQL relational core / MongoDB raw telemetry / Redis latest-state) is
ADR-1. Why no vector DB: ADR-2.

## Quick start (one command)

```bash
cp .env.example .env   # placeholder values are fine for local-only use
docker compose up --build
```

Then, once running:
```bash
curl -X POST http://localhost:8001/api/v1/simulation -H "Content-Type: application/json" -d '{"seed":1,"mode":"LONGITUDINAL","vehicle_count":200,"active_vehicle_count":200,"duration_days":90,"simulation_speed":2880,"start_time":"2026-06-01T00:00:00Z","archetype_distribution":{"HEALTHY":0.35,"NORMAL":0.45,"STRESSED":0.15,"BAD":0.05},"burst":{"enabled":false,"duration_seconds":60,"target_events_per_sec":1000},"fault_injection":{"duplicate_rate":0.01,"out_of_order_rate":0.02,"malformed_rate":0.005}}'
# copy the returned simulation_id, then:
curl -X POST http://localhost:8001/api/v1/simulation/<id>/start
```

Wait for it to complete (watch `GET /api/v1/simulation/<id>` — includes a live `progress_pct`/`eta_seconds`),
then train and predict:
```bash
curl -X POST http://localhost:8003/api/v1/train -H "Content-Type: application/json" -d '{"simulation_id":"<id>","horizon_days":30}'
curl -X POST http://localhost:8003/api/v1/predict -H "Content-Type: application/json" -d '{"simulation_id":"<id>"}'
```

Open the dashboard: **http://localhost:3000**. Demo accounts (auto-seeded on `api` boot):

| Email | Password | Role |
|---|---|---|
| admin@cvi.local | admin123 | ADMIN |
| manager@cvi.local | manager123 | FLEET_MANAGER |
| viewer@cvi.local | viewer123 | VIEWER |

## Environment variables

See [`.env.example`](.env.example) for the full list with defaults — every value has a safe local-dev
default, no real secrets required to run locally. Notable ones: `POSTGRES_*` / `MONGO_*` / `REDIS_*`
connection settings, `JWT_SECRET` / `JWT_EXPIRY_MINUTES`, `RATE_LIMIT_PER_MINUTE` (raise this temporarily for
load testing, see `infra/k6/burst_load_test.js`'s header comment), `SQL_SANDBOX_ENABLED`.

## Running tests

Each service's test suite lives in its own `tests/` folder but isn't baked into its runtime Docker image (kept
consistent with the CI workflow, which installs dependencies + pytest directly rather than shipping test code
in production images). To run them against the live stack:

```bash
docker compose cp services/<service>/tests <service>:/app/tests
docker compose exec <service> pytest tests/ -v
```

for `<service>` in `data-gen`, `ingestion`, `ml`, and `dashboard/api` → container name `api`. All four suites
pass (90 tests total: data-gen 8, ingestion 24, ml 11, api 47). See `test-evidence/` for load test and chaos
demo results.

Load test: `infra/k6/burst_load_test.js` (see header comment for exact invocation — needs a temporarily
raised rate limit). Chaos/resilience demo: `test-evidence/chaos_demo.ps1` (PowerShell, fully automated,
generates a timestamped report). 100K-vehicle throughput test runbook:
`test-evidence/SHORT_BURST_100k_throughput_test.md`.

## Known issues / declared limitations

- **No Flink/Spark** — aggregation is a plain in-process consumer with bounded-batch flushing. In-flight
  (not-yet-flushed) state is lost if the ingestion process crashes between flushes; fully recoverable via
  Kafka replay, just not instant. (ADR-3)
- **Minimal auth** — JWT + RBAC + rate limiting, not full OAuth2/OIDC/mTLS/vault. Upgrade path is clean (see
  ADR-5); see `docs/STRIDE_threat_model.md` for the full declared gap list.
- **No vector database** — not needed for this MVP's core decision; see ADR-2.
- **Single-tenant** — no per-tenant row-level isolation in this MVP.
- **`build_or_load_fleet()` can't grow an existing fleet** — requesting a larger `vehicle_count` than what's
  already persisted collides on vehicle IDs; the fleet tables must be truncated first (documented in
  `docs/dataGenMaster.md`).
- **Docker's `unless-stopped` restart policy does not auto-recover from an externally-issued `docker kill`**
  (this is correct, documented Docker behavior — an external kill is treated as a deliberate operator action,
  not a crash) — it does correctly auto-recover from an internal process crash, verified in the chaos demo.
