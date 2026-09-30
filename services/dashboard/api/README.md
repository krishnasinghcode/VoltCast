# api — CVI Platform REST API (Part D.1)

FastAPI service on port **8000**. The only service real users talk to: owns auth (JWT + RBAC), reads
Postgres/Redis, and proxies simulation control (data-gen :8001) and train/predict (ML :8003) so the frontend has
a single backend. Built to `docs/dashboardMaster.md` + `docs/CONTRACT.md` v1.0.0.

## Run

```bash
# whole stack (from repo root)
docker compose up -d --build api frontend      # api seeds demo users on start-up

# local, without docker (needs Postgres + Redis reachable)
cd services/dashboard/api
pip install -r requirements.txt
POSTGRES_HOST=localhost REDIS_HOST=localhost DATA_GEN_URL=http://localhost:8001 \
  INGESTION_URL=http://localhost:8002 ML_URL=http://localhost:8003 \
  python src/seed_users.py && uvicorn src.main:app --port 8000 --reload
```

Interactive docs: http://localhost:8000/docs

Demo accounts (from `seed_users.py`, passwords overridable via `SEED_*_PASSWORD`):

| Email | Password | Role |
|---|---|---|
| admin@cvi.local | admin123 | ADMIN |
| manager@cvi.local | manager123 | FLEET_MANAGER |
| viewer@cvi.local | viewer123 | VIEWER |

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"viewer@cvi.local","password":"viewer123"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -H "Authorization: Bearer $TOKEN" "localhost:8000/api/v1/vehicles?limit=10&search=EV-0000001"
```

## Endpoints (all under `/api/v1`, Bearer JWT unless noted)

| Method | Path | Roles | Notes |
|---|---|---|---|
| GET | `/health` (also `/api/v1/health`) | public | `{"status","contract_version"}` |
| POST | `/auth/login` | public | `{email,password}` → `{access_token, role, expires_in, …}` |
| GET | `/auth/me` | any | |
| POST | `/auth/register` | ADMIN | no public sign-up |
| GET | `/fleets` | any | keyset (`limit`, `after`) + vehicle counts |
| GET | `/fleets/summary` | any | all fleets; Redis `cache:fleet_summary` 10 s |
| GET | `/fleets/{fleet_id}/summary` | any | vehicle count, avg/min SOH, risk + archetype breakdown, open alerts |
| GET | `/vehicles?search=&fleet_id=&archetype=&risk_level=&limit=&after=` | any | substring match on `vehicle_id`, keyset on `vehicle_id` |
| GET | `/vehicles/{id}` | any | static info + current state (Redis → Postgres fallback) + latest prediction + alert flags |
| GET | `/vehicles/{id}/history?resolution=daily\|hourly\|5min&from=&to=&limit=&after=` | any | newest first, keyset on the time column |
| GET | `/vehicles/{id}/predictions?limit=` | any | also at `/predictions/{id}` |
| GET | `/vehicles/{id}/charging-sessions` | any | keyset |
| GET | `/alerts?status=&severity=&source=&alert_type=&vehicle_id=&fleet_id=&limit=&after=` | any | keyset on `(created_at, alert_id)` desc |
| GET | `/alerts/{id}` | any | |
| POST | `/alerts/{id}/acknowledge` | FLEET_MANAGER, ADMIN | 409 if not OPEN |
| GET | `/risk/high`, `/risk/medium`, `/risk/low` | any | latest prediction per vehicle, worst degradation first, keyset |
| POST | `/simulation` | FLEET_MANAGER, ADMIN | proxy → data-gen |
| POST | `/simulation/{id}/start\|pause\|resume\|reset` | FLEET_MANAGER, ADMIN | proxy |
| GET | `/simulation/{id}`, `/simulations` | any | proxy |
| POST | `/ml/train` `{simulation_id, horizon_days}` | FLEET_MANAGER, ADMIN | proxy → ML (600 s timeout) |
| GET | `/ml/train/status` | any | proxy |
| POST | `/ml/predict`, `/ml/predict/{vehicle_id}` | FLEET_MANAGER, ADMIN | proxy |
| GET | `/observability` | any | ingestion `/metrics` + own request metrics + fleet counts + peer health/contract check |
| GET | `/sql-sandbox` | any | example queries, limits |
| POST | `/sql-sandbox` `{query}` | FLEET_MANAGER, ADMIN | see guardrails below |
| WS | `/ws/live?token=<jwt>` | any | pushes every 2 s: fleet summary, 10 latest alerts, subscribed vehicles' Redis state |

Every error uses the contract shape `{"error":{"code","message","details"}}` — including 401/403/404/422/429 and
upstream failures (`UPSTREAM_UNAVAILABLE` 502, `UPSTREAM_TIMEOUT` 504). Pagination is keyset only: responses
carry `next_cursor` (opaque base64 JSON), pass it back as `after`.

## Design notes

- **Auth**: HS256 JWT (`sub`, `role`, `exp`), bcrypt via passlib. `require_role(*roles)` on every route.
  VIEWER reads everything; FLEET_MANAGER/ADMIN may also acknowledge alerts, run simulations/ML and the sandbox.
  Declared scope cut: no refresh tokens / OAuth2 / password reset.
- **Rate limiting**: `ratelimit:<user_id|ip>:<route template>` in Redis, INCR + EXPIRE 60, default 120/min
  (`RATE_LIMIT_PER_MINUTE`). `/auth/login` is keyed by client IP to throttle password guessing. The limiter
  fails open if Redis is down. `X-Forwarded-For` is deliberately not trusted (spoofable).
- **SQL portability**: router SQL runs on Postgres and SQLite (no `::`, `ILIKE`, `DISTINCT ON`), so tests run
  against an in-memory fixture DB in CI. "Latest row per vehicle" uses correlated `ORDER BY … LIMIT 1`
  subqueries served by the `(vehicle_id, time)` indexes, or `ROW_NUMBER()` for fleet-wide aggregates (cached).
- **Live updates** are polling-based (one background task, 2 s), not pub/sub — honest trade-off for the time
  budget. The JWT goes in the query string because browser `WebSocket` can't send headers; bad token →
  close code 4401.
- **Workers / observability**: the image runs `API_WORKERS` (default 4) uvicorn processes — one Python process
  tops out around 135 req/s. Each worker keeps an in-memory ring buffer of its last 2000 request latencies and
  counters, publishes it to Redis (`metrics:api:<host>:<pid>`, 15 s TTL, removed on shutdown) every 2 s, and
  `/observability` merges all live workers. Rate limits are Redis-backed, so they're shared across workers too.

## Load test (k6, `infra/k6/burst_load_test.js`)

0→200 VUs over 30 s, hold 60 s, mixed reads (35 % vehicle detail, 25 % search, 20 % fleet summary,
20 % alerts), local Docker Desktop (8 CPUs), 500-vehicle dataset, rate limit raised for the run:

| Scenario | req/s | p95 | p99 | errors | thresholds (p95<200, p99<500) |
|---|---|---|---|---|---|
| 1 s think time (default — models people using a dashboard) | 153 | 82 ms | 187 ms | 0 % | pass |
| 0 think time (closed-loop stress), 4 workers | 389 | 651 ms | 754 ms | 0 % | fail |
| 0 think time, 1 worker (before multi-worker) | 135 | 1.77 s | 2.06 s | 0 % | fail |

With zero think time latency is queueing (≈ 200 VUs ÷ throughput), so the stress run measures capacity, not
per-request cost. Scale-out path: more workers/replicas behind nginx, or `redis.asyncio` + async DB driver.

### SQL sandbox guardrails (all required by spec, all implemented)

1. Single `SELECT`/`WITH` statement only; any `;` other than one trailing is rejected. A small scanner strips
   comments/strings first and rejects data-modifying keywords (`INSERT`, `DELETE`, `INTO`, …) even inside CTEs.
2. Executed as `POSTGRES_READONLY_USER` — SELECT-only grants, **no access to `users`**, role-level
   `statement_timeout` and `default_transaction_read_only`.
3. `SET LOCAL statement_timeout = '3s'` in the same transaction (+ `SET TRANSACTION READ ONLY`).
4. Queries without `LIMIT` are wrapped: `SELECT * FROM (<q>) AS sandbox_q LIMIT 500`; results hard-capped at 500.
5. Transaction always rolled back.

Verified against the live database: `SELECT * FROM users` → 403, `pg_sleep(10)` → `QUERY_TIMEOUT`,
`set_config('statement_timeout','0')` dodge → still times out, `lo_import` → permission denied.

## Tests

```bash
cd services/dashboard/api && pip install -r requirements.txt && pytest
```

47 tests, no external services needed (SQLite fixture DB + fakeredis, peers pointed at a closed port):
`test_auth.py` (login, bad password, expired/forged tokens, VIEWER → 403 on FLEET_MANAGER endpoints),
`test_rate_limit.py` (N+1th request → 429, per-endpoint buckets, login by IP, fail-open),
`test_vehicle_router.py` (search/pagination/filters/detail/history shapes),
`test_other_routers.py` (alerts, acknowledge, risk lists, fleet summary cache, observability, proxies,
sandbox guardrails, WebSocket auth + pushes).

## Assumptions / deviations from the docs

- **Location**: code lives in `services/dashboard/api` and `services/dashboard/frontend` (instead of
  `services/api` + `frontend/` in CONTRACT.md §0), as requested. `docker-compose.yml` and CI point there.
- **Read-only role**: dashboardMaster §7 puts `CREATE ROLE cvi_readonly` in `infra/postgres/init.sql`; that file
  doesn't contain it and isn't this module's to edit, so `seed_users.py` creates/updates it idempotently (the
  docker Postgres app user is a superuser). If it can't, the sandbox returns 503 `SANDBOX_UNAVAILABLE`.
- **Fleet summary cache**: all-fleets summary uses the exact contract key `cache:fleet_summary`; per-fleet
  summaries use `cache:fleet_summary:<fleet_id>` (a single STRING can't hold several independently-expiring
  summaries).
- **SOH in live state**: ingestion's `vehicle:state:*` hash has no SOH (it's data-gen's ground truth), so the
  API always takes SOH from `simulation_vehicle_state` (fallback `agg_daily.soh_pct`).
- **Extra endpoints** beyond the doc table: `/fleets/summary`, `/risk/medium`, `/simulation/{id}/resume`,
  `/simulations`, `/ml/train/status`, `/ml/predict/{vehicle_id}`, `/vehicles/{id}/charging-sessions`,
  `/alerts/{id}`, `/auth/me`.
