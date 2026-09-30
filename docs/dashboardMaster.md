# dashboardMaster.md — API Backend + Web Dashboard (Part D)

**Read `CONTRACT.md` first.** You will NOT have access to data-gen, ingestion, or ML code — only their
documented HTTP endpoints and Postgres/Redis shapes. This doc covers TWO deliverables built together:
`services/api/` (backend) and `frontend/` (React app). Build both; they are one logical part.

---

## PART D.1 — API Backend (`services/api/`)

### 1. Purpose

The only service exposed to real users. Owns auth. Reads Postgres/Redis (never writes aggregates — those
belong to ingestion; never writes predictions — those belong to ML). Proxies simulation control to data-gen
and train/predict triggers to ML so the frontend only ever talks to this one backend (simplifies CORS, auth,
and rate limiting — single choke point).

### 2. Inner architecture

```
services/api/
├── Dockerfile
├── requirements.txt
├── src/
│   ├── main.py                 # FastAPI app, mounts all routers, CORS, rate-limit middleware
│   ├── config.py
│   ├── db.py                    # Postgres (SQLAlchemy), Redis client
│   ├── auth.py                   # JWT issue/verify, password hashing (passlib/bcrypt), role dependency
│   ├── rate_limit.py               # Redis-backed sliding window, per CONTRACT.md §6 key pattern
│   ├── routers/
│   │   ├── auth_router.py          # /api/v1/auth/login, /register (admin-only, seed script preferred over open register)
│   │   ├── fleet_router.py          # /api/v1/fleets, /api/v1/fleets/{id}/summary
│   │   ├── vehicle_router.py         # /api/v1/vehicles (search/list), /api/v1/vehicles/{id}, /{id}/history
│   │   ├── alert_router.py            # /api/v1/alerts (list, filter, acknowledge)
│   │   ├── prediction_router.py        # /api/v1/predictions/{vehicle_id}, /api/v1/risk/high, /api/v1/risk/low
│   │   ├── simulation_router.py         # thin proxy to data-gen control API
│   │   ├── ml_router.py                  # thin proxy to ML /train, /predict
│   │   ├── observability_router.py        # /api/v1/observability -> pulls ingestion /metrics + own request metrics
│   │   ├── sql_sandbox_router.py           # OPTIONAL — see §7, only build if time remains, guarded per spec below
│   │   └── ws_router.py                     # /ws/live -> WebSocket, pushes Redis latest-state + new alerts
│   └── seed_users.py                         # script: creates one ADMIN, one FLEET_MANAGER, one VIEWER user
├── tests/
│   ├── test_auth.py
│   ├── test_rate_limit.py
│   └── test_vehicle_router.py
└── README.md
```

requirements.txt (minimum):
```
fastapi
uvicorn[standard]
sqlalchemy
psycopg2-binary
redis
python-jose[cryptography]
passlib[bcrypt]
httpx
websockets
```

### 3. Auth (`auth.py`) — minimal, workable, nothing more

- `POST /api/v1/auth/login` — body `{"email","password"}`, verifies against `users` table (bcrypt hash),
  returns `{"access_token": "<jwt>", "role": "..."}`. JWT payload per CONTRACT.md §8: `sub`, `role`, `exp`
  (`JWT_EXPIRY_MINUTES` from env).
- No self-registration endpoint exposed publicly — `seed_users.py` creates the three demo accounts on first
  boot (idempotent — check existence before insert). This is the deliberately-minimal auth decision: no
  OAuth2/OIDC/refresh tokens/password reset flow. State this explicitly as a declared scope cut in the
  Solution Document's Security section, alongside what a production version would add.
- `require_role(*roles)` FastAPI dependency — every router endpoint declares which roles can call it.
  Suggested mapping: VIEWER can read everything; FLEET_MANAGER can also acknowledge alerts and trigger
  simulation/ML actions; ADMIN can do everything FLEET_MANAGER can (no extra endpoints needed for MVP, but the
  distinction exists and is checked — this demonstrates RBAC without needing a complex permission matrix).
- Passwords: bcrypt via `passlib`. Never log or return password hashes.

### 4. Rate limiting (`rate_limit.py`)

Redis-backed fixed-window counter, per CONTRACT.md §6 key `ratelimit:<user_id>:<endpoint>`: `INCR` the key,
`EXPIRE 60` on first increment, reject with `429` (contract error shape, `code: "RATE_LIMITED"`) if count
exceeds a configurable limit (default 120 requests/minute per user per endpoint). Applied as FastAPI
middleware, keyed off the authenticated user (or client IP for the unauthenticated `/auth/login` endpoint,
to also rate-limit login attempts — a real security benefit, not just a checklist item).

### 5. Endpoints (all under `/api/v1/`, all require `Authorization: Bearer <jwt>` unless noted)

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | no auth required |
| GET | `/fleets` | list fleets, keyset paginated |
| GET | `/fleets/{fleet_id}/summary` | vehicle count, avg SOH, risk breakdown (LOW/MED/HIGH counts) — cache in Redis `cache:fleet_summary` per CONTRACT.md §6, 10s TTL |
| GET | `/vehicles?search=&fleet_id=&archetype=&risk_level=&limit=&after=` | keyset-paginated search — this backs the "search about a vehicle/fleet" feature you asked for; `search` matches `vehicle_id` prefix/substring |
| GET | `/vehicles/{vehicle_id}` | current state (Redis latest-state hash, fall back to Postgres `simulation_vehicle_state` if Redis miss) + static vehicle/battery info |
| GET | `/vehicles/{vehicle_id}/history?resolution=daily&from=&to=` | reads `agg_daily`/`agg_hourly`/`agg_5min` depending on `resolution` param |
| GET | `/vehicles/{vehicle_id}/predictions` | latest N rows from `battery_predictions` |
| GET | `/alerts?status=&severity=&vehicle_id=&limit=&after=` | keyset-paginated |
| POST | `/alerts/{alert_id}/acknowledge` | FLEET_MANAGER+ only |
| GET | `/risk/high` | vehicles with latest prediction `risk_level='HIGH'` — backs your "high risk vehicles" button |
| GET | `/risk/low` | same for LOW — backs "low risk vehicles" button |
| POST | `/simulation` / `/simulation/{id}/start` / `/pause` / `/reset` | thin proxy (httpx call) to data-gen's control API, FLEET_MANAGER+ only |
| GET | `/simulation/{id}` | proxy to data-gen |
| POST | `/ml/train` / `/ml/predict` | proxy to ML service, FLEET_MANAGER+ only |
| GET | `/observability` | merges ingestion `/metrics` with this service's own request-count/error-rate/latency counters (keep your own counters simple — an in-memory dict incremented per request is enough, no need for a full metrics library) |
| GET/POST | `/sql-sandbox` (OPTIONAL) | see §7 |
| WS | `/ws/live` | see §6 |

Every list endpoint follows CONTRACT.md §8 pagination/error conventions exactly.

### 6. Live updates (`ws_router.py`)

WebSocket at `/ws/live`. On connect (after validating a JWT passed as a query param, since WS can't send
custom headers easily from a browser `WebSocket` object — document this as the practical reason for the
query-param auth exception here, everywhere else uses the header), the server subscribes to a lightweight
internal pub/sub: simplest workable approach — a background task polls Redis `vehicle:state:*` keys for
recently-changed vehicles (e.g. by keeping a small Redis SET of vehicle_ids touched in the last poll interval,
written to by... actually simplest of all: **poll, don't pub/sub.** Every 2 seconds, query Redis for the fleet
summary + latest N alerts, and push a JSON delta to all connected clients. This is not the most elegant
real-time architecture but is trivial to build correctly and is honestly documented as "polling-based live
update, not a push-based pub/sub, given the time budget" — still satisfies "live data update" as a demoable
feature.

### 7. SQL Sandbox — OPTIONAL, build LAST if time remains, full guardrail spec (decision was parked, so this
is written complete and ready but not mandatory for a working submission)

If built:
- `GET /api/v1/sql-sandbox` — returns a short list of example queries (helps the demo).
- `POST /api/v1/sql-sandbox` — body `{"query": "SELECT ..."}`. FLEET_MANAGER+ only.
- **Guardrails, all required, no partial version:**
  1. Reject (400) unless the trimmed, uppercased query starts with `SELECT ` or `WITH ` (for CTEs that
     themselves must resolve to a SELECT) — simple prefix check plus a secondary check that no
     semicolon-separated second statement exists (reject on any `;` that isn't the trailing one).
  2. Execute against `POSTGRES_READONLY_USER` (CONTRACT.md §7) — a genuinely separate DB role with only
     `SELECT` grants, created in `infra/postgres/init.sql` (`CREATE ROLE cvi_readonly LOGIN PASSWORD '...';
     GRANT SELECT ON ALL TABLES IN SCHEMA public TO cvi_readonly;`) — this is the real safety boundary, the
     text-prefix check alone is not sufficient (someone could still write a `SELECT` that calls a
     volume-DoS function, hence points 3-4).
  3. Wrap execution with `SET LOCAL statement_timeout = '3s'` in the same transaction.
  4. Append `LIMIT 500` server-side if the user's query has no `LIMIT` clause (simple regex check).
  5. Run in a transaction that is always rolled back (`ROLLBACK` even on success) — belt-and-suspenders, since
     the role can't write anyway, but costs nothing.
- If any guardrail is skipped, do not ship this feature — a naive version is worse than not having it. It's
  explicitly optional; cutting it entirely is a safe, defensible choice if time is short.

### 8. Observability data this service must expose (feeds the dashboard's Observability page)

`GET /api/v1/observability` response shape:
```json
{
  "ingestion": { "events_consumed_total": 0, "events_deduplicated_total": 0, "events_rejected_total": 0, "consumer_lag": 0 },
  "api": { "requests_total": 0, "errors_total": 0, "avg_latency_ms": 0, "p95_latency_ms": 0 },
  "fleet": { "vehicle_count": 0, "active_alerts": 0 }
}
```
Track `api.*` with a simple in-memory ring buffer of the last N request latencies (no Prometheus/Grafana
needed — the dashboard page itself IS the observability deliverable, satisfying that checklist item cheaply).

---

## PART D.2 — Frontend (`frontend/`)

### 9. Inner architecture

```
frontend/
├── Dockerfile                # multi-stage: vite build -> nginx serve
├── package.json
├── vite.config.ts
├── src/
│   ├── main.tsx
│   ├── App.tsx                 # router
│   ├── api/client.ts             # fetch wrapper, attaches JWT, handles 401 -> redirect to login
│   ├── auth/LoginPage.tsx
│   ├── pages/
│   │   ├── FleetOverview.tsx       # fleet summary cards, risk breakdown chart
│   │   ├── VehicleSearch.tsx        # search bar -> table -> click through to detail
│   │   ├── VehicleDetail.tsx         # current state, history chart (soc/temp/soh over time), predictions, alerts for this vehicle
│   │   ├── AlertsPage.tsx             # filterable alert list, acknowledge button
│   │   ├── RiskPage.tsx                # "High Risk Vehicles" / "Low Risk Vehicles" buttons -> lists, "Run Prediction" / "Retrain Model" buttons -> POST to ml proxy
│   │   ├── ObservabilityPage.tsx        # numbers/charts from /api/v1/observability, auto-refresh every few seconds
│   │   ├── SimulationControlPage.tsx     # start/pause/reset simulation, shows current sim status
│   │   └── SqlSandboxPage.tsx (OPTIONAL)  # textarea + run button + result table, only if backend built it
│   ├── hooks/useLiveSocket.ts             # connects /ws/live, exposes latest pushed state to components
│   └── components/ (shared table/card/chart components)
└── README.md
```

package.json deps (minimum): `react`, `react-dom`, `react-router-dom`, `recharts` (or `chart.js` +
`react-chartjs-2` — pick one), `vite`, `typescript`.

### 10. Pages ↔ endpoints mapping (so the frontend builder knows exactly what to call)

| Page | Calls |
|---|---|
| Login | `POST /auth/login` |
| Fleet Overview | `GET /fleets`, `GET /fleets/{id}/summary` |
| Vehicle Search | `GET /vehicles?search=...` |
| Vehicle Detail | `GET /vehicles/{id}`, `GET /vehicles/{id}/history`, `GET /vehicles/{id}/predictions`, `GET /alerts?vehicle_id=` |
| Alerts | `GET /alerts`, `POST /alerts/{id}/acknowledge` |
| Risk | `GET /risk/high`, `GET /risk/low`, `POST /ml/train`, `POST /ml/predict` |
| Observability | `GET /observability` (poll every 3-5s) |
| Simulation Control | `POST /simulation`, `POST /simulation/{id}/start`, `/pause`, `/reset`, `GET /simulation/{id}` |
| (optional) SQL Sandbox | `POST /sql-sandbox` |

Live-updating pieces (Fleet Overview's numbers, Vehicle Detail's current state) additionally subscribe via
`useLiveSocket` to `/ws/live` and merge pushed deltas on top of the initial REST fetch — this is what
satisfies "live values change" from your original feature list.

### 11. What this part assumes about the rest of the system

- ML and data-gen's control APIs match §5's proxy targets exactly (CONTRACT.md ports 8001/8003).
- Postgres/Redis schemas from CONTRACT.md §3/§6 already applied and populated by the other three services —
  this backend never creates schema, only queries it.
- If ML hasn't run yet (`battery_predictions` empty), `/risk/high`/`/risk/low` return empty lists, not errors
  — the frontend must handle empty states gracefully (this WILL happen the first time the whole stack is
  brought up together, before anyone has clicked "Run Prediction").

### 12. Dependents

Nothing depends on this part — it's the top of the stack. This part depends on all three others being
reachable at their documented URLs (`.env` `DATA_GEN_URL`/`ML_URL`) and on the Postgres/Redis schema.

### 13. How to run and verify standalone

Backend can be tested against fixture data even before ingestion/ML are live: `services/api/tests/` should
include a script or fixtures inserting a handful of fake rows directly into Postgres/Redis so `vehicle_router`
and `alert_router` are verifiable without the rest of the pipeline running.

```bash
docker compose up postgres redis api
python services/api/src/seed_users.py
curl -X POST localhost:8000/api/v1/auth/login -d '{"email":"admin@cvi.local","password":"..."}'
curl -H "Authorization: Bearer <token>" localhost:8000/api/v1/vehicles?limit=10

cd frontend && npm install && npm run dev   # localhost:5173, points at localhost:8000 via .env VITE_API_URL
```

### 14. Tests to include

- `test_auth.py`: login succeeds with seeded user, fails with wrong password, JWT round-trips through
  `require_role` correctly (VIEWER blocked from a FLEET_MANAGER-only endpoint returns 403).
- `test_rate_limit.py`: hammer one endpoint past the limit, assert the N+1th request returns 429.
- `test_vehicle_router.py`: against fixture DB rows, assert search/pagination/detail responses match expected
  shape.

---

## 15. Load testing (`infra/k6/burst_load_test.js`) — satisfies the explicit "k6" tooling requirement

This script targets the **API's read endpoints**, not raw Kafka ingestion (k6 is an HTTP/WS tool; the
100K-events/sec ingest throughput number instead comes from data-gen's `SHORT_BURST` mode measured via
ingestion's `/metrics` consumer lag — document both numbers separately in the Solution Document's NFR table,
don't conflate them).

k6 script does:
1. Logs in once (`POST /auth/login`), reuses the token.
2. Ramps virtual users 0→200 over 30s, holds 200 VUs for 60s, ramps down — hitting a mix of `GET /vehicles`,
   `GET /vehicles/{id}`, `GET /fleets/{id}/summary`, `GET /alerts` (weighted toward the endpoints most likely
   to be hit in a real dashboard session).
3. Thresholds: `http_req_duration{p(95)}<200`, `http_req_duration{p(99)}<500` (matches the NFR table's
   API-latency target directly — a failed threshold is a real, honest signal, not something to hide).

Run: `k6 run infra/k6/burst_load_test.js` (needs k6 installed locally, or run via a `grafana/k6` Docker
image against the running `docker compose` stack). Save the summary output — it's your load-test evidence for
the Solution Document.
