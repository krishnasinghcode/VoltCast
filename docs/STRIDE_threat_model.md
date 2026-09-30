# STRIDE Threat Model

Scope: the two attacker-facing surfaces — the public API (`dashboard/api`) and the telemetry ingestion path
(Kafka → `ingestion`). Internal service-to-service calls (e.g. `api` proxying to `data-gen`/`ml`) are treated
as trusted network, consistent with the rest of the design (see ADR-5 on the auth scope cut).

Attacker personas considered: an unauthenticated internet client hitting the public API; an authenticated but
low-privilege user (VIEWER) attempting to exceed their role; a compromised or malicious data source feeding
the ingestion pipeline (standing in for "a compromised OEM feed" in a real deployment).

| Threat | Where | Risk if unmitigated | Mitigation in this system | Residual gap (declared, not hidden) |
|---|---|---|---|---|
| **Spoofing** — impersonating a user or service | API auth | Attacker acts as another user, or as an internal service | JWT (HS256) required on every endpoint except `/health` and `/auth/login`; role embedded in the signed token, verified server-side per request | No mTLS between internal services — any container on the Docker network can call any other service's HTTP API directly. Declared scope cut (ADR-5). |
| **Tampering** — modifying data in transit or at rest | Telemetry ingestion; SQL sandbox | Malformed/adversarial telemetry corrupting aggregates; arbitrary SQL execution via the sandbox feature | Schema + range validation on every ingested event (`ingestionMaster.md` §8) rejects out-of-range values before they reach any store; SQL sandbox enforces SELECT/WITH-only (comment- and string-literal-aware parser, not a naive keyword grep), runs under a genuinely separate read-only Postgres role with `SET TRANSACTION READ ONLY`, a 3-second statement timeout, and an always-rolled-back transaction | TLS is not enabled between containers on the local Docker network (plaintext internal traffic) — acceptable for a single-host demo, a real gap in a multi-host deployment. |
| **Repudiation** — denying an action was taken | Alert acknowledgement, ML prediction runs, SQL sandbox queries | No record of who did what | `alerts.status` changes are attributable (author known from the JWT that made the call); SQL sandbox logs every query text with the calling user's ID (`sql_sandbox_router.py`) | No centralized, tamper-evident audit log (e.g. append-only, hash-chained) — a real production gap; current logging is standard application logs, not a compliance-grade audit trail. |
| **Information Disclosure** — exposing data beyond what's authorized | SQL sandbox; API responses | A low-privilege user reading password hashes or other users' predictions | Sandbox's read-only DB role has `REVOKE ALL ON TABLE users` — even a successful sandbox query cannot read password hashes regardless of what SQL is typed; RBAC (`require_role`) gates every endpoint by role | No field-level data masking (e.g. location data, if this project had location fields — it deliberately doesn't, having scoped to battery-only telemetry); no per-tenant row-level isolation (single-tenant MVP, declared). |
| **Denial of Service** — overwhelming the system | Public API; login endpoint specifically | Brute-force login attempts; a client hammering read endpoints | Per-user-per-endpoint rate limiting (Redis-backed sliding window, default 120/min); login is additionally rate-limited by client IP (not just by user, since an attacker has no valid user yet); ingestion's Bloom-filter dedup + schema validation rejects malformed/duplicate events before they reach expensive downstream writes | Single Kafka broker, single ingestion consumer thread — no horizontal scaling under sustained attack-scale load within this deployment (see the `SHORT_BURST` throughput test results for the actual measured ceiling). A determined attacker with more bandwidth than our measured ingest ceiling could still degrade the pipeline. |
| **Elevation of Privilege** — a low-privilege actor gaining high-privilege capability | RBAC boundaries | A VIEWER acknowledging alerts, triggering simulations/ML training, or running SQL sandbox writes | Every state-changing endpoint requires `FLEET_MANAGER` or `ADMIN` via `require_role(*WRITE_ROLES)`; verified by automated tests (`test_sandbox_rbac`, `test_register_is_admin_only`, etc. — all passing, see test evidence) | JWT secret is a single shared `HS256` key (`JWT_SECRET` env var) rather than per-environment asymmetric keys — acceptable for this deployment's scale, a real upgrade item for a multi-team or multi-environment production setup. |

## Summary for the Solution Document

Of the six STRIDE categories, the two strongest mitigations are **Tampering** on the SQL sandbox (genuinely
defense-in-depth: parser-level rejection, a real separate DB role, a hard timeout, and an always-rolled-back
transaction — not just one layer) and **Elevation of Privilege** (RBAC verified by automated tests, not just
claimed). The declared gaps — no mTLS, no centralized audit log, a single shared JWT secret, no per-tenant
isolation — are consistent with the scope cuts already made explicit in ADR-5 and should be read as a stated
roadmap for a production hardening pass, not as oversights.
