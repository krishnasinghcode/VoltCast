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

## Running the frontend in dev mode (hot reload, outside Docker)

Useful for frontend-only iteration without rebuilding the Docker image each time:

```bash
cd services/dashboard/frontend
npm install
npm run dev
```

Opens at `http://localhost:5173` by default (not 3000 — that's the Docker/nginx production build, see
`frontend/.env.example` for `VITE_API_URL` if you need to point it at a non-default API location). The
backend (`api`) must already be running via Docker for this to have anything to talk to. `npm run build`
produces the production bundle nginx serves inside the container; `npm run typecheck` runs TypeScript checks
only, no build.

## Day-to-day operations

```bash
# View logs for one service (add -f to follow live)
docker compose logs ingestion --tail 50

# Stop everything (keeps all data — named volumes persist)
docker compose stop

# Bring it back up later, same data
docker compose up -d

# Full teardown INCLUDING all data — only do this deliberately
docker compose down -v

# Check every container's status (use -a to include stopped/exited ones)
docker compose ps -a

# Reset just the vehicle/fleet/prediction data (keeps infra running) — needed before building a
# different-sized fleet, since a larger vehicle_count collides with an existing smaller one (see Known
# Issues below)
docker exec -it krishna-postgres-1 psql -U cvi_app -d cvi -c "TRUNCATE simulation_vehicle_state, battery_predictions, alerts, charging_sessions, agg_5min, agg_hourly, agg_daily, simulations, vehicles, batteries, battery_chemistries, vehicle_models, fleets CASCADE;"
docker exec -it krishna-mongo-1 mongosh cvi_telemetry --eval "db.raw_telemetry.deleteMany({})"

# Backup / restore the whole Postgres database (useful before any destructive operation, or to move data
# between machines — see test-evidence/SHORT_BURST_100k_throughput_test.md for the full pattern)
docker compose exec postgres pg_dump -U cvi_app -d cvi -Fc -f /tmp/backup.dump
docker compose cp postgres:/tmp/backup.dump ./backups/backup.dump
# ... later, to restore:
docker compose cp ./backups/backup.dump postgres:/tmp/backup.dump
docker compose exec postgres pg_restore -U cvi_app -d cvi --clean --if-exists /tmp/backup.dump
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `failed to connect to the docker API` / any `docker` command errors immediately | Docker Desktop isn't running | Start Docker Desktop, wait for the engine to fully boot (30-60s on a cold start), retry |
| `UNKNOWN_TOPIC_OR_PART` in ingestion logs on first boot | Kafka topic doesn't exist yet — harmless, auto-creates once data-gen produces the first event (12 partitions, per `KAFKA_NUM_PARTITIONS` in `docker-compose.yml`) | Ignore if it's a fresh boot; if it persists after a simulation has started, check `docker compose ps` for kafka's health |
| `curl` in PowerShell doesn't accept `-X`/`-d` the way these docs show | Plain `curl` in PowerShell is aliased to `Invoke-WebRequest`, different syntax | Use `curl.exe` explicitly (not bare `curl`) for every command in this README on Windows |
| `exec: "curl"`/`"kill"`: executable file not found` when running `docker compose exec <service> curl ...` | The slim Python images used here don't include `curl` or a standalone `kill` binary | Hit published ports from the host instead of execing in for `curl`; for `kill`, route through the shell builtin: `docker compose exec <service> sh -c "kill -9 1"` |
| A simulation request with a bigger `vehicle_count` than before fails/crashes | `build_or_load_fleet()` can't grow an existing fleet — new vehicle IDs collide with ones that already exist | Truncate the fleet tables first (see Day-to-day operations above), then create the larger fleet fresh |
| `docker kill <container>` doesn't bring the container back despite `restart: unless-stopped` | Correct, documented Docker behavior — an externally-issued kill/stop is treated as a deliberate operator action, not a crash, so the policy intentionally does not auto-restart it | To test genuine crash-recovery, kill the process *inside* the container instead: `docker compose exec <service> sh -c "kill -9 1"` — this DOES trigger auto-restart (verified, see `test-evidence/chaos_demo.ps1`) |
| A service's tests fail with `No module named pytest` or similar | Test files aren't baked into the runtime Docker image (by design, matches CI) | Use the copy-in pattern from "Running tests" above — don't expect `pytest` to already be inside a freshly-pulled container without that step |

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
- **No frontend tests** — the React app has zero automated tests (no test files, no test script in
  `package.json`); only the four backend services have real test coverage (90 tests total). A declared gap,
  not an oversight.
- **`build_or_load_fleet()` can't grow an existing fleet** — requesting a larger `vehicle_count` than what's
  already persisted collides on vehicle IDs; the fleet tables must be truncated first (documented in
  `docs/dataGenMaster.md`).
- **Docker's `unless-stopped` restart policy does not auto-recover from an externally-issued `docker kill`**
  (this is correct, documented Docker behavior — an external kill is treated as a deliberate operator action,
  not a crash) — it does correctly auto-recover from an internal process crash, verified in the chaos demo.
