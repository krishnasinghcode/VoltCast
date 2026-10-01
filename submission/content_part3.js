const { H1, H2, H3, P, PB, bold, ital, plain, bullet, pending, note, pageBreak, table, image } = require("./helpers");

const s7 = [
  H1("7. Non-Functional Requirements & Performance Benchmarks"),
  table(
    ["NFR", "Target", "Achieved", "How Measured"],
    [
      ["Ingest Throughput", "100,000+ events/sec", "PENDING — runbook ready, not yet executed", "test-evidence/SHORT_BURST_100k_throughput_test.md (100K-vehicle SHORT_BURST mode, measured via ingestion's /metrics events_consumed_total delta over time)"],
      ["End-to-End Latency", "< 2s dashboard; < 5s critical alert", "Real-time alerts fire inline during ingestion, same event (sub-second rule evaluation); dashboard live-state push interval is 2s (WS_PUSH_INTERVAL_SECONDS) — not independently measured end-to-end under load", "Configuration value (push interval) + code inspection of alert_rules.py's inline evaluation; not a dedicated latency measurement"],
      ["API Latency", "p95 < 200 ms; p99 < 500 ms", "ACHIEVED — p95 = 60.84 ms; p99 = 93.4 ms", "k6, 200 concurrent VUs, 30s ramp/60s hold/15s down, 16,203 requests, 100% success. test-evidence/k6_load_test_results.md"],
      ["Resilience", "Recovers after broker / pod failure", "ACHIEVED — internal process crash recovered automatically in 5.9s with zero data loss", "test-evidence/chaos_demo.ps1 + generated report; see §6.4 and §9"],
      ["Availability", "99.9%, no single point of failure", "PARTIAL — each datastore (Postgres/Mongo/Redis/Kafka) runs as a single instance in this deployment; no replication/clustering configured", "Declared gap — a real multi-replica deployment is future work (§12), not measured as a live SLA here"],
    ],
    [1550, 2400, 3050, 2350]
  ),
  H3("Load test setup and results"),
  P("Tool: k6 (Grafana), run via the official Docker image sharing the api container's network namespace. Scenario: ramping-VUs executor, 0 -> 200 VUs over 30s, hold 200 VUs for 60s, ramp down over 15s. Traffic mix weighted toward realistic dashboard usage: vehicle detail (35%), vehicle search (25%), fleet summary (20%), alerts list (20%). Rate limiting was temporarily raised for the duration of this specific test (to measure true latency headroom rather than 429 throttling) and restored to its normal default immediately afterward."),
  table(
    ["Metric", "Result"],
    [
      ["Total requests", "16,203 (+ some setup calls, 16,206 total HTTP requests)"],
      ["Success rate", "100.00% (0 failed)"],
      ["avg / median latency", "20.13 ms / 12.43 ms"],
      ["p90 / p95 / p99 latency", "45.1 ms / 60.86 ms / 93.5 ms"],
      ["max latency", "364.1 ms (single outlier; p99 band max was 179.48 ms)"],
      ["Throughput", "~152.4 requests/sec sustained"],
    ],
    [3400, 5950]
  ),
  pageBreak(),
];

const s8 = [
  H1("8. Security & Compliance"),
  P("Full threat model: docs/STRIDE_threat_model.md. Summary below."),
  table(
    ["STRIDE Category", "Key Mitigation", "Declared Residual Gap"],
    [
      ["Spoofing", "JWT (HS256) required on every endpoint except /health and /auth/login; role embedded in the signed token and verified server-side per request", "No mTLS between internal services — any container on the Docker network can call any other service's HTTP API directly"],
      ["Tampering", "Schema + range validation on every ingested event; SQL sandbox enforces SELECT/WITH-only via a comment- and string-literal-aware parser (not a naive keyword grep), a genuinely separate read-only Postgres role, a 3-second statement timeout, and an always-rolled-back transaction", "No TLS between containers on the local Docker network (plaintext internal traffic) — acceptable for a single-host demo, a real gap for a multi-host deployment"],
      ["Repudiation", "Alert acknowledgements and SQL sandbox queries are attributable to the authenticated user who made the call; sandbox logs every query's text with the caller's user ID", "No centralized, tamper-evident (e.g. hash-chained) audit log — current logging is standard application logs, not a compliance-grade audit trail"],
      ["Information Disclosure", "Sandbox's read-only role has REVOKE ALL ON TABLE users — even a successful sandbox query cannot read password hashes regardless of what SQL is typed; RBAC gates every endpoint by role", "No field-level data masking (project deliberately has no location data to mask); no per-tenant row-level isolation (single-tenant MVP, declared)"],
      ["Denial of Service", "Per-user-per-endpoint rate limiting (Redis-backed sliding window, 120/min default); login additionally rate-limited by client IP; Bloom-filter dedup + schema validation reject malformed/duplicate events before expensive downstream writes", "Single Kafka broker, single ingestion consumer thread — no horizontal scaling under sustained attack-scale load in this deployment"],
      ["Elevation of Privilege", "Every state-changing endpoint requires FLEET_MANAGER or ADMIN via require_role(*WRITE_ROLES); verified by automated tests (test_sandbox_rbac, test_register_is_admin_only, all passing)", "Single shared HS256 JWT secret rather than per-environment asymmetric keys"],
    ],
    [1700, 4300, 3350]
  ),
  H3("Authentication & Authorization"),
  P("JWT (HS256), three roles (ADMIN, FLEET_MANAGER, VIEWER), enforced via a FastAPI dependency on every protected route. This is a declared, documented scope cut versus the full NFR-table ask (OAuth2/OIDC, mTLS, a secrets vault) — see ADR-5 for the full reasoning and the stated upgrade path (swapping JWT issuance for a real IdP later without touching any require_role call site)."),
  H3("Device Identity & Encryption"),
  P("Not implemented in this deployment: no mTLS for simulated \"devices\" (the data-gen simulator stands in for real vehicle hardware), no TLS between internal containers. Secrets (DB passwords, JWT secret) are environment-variable-based, kept in a gitignored .env file (never committed; .env.example provides the template with placeholder values)."),
  H3("Privacy"),
  P("No real personal or vehicle-owner data is used anywhere (all data is synthetically generated — see Declarations, §16). No location data is collected at all (the project deliberately scoped to battery-only telemetry, so there is no location field to mask or anonymize in the first place)."),
  H3("AI Safety (SQL Sandbox as the relevant analog)"),
  P("This project has no LLM/agentic component in its core decision path (§11 explains why), so classic prompt-injection concerns do not directly apply. The closest analogous risk — a user-facing feature that executes arbitrary input against the database — is the SQL sandbox, and it is defended in depth: a parser that correctly handles comments and string literals (so a real DELETE cannot be hidden inside a comment or a string that merely contains the word), a genuinely separate database role (not just an application-level check), a hard timeout, a forced row limit, and a transaction that is always rolled back even on success."),
  pageBreak(),
];

const s9 = [
  H1("9. Test Strategy"),
  table(
    ["Test Type", "Tools", "No. of Tests", "Coverage / Result", "In CI?"],
    [
      ["Unit", "pytest", "90 (data-gen 8, ingestion 24, ml 11, api 47)", "100% passing; line/branch coverage not separately measured (coverage.py not run) — declared gap", "Yes (.github/workflows/ci.yml runs all four suites + a frontend build on every push)"],
      ["Integration & contract", "pytest with an in-memory SQLite fixture DB + fakeredis (api); real-logic tests against the actual aggregator/segmenter code (ingestion)", "Included within the above 90 (e.g. test_detail_falls_back_to_postgres, windowed-aggregator flush tests)", "Passing. Declared simplification: not full Testcontainers-based cross-service integration tests against real Postgres/Kafka in CI", "Yes, same CI run"],
      ["Acceptance (BDD)", "Not implemented (no Cucumber/behave)", "0", "Declared gap given the time budget; the Feature List (§4) user stories serve as informal acceptance criteria instead", "No"],
      ["Performance / Load", "k6 (Docker)", "1 scenario, 16,203 requests", "PASSED both thresholds: p95 60.84ms (<200ms), p99 93.4ms (<500ms); consumer-throughput SHORT_BURST test pending (§7)", "No (run manually, not wired into CI)"],
      ["Security (SAST/DAST/dependency scan)", "Not run (no Semgrep, OWASP ZAP, or Trivy scan performed)", "0", "Declared gap; the SQL sandbox's defense-in-depth design (§8) is the strongest concrete security evidence available instead of a scan report", "No"],
      ["Compliance & Chaos", "Custom PowerShell chaos script (test-evidence/chaos_demo.ps1)", "1 full run, automated and reproducible", "PASSED — internal crash recovered in 5.9s, zero data loss, consumer lag draining", "No (run manually)"],
    ],
    [1600, 2300, 1550, 2950, 950]
  ),
  H3("Edge cases covered"),
  bullet("Out-of-order telemetry events: deliberately injected by the simulator's fault-injection config (out_of_order_rate) and handled without crashing (test_out_of_order_event_folded_without_crash, and observed live in real runs)."),
  bullet("Duplicate events: deliberately injected (duplicate_rate) and caught by the Bloom filter + Mongo unique-index backstop (test_second_occurrence_flagged_duplicate, test_duplicate_dropped_before_any_db_write, plus real observed dedup counts in /metrics during live runs)."),
  bullet("Malformed / out-of-range telemetry: deliberately injected (malformed_rate) and rejected at the validation boundary (10 parametrized range-violation tests, all passing; real rejection logs observed during live runs, e.g. soc_pct=150.0, voltage_v=-10.0)."),
  bullet("Broker/consumer crash: the chaos demo (§6.4, §9 above) — a real, reproducible, automated test, not a manual one-off."),
  bullet("SQL sandbox bypass attempts: 10 parametrized adversarial queries (DELETE hidden in a comment, a CTE wrapping a DELETE, dollar-quoted strings, a semicolon-separated second statement, SELECT ... INTO) — every one correctly rejected (test_sandbox_rejects, all passing)."),
  H3("Sample test evidence"),
  P("Full pytest output, k6 summary, and the chaos demo's generated report are preserved in test-evidence/ and inline in the project's development history. Representative result: pytest tests/ -v against the api service's 47 tests completed in 13.84s with 0 failures (once run against an isolated environment matching CI's conditions — see the note below on one initially-confusing result)."),
  note("One pair of api tests (test_observability_shape_when_peers_down, test_proxies_report_upstream_unavailable) initially appeared to fail when run manually inside the already-running, fully-live Docker stack — not because of an application bug, but because those two tests rely on peer services (data-gen/ingestion/ml) being genuinely unreachable to exercise the \"upstream down\" code path, and the live stack's real .env values made them reachable, overriding the test's intended isolation (os.environ.setdefault only applies when a variable is not already set). Re-run with those three peer URLs explicitly overridden to a closed port for that one invocation, all 47 passed. This is documented here deliberately, as an example of verifying an unexpected result rather than either hiding it or accepting a wrong explanation."),
  pageBreak(),
];

const s10 = [
  H1("10. Observability"),
  P("Dashboard Observability page: merges ingestion's own /metrics (events_consumed_total, events_deduplicated_total, events_rejected_total, consumer_lag, current_watermark_timestamp) with the API's own request-count/error-rate/latency counters, in one view — no separate Grafana/Prometheus stack was stood up (a declared scope cut given the time budget), but the same numbers that stack would show are available directly."),
  H3("Troubleshooting walk-through: finding the cause of a latency spike"),
  bullet("Check GET /api/v1/observability first — api.p95_latency_ms and api.errors_total tell you immediately whether the API layer itself is slow/erroring, versus the problem being upstream."),
  bullet("Check ingestion.consumer_lag in the same response (or directly at ingestion's /metrics) — a growing, non-draining lag means ingestion cannot keep up with production rate, which is exactly the real bottleneck this project found and fixed once during development (an unbatched Mongo/Redis I/O pattern capping throughput around ~400 events/sec until it was batched/pipelined)."),
  bullet("Check docker compose logs ingestion for the periodic \"flush cycle\" log line (added specifically for this kind of debugging during development) — it reports max_seen timestamp, pending bucket counts, and how many buckets closed on each ~30-second cycle, which is exactly what let this project diagnose a real stuck-aggregation bug (buckets never closing) down to its root cause (a too-large or wrongly-timed grace period) rather than guessing."),
  bullet("For a crash specifically: docker compose ps -a shows container exit codes and restart counts; docker inspect <container> --format \"{{.State.StartedAt}}\" confirms when (and whether) a restart actually happened."),
  pageBreak(),
];

module.exports = { s7, s8, s9, s10 };
