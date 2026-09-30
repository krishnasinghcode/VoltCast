// k6 load test for the API's read endpoints (dashboardMaster.md §15).
//
//   k6 run infra/k6/burst_load_test.js
//   docker run --rm -i --network host grafana/k6 run - < infra/k6/burst_load_test.js
//
// Env overrides: API_URL (default http://localhost:8000), K6_EMAIL / K6_PASSWORD (default viewer demo user),
// THINK_TIME_S (default 1): mean pause between a VU's requests, randomised ±50%, modelling a person using the
// dashboard. THINK_TIME_S=0 turns it into a closed-loop stress test (every VU fires back-to-back), where
// latency is pure queueing: p95 ≈ 200 VUs / throughput.
//
// IMPORTANT: every VU shares ONE token (logged in once in setup), and the API rate-limits per user per
// endpoint (default 120/min). For a latency test, start the api with a high limit, e.g.
//   RATE_LIMIT_PER_MINUTE=100000000 docker compose up -d api
// otherwise most requests return 429 — those are counted separately below, not hidden.
//
// This measures API read latency only. Ingest throughput (100K events/s) comes from data-gen SHORT_BURST
// mode + ingestion /metrics — report the two numbers separately.

import http from "k6/http";
import { check, fail, sleep } from "k6";
import { Counter } from "k6/metrics";

const API = (__ENV.API_URL || "http://localhost:8000").replace(/\/$/, "");
const BASE = `${API}/api/v1`;
const rateLimited = new Counter("rate_limited_429");
const THINK_TIME_S = Number(__ENV.THINK_TIME_S ?? 1);

export const options = {
  scenarios: {
    dashboard_reads: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: 200 }, // ramp 0 -> 200 VUs
        { duration: "60s", target: 200 }, // hold
        { duration: "15s", target: 0 },   // ramp down
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    "http_req_duration{kind:read}": ["p(95)<200", "p(99)<500"],
    "checks{kind:read}": ["rate>0.99"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

export function setup() {
  const res = http.post(
    `${BASE}/auth/login`,
    JSON.stringify({ email: __ENV.K6_EMAIL || "viewer@cvi.local", password: __ENV.K6_PASSWORD || "viewer123" }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (res.status !== 200) fail(`login failed: ${res.status} ${res.body}`);
  const token = res.json("access_token");
  const params = { headers: { Authorization: `Bearer ${token}` } };

  // Real ids to hit: a page of vehicles and the fleets.
  const vehicles = http.get(`${BASE}/vehicles?limit=200`, params).json("items") || [];
  const fleets = http.get(`${BASE}/fleets?limit=200`, params).json("items") || [];
  if (!vehicles.length) fail("no vehicles in the database — run a simulation first");
  return {
    token,
    vehicleIds: vehicles.map((v) => v.vehicle_id),
    fleetIds: fleets.map((f) => f.fleet_id),
  };
}

function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

// Weighted toward what a dashboard session actually does.
const MIX = [
  { weight: 35, name: "vehicle_detail", path: (d) => `/vehicles/${pick(d.vehicleIds)}` },
  { weight: 25, name: "vehicle_search", path: (d) => `/vehicles?limit=25&search=${pick(d.vehicleIds).slice(0, 8)}` },
  { weight: 20, name: "fleet_summary", path: (d) => (d.fleetIds.length ? `/fleets/${pick(d.fleetIds)}/summary` : "/fleets/summary") },
  { weight: 20, name: "alerts", path: () => "/alerts?limit=50&status=OPEN" },
];
const TOTAL = MIX.reduce((s, m) => s + m.weight, 0);

export default function (data) {
  let r = Math.random() * TOTAL;
  const choice = MIX.find((m) => (r -= m.weight) < 0) || MIX[0];
  const res = http.get(`${BASE}${choice.path(data)}`, {
    headers: { Authorization: `Bearer ${data.token}` },
    tags: { kind: "read", endpoint: choice.name },
  });
  if (res.status === 429) rateLimited.add(1);
  check(res, { "status is 200": (x) => x.status === 200 }, { kind: "read" });
  if (THINK_TIME_S > 0) sleep(THINK_TIME_S * (0.5 + Math.random()));
}
