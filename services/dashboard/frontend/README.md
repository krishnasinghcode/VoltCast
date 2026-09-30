# frontend — CVI Platform dashboard (Part D.2)

React 18 + Vite + TypeScript + Recharts. Talks only to the API service.

## Run

```bash
# dev server on :5173 — /api and /ws are proxied to http://localhost:8000 (override with API_PROXY_TARGET)
cd services/dashboard/frontend
npm install
npm run dev

# production image: nginx on :80 (compose maps it to :3000) serving the build and reverse-proxying
# /api and /ws to the api container — same origin, so no CORS
docker compose up -d --build frontend     # http://localhost:3000
```

`VITE_API_URL` (build-time) makes the app call the API directly instead of same-origin `/api`; the API allows
CORS from `:5173` and `:3000`.

## Pages

| Route | Page | API calls |
|---|---|---|
| `/login` | Login (demo accounts listed) | `POST /auth/login` |
| `/` | Fleet overview — KPI cards, risk + archetype charts, fleets table, live alert feed | `GET /fleets`, `/fleets/summary`, `/fleets/{id}/summary`, WS |
| `/vehicles` | Vehicle search — debounced ID search + fleet/archetype/risk filters, keyset pager | `GET /vehicles` |
| `/vehicles/:id` | Vehicle detail — live SOC/temp/charging, SOH, 30-day outlook, history chart (daily/hourly/5-min), predictions, alerts, simulator ground truth, "Predict now" | `GET /vehicles/{id}`, `/history`, `/predictions`, `/alerts?vehicle_id=`, `POST /ml/predict/{id}`, WS |
| `/alerts` | Alerts — status/severity/source/vehicle filters, acknowledge | `GET /alerts`, `POST /alerts/{id}/acknowledge` |
| `/risk` | "High risk vehicles" / "Low risk vehicles" buttons, "Retrain model" / "Run prediction" | `GET /risk/high\|medium\|low`, `GET /simulations`, `/ml/train/status`, `POST /ml/train`, `/ml/predict` |
| `/simulation` | Create (demo / longitudinal / short-burst with fault injection), start/pause/resume/reset, live progress | `/simulations`, `/simulation`, `/simulation/{id}/…` |
| `/observability` | Ingestion counters, events/s + lag chart, API latency chart, service health + contract check (polls every 3 s) | `GET /observability` |
| `/sql` | SQL sandbox — editor, examples, result table | `GET/POST /sql-sandbox` |

## Structure

```
src/
  api/client.ts        fetch wrapper: attaches JWT, parses contract errors, 401 → logout + redirect to /login
  api/types.ts         response types
  auth/                AuthContext (token in localStorage, auto-logout at expiry), LoginPage
  hooks/useLiveSocket  one shared /ws/live connection (auto-reconnect w/ backoff), per-vehicle subscriptions
  hooks/useApi         fetch-on-deps + optional polling, drops stale responses
  components/          Layout (sidebar, live indicator), shared cards/badges/pager
  pages/               one file per page above
```

Role awareness: write actions (acknowledge, train/predict, simulation control, running SQL) are hidden or
disabled for VIEWER; the API enforces the same rules (403) regardless.

Empty states are handled everywhere — in particular the risk pages before ML has ever run.
