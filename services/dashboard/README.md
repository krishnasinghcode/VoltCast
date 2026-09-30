# dashboard — Part D (API backend + web dashboard)

Built to `docs/dashboardMaster.md` against `docs/CONTRACT.md` v1.0.0.

```
services/dashboard/
├── api/        FastAPI backend, port 8000  → see api/README.md
└── frontend/   React + Vite dashboard, nginx on :80 (host :3000) → see frontend/README.md
```

## Quick start (repo root)

```bash
docker compose up -d --build api frontend
# open http://localhost:3000 and sign in as manager@cvi.local / manager123
```

The api container seeds the three demo users and the SQL-sandbox read-only role on every start (idempotent).
Integration order for the whole platform is in `docs/INTEGRATION.md` §1 — risk pages stay empty until ML has
trained and `/predict` has run at least once (expected, not a bug).

## Load test

```bash
RATE_LIMIT_PER_MINUTE=100000000 docker compose up -d api     # all k6 VUs share one token
docker run --rm -i --network krishna_cvi-net -e API_URL=http://api:8000 grafana/k6 run - < infra/k6/burst_load_test.js
```

Results and the think-time option (`-e THINK_TIME_S=0` for a closed-loop stress run) are in
`api/README.md`. The rate limit must be raised because the script logs in once and every virtual user shares that token;
at the default 120/min per user per endpoint most requests would be 429s.
