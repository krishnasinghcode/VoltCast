const { H1, H2, H3, P, PB, bold, ital, plain, bullet, pending, note, pageBreak, table, image } = require("./helpers");

const s4 = [
  H1("4. Feature List"),
  P("Every feature below is traceable to real code in the repository. Video column will be filled with the demo video timestamp once recorded (§13); all other columns reflect actual, verified status."),
  table(
    ["ID", "Feature", "Priority", "Status", "Code Path"],
    [
      ["F-01", "100K-vehicle deterministic fleet simulator (3 modes: SHORT_BURST/LONGITUDINAL/DEMO)", "Must", "Done", "services/data-gen"],
      ["F-02", "Kafka ingestion: schema validation, Bloom-filter dedup, windowed aggregation", "Must", "Done", "services/ingestion"],
      ["F-03", "30-day battery degradation prediction (GBM vs naive baseline)", "Must", "Done", "services/ml"],
      ["F-04", "Risk classification (LOW/MEDIUM/HIGH), empirically calibrated", "Must", "Done", "services/ml/src/risk_rules.py"],
      ["F-05", "JWT auth + RBAC (Admin/Fleet Manager/Viewer)", "Must", "Done", "services/dashboard/api/src/auth.py"],
      ["F-06", "Per-user, per-endpoint rate limiting (Redis-backed)", "Must", "Done", "services/dashboard/api/src/rate_limit.py"],
      ["F-07", "Fleet overview dashboard, live via WebSocket", "Must", "Done", "frontend/src/pages/FleetOverview.tsx"],
      ["F-08", "Vehicle search + detail/history", "Must", "Done", "frontend/src/pages/VehicleSearch.tsx, VehicleDetail.tsx"],
      ["F-09", "Real-time rule-based alerts (high temp, deep discharge) + ML-driven high-risk alerts", "Must", "Done", "services/ingestion/src/alert_rules.py, services/ml/src/predict.py"],
      ["F-10", "Guarded read-only SQL sandbox", "Should", "Done", "services/dashboard/api/src/routers/sql_sandbox_router.py"],
      ["F-11", "Observability page (ingestion + API metrics)", "Should", "Done", "services/dashboard/api/src/routers/observability_router.py"],
      ["F-12", "Simulation control panel (start/pause/reset, live progress/ETA)", "Should", "Done", "frontend/src/pages/SimulationControlPage.tsx"],
      ["F-13", "Chaos-tested self-healing (Docker restart policy + Kafka replay)", "Should", "Done", "docker-compose.yml, test-evidence/chaos_demo.ps1"],
      ["F-14", "Automated test suites (90 tests across 4 services)", "Must", "Done", "services/*/tests/"],
      ["F-15", "k6-load-tested API (p95/p99 well under target)", "Should", "Done", "infra/k6/burst_load_test.js"],
      ["F-16", "100K-vehicle ingest throughput proof (SHORT_BURST)", "Should", "Planned", "test-evidence/SHORT_BURST_100k_throughput_test.md"],
      ["F-17", "Query optimization evidence (EXPLAIN ANALYZE before/after)", "Should", "Planned", "TBD"],
    ],
    [900, 4200, 1000, 1200, 2050]
  ),
  pending("F-16 and F-17 have ready-to-run procedures documented (TODO.md, test-evidence/) but have not yet been executed at submission-writing time — fill in the real measured numbers once run, per the runbooks already in the repository."),
  pageBreak(),
];

const s5 = [
  H1("5. Solution Architecture (High-Level Design)"),
  H2("5.1 Architecture Overview"),
  P("System context: a single user class (fleet manager / analyst) interacts with the platform through the web dashboard. There are no external systems in this deployment (it is fully self-contained and simulates the connected-vehicle data source itself, standing in for a real OEM cloud feed in a production deployment)."),
  P("Container diagram (C4 Level 2) — five services, one Kafka topic, three datastores, one Docker network:"),
  image("docs/diagrams/architecture.png", 620, 346),
  H3("Data flow and per-hop latency"),
  bullet("Vehicle tick -> Kafka: sub-millisecond locally (in-process producer, batched by librdkafka)."),
  bullet("Kafka -> ingestion consume + validate + dedupe + Redis/Mongo write: near-real-time, bounded by the consumer's per-event processing cost (optimized from an initial ~400 events/sec bottleneck to a much higher throughput after batching Mongo writes and pipelining Redis operations — a real fix made during integration, see §12)."),
  bullet("Ingestion -> Postgres aggregates: periodic flush (default 30-second cycle), not per-event — a deliberate batching trade-off (ADR-3)."),
  bullet("Postgres aggregates -> ML prediction: on-demand (via /train and /predict calls), not continuously re-computed."),
  bullet("Postgres -> API -> dashboard: live REST reads plus a WebSocket push (default 2-second interval) for the fleet-overview live state."),
  H2("5.2 Technology Stack & Justification"),
  table(
    ["Layer", "Choice", "Why This, and What Was Rejected"],
    [
      ["Ingestion / Messaging", "Apache Kafka (KRaft mode)", "Durable, partitioned, replayable streams with consumer groups — the explicit recommendation in the problem statement. Rejected RabbitMQ: partition-based parallel consumption fits the scale story better. Also rejected the originally-planned bitnami/kafka image after its published tag disappeared mid-build (a real incident) in favor of the official apache/kafka image."],
      ["Stream / Batch Processing", "A plain in-process Python consumer doing windowed aggregation, not a stream-processing framework", "Dramatically faster to build under the time budget; Kafka itself (not the processing layer) is what provides durability/replay. Rejected Flink and Kafka Streams — see ADR-3 for the full trade-off, including the documented in-flight-state-loss limitation and its mitigation (Kafka replay)."],
      ["Relational / NoSQL / Cache", "PostgreSQL (relational core) + MongoDB (raw telemetry) + Redis (latest-state cache)", "Write isolation between transactional and firehose workloads — see ADR-1. Rejected a single-Postgres design (exactly the anti-pattern the problem statement describes) and Cassandra (added operational overhead not justified at this scale/time budget)."],
      ["Vector storage", "None", "The core decision is structured regression on engineered tabular features, not retrieval/semantic search — see ADR-2."],
      ["Backend", "FastAPI (Python), one language across all four services", "Minimizes integration friction when services are built independently/in parallel — a deliberate locked decision (CONTRACT.md §1), not an afterthought."],
      ["Frontend", "React + Vite, served by nginx with a same-origin reverse proxy to the API", "Fast dev loop; same-origin proxy avoids CORS entirely and keeps the JWT from ever leaving the one origin."],
      ["ML", "scikit-learn GradientBoostingRegressor", "Tabular regression on a few thousand engineered feature rows trains in seconds and is easy to reason about; rejected deep learning as unnecessary complexity for this problem size and time budget."],
      ["Infrastructure / DevOps", "Docker Compose, single host", "Every service is containerized with no cloud-specific SDK calls anywhere in the code — deployable to any cloud's container runtime with no code changes, satisfying the cloud-agnostic requirement without needing a full Kubernetes/Terraform build-out given the time budget (declared scope cut, see §12)."],
    ],
    [1550, 2450, 5350]
  ),
  H2("5.3 Data Architecture"),
  P("ER diagram (PostgreSQL relational core, normalized to 3NF, one deliberate denormalization documented below):"),
  image("docs/diagrams/er_diagram.png", 620, 349),
  PB([bold("Deliberate denormalization: "), plain("agg_daily.soh_pct duplicates the simulator's ground-truth SOH value for a given vehicle/day, rather than requiring every ML feature-build query to join back to the simulator's own state table. This is intentional and documented (see ADR list and mlMaster.md §8), not an oversight.")]),
  H3("Polyglot map and CAP choice"),
  table(
    ["Store", "Data", "CAP choice", "Why"],
    [
      ["PostgreSQL", "Fleets, vehicles, batteries, aggregates, predictions, alerts, users", "CP (strong consistency)", "Billing-adjacent and decision-critical data (predictions, alerts, access control) needs ACID guarantees and correct joins."],
      ["MongoDB", "Raw telemetry firehose", "AP (eventual consistency acceptable)", "Append-heavy, high-volume, time/vehicle-keyed access; losing strict consistency briefly under load is an acceptable trade for write throughput."],
      ["Redis", "Latest vehicle state, rate-limit counters", "AP (fully disposable)", "Purely a cache/derived-state layer; API already falls back to Postgres if Redis is unavailable (verified by an automated test, test_fails_open_when_redis_is_down)."],
    ],
    [1700, 3550, 2100, 2000]
  ),
  H3("Capacity and partitioning"),
  P("Kafka topic telemetry.raw: 12 partitions, keyed by vehicle_id, so one vehicle's events are always ordered on one partition while the fleet is spread evenly across partitions (avoiding hotspots). Measured real-world throughput during a chaos-demo run: 50 active vehicles produced/consumed roughly 260 events/sec sustained. A dedicated 100,000-vehicle SHORT_BURST throughput test is specified and ready to run (test-evidence/SHORT_BURST_100k_throughput_test.md) to produce the headline NFR number at full scale."),
  pending("the 100K-vehicle SHORT_BURST throughput number, and the EXPLAIN ANALYZE before/after query-optimization evidence — both have ready runbooks (TODO.md) but were not yet executed at the time this document was drafted."),
  H2("5.4 Deployment View"),
  P("Single Docker Compose stack, nine containers (kafka, postgres, mongo, redis, data-gen, ingestion, ml, api, frontend) on one Docker network, with named volumes for the three datastores so data survives container restarts. Every service reads all configuration from environment variables (CONTRACT.md §7) and contains no cloud-provider-specific SDK calls — meaning the same container images and compose definition (adapted to whichever container orchestrator a target cloud uses — ECS, GKE, AKS, or a self-managed Kubernetes cluster) would run unmodified. This project has not yet been deployed to an actual cloud environment (a declared scope decision given the available time — see §12); the cloud-agnostic property is a design property verified by code inspection (no SDK imports), not yet a live deployment."),
  pageBreak(),
];

const s6 = [
  H1("6. Low-Level Design"),
  H2("6.1 Layering & Separation of Concerns"),
  P("Architecture style: a pragmatic layered style (not full hexagonal/clean architecture, given the time budget), but with the one rule that actually matters enforced throughout: business/domain logic never imports a database or HTTP framework directly. For example, services/ml/src/risk_rules.py (the risk-derivation rule) has zero database or HTTP imports — it is a pure function of a number, testable in complete isolation (and is — see test_risk_rules.py, 5/5 passing)."),
  table(
    ["Layer", "Responsibility", "Must Not", "Example"],
    [
      ["Presentation / API", "HTTP/WebSocket routing, request validation, auth checks, DTO mapping", "Contain business rules or raw SQL", "services/dashboard/api/src/routers/*.py"],
      ["Application / Service", "Use-case orchestration (e.g. run a prediction, process one ingested event)", "Depend on a specific database driver directly", "services/ml/src/predict.py, services/ingestion/src/kafka_consumer.py"],
      ["Domain", "Pure business rules: risk thresholds, degradation formula, dedup logic", "Import a framework or infrastructure client", "services/ml/src/risk_rules.py, services/data-gen/src/degradation.py"],
      ["Infrastructure", "Repositories/clients: Postgres, Mongo, Redis, Kafka", "Leak vendor-specific types into the domain layer", "services/*/src/db.py, *_writer.py, kafka_consumer.py"],
    ],
    [1900, 2700, 2450, 2300]
  ),
  P("Repository tree (two levels), api service as the representative example:"),
  bullet("services/dashboard/api/src/ — main.py, auth.py, config.py, db.py, errors.py, metrics.py, rate_limit.py, seed_users.py, upstream.py"),
  bullet("services/dashboard/api/src/routers/ — alert_router.py, auth_router.py, fleet_router.py, ml_router.py, observability_router.py, prediction_router.py, simulation_router.py, sql_sandbox_router.py, vehicle_router.py, ws_router.py"),
  H2("6.2 Design Principles Applied"),
  PB([bold("SOLID: "), plain("Single Responsibility is the clearest — risk_rules.py does exactly one thing (threshold -> label); Dependency Inversion appears in the Redis pipelining refactor (alert_rules.py and redis_writer.py both accept a caller-supplied pipeline object rather than each managing their own connection, decoupling them from the exact I/O mechanism).")]),
  PB([bold("12-Factor App: "), plain("Config is entirely environment-variable driven (CONTRACT.md §7) with safe local defaults; services are stateless where it matters (ingestion's in-memory aggregation state is bounded and fully recoverable via Kafka replay if lost, a documented trade-off, not an unbounded stateful dependency); startup/shutdown is fast (seconds), supporting disposability.")]),
  PB([bold("Idempotency: "), plain("Mongo's unique index on event_id plus the Bloom filter makes raw ingestion idempotent under at-least-once delivery; every aggregate upsert uses ON CONFLICT DO UPDATE; the ML prediction endpoint was fixed during integration (a real, documented bug) to update an existing prediction on rerun for the same vehicle/horizon/model version rather than inserting duplicate rows.")]),
  PB([bold("Fail-Fast: "), plain("Schema and field-range validation reject malformed telemetry immediately at the ingestion boundary rather than allowing it to silently corrupt downstream aggregates — verified by 10+ passing range-violation test cases.")]),
  PB([bold("Least Privilege: "), plain("The SQL sandbox executes under a dedicated Postgres role with SELECT-only grants and an explicit REVOKE ALL on the users table, so no sandbox query — however cleverly written — can ever read a password hash.")]),
  PB([bold("DRY / KISS: "), plain("One shared CONTRACT.md is the single source of truth for every schema/port/event format used by all four independently-built services, preventing the divergent-redefinition problem that would otherwise occur when four separate builders each need the same shared types.")]),
  H2("6.3 Design Patterns Used"),
  table(
    ["Pattern", "Problem It Solves Here", "Location in Code"],
    [
      ["Repository", "Isolates SQL/Mongo/Redis details from the services that use them", "services/*/src/db.py, mongo_writer.py, redis_writer.py, postgres_writer.py"],
      ["Strategy", "Risk-level derivation is a swappable rule over a number, independent of how the number was produced", "services/ml/src/risk_rules.py"],
      ["Fail-Open / graceful degradation (a resilience pattern, adjacent to Circuit Breaker)", "The rate limiter must not become a single point of failure if Redis is briefly unavailable", "services/dashboard/api/src/rate_limit.py — verified by test_fails_open_when_redis_is_down"],
      ["Observer / pub-sub-style broadcast", "Pushing live fleet state to every connected dashboard client without each client polling", "services/dashboard/api/src/routers/ws_router.py (connection hub)"],
      ["Upsert-as-Idempotent-Write", "Re-processing the same aggregate window or the same prediction rerun must not create duplicates", "postgres_writer.py (ON CONFLICT DO UPDATE), predict.py (update-existing-prediction fix)"],
    ],
    [2400, 4150, 2800]
  ),
  H2("6.4 Interfaces, Contracts & Runtime Flows"),
  PB([bold("API Contract: "), plain("REST under /api/v1/, versioned; FastAPI auto-generates an OpenAPI schema (available at each service's /docs in development). Pagination is keyset-based everywhere (never offset), verified by automated tests (test_invalid_cursor, test_search_shape_and_pagination). Standard error envelope: {\"error\": {\"code\", \"message\", \"details\"}}.")]),
  PB([bold("Event Schema: "), plain("JSON over Kafka (not Avro/Protobuf — a declared simplification, CONTRACT.md §5), with an explicit contract_version field checked on every ingested event; a mismatch is a hard rejection, not a silent acceptance.")]),
  H3("Sequence — happy path"),
  P("Vehicle tick -> event_builder constructs the canonical event -> Kafka (telemetry.raw, partitioned by vehicle_id) -> ingestion validates + deduplicates + writes Mongo/Redis + folds into the in-memory aggregator -> periodic flush writes closed windows to Postgres -> ml reads Postgres aggregates on a training/prediction run -> writes battery_predictions and, for HIGH risk, an alert -> api reads Postgres/Redis -> dashboard displays the result, live-pushed over WebSocket."),
  H3("Sequence — failure path (broker/consumer down, as actually tested)"),
  P("The ingestion process crashes (simulated by killing its PID 1 internally, standing in for an unhandled exception or OOM in production). Kafka retains every event already produced, including ones not yet consumed. Docker's restart:unless-stopped policy detects the crashed process and restarts the container automatically — measured recovery time: 5.9 seconds. The consumer resumes from its last committed Kafka offset; the Bloom filter and Mongo unique-index dedup layer mean any event reprocessed during the recovery window cannot be double-counted. Post-recovery, events_consumed_total continued climbing (471 -> 865 across the test window) with consumer_lag draining (60 -> 31) — zero data loss, verified, not assumed. Full detail: test-evidence/chaos_demo_report_20261001_045620.md."),
  H2("6.5 Algorithms & Data Structures"),
  table(
    ["Problem Solved", "Algorithm / Structure", "Complexity", "Where"],
    [
      ["De-duplicating telemetry under at-least-once delivery without storing every event ID seen", "Bloom filter (probabilistic set membership) as the fast synchronous gate, backed by a Mongo unique-index as an exact backstop for whatever the filter's false-negative rate lets through", "O(1) time and space per check, independent of event volume", "services/ingestion/src/dedup.py"],
      ["Computing rolling mean/variance over a 5-minute window without storing every raw value", "Welford's online variance algorithm", "O(1) time and memory per event, per open window", "services/ingestion/src/windowed_aggregator.py"],
      ["Detecting charging-session start/end boundaries from a continuous telemetry stream", "Single-pass streaming state machine (adapted from classic trip/interval segmentation to charging sessions, since this project scoped to battery-only telemetry with no location data)", "O(1) memory per vehicle, O(1) time per event", "services/ingestion/src/session_segmenter.py"],
      ["Preventing ML label leakage (using future information to predict the past)", "An assert-enforced invariant: feature_window_end <= as_of_day, checked on every feature build", "O(1) check per sample; verified by a dedicated test (test_leakage_check_trips_when_feature_window_extends_past_as_of_day)", "services/ml/src/label_construction.py, feature_engineering.py"],
      ["Bounded-step physics simulation across up to 100,000 vehicles per tick", "Vectorized NumPy array operations across the whole active fleet at once (not a per-vehicle Python loop), since a naive per-vehicle loop over ~90 days x thousands of vehicles is computationally intractable in Python", "O(n) per tick across n active vehicles, vectorized (not O(n) in interpreted Python loop overhead)", "services/data-gen/src/simulation_runner.py, vehicle_state.py"],
    ],
    [2300, 2900, 2050, 2100]
  ),
  P("Pseudocode — Bloom-filter dedup with Mongo backstop:"),
  bullet("if bloom_filter.might_contain(event_id): return DUPLICATE  # O(1), may false-positive as \"maybe seen\" but never false-negative if sized correctly"),
  bullet("bloom_filter.add(event_id)"),
  bullet("try: mongo.insert(event)  # unique index on event_id is the exact, durable backstop"),
  bullet("except DuplicateKeyError: return DUPLICATE  # rare — only the Bloom filter's false-negative case reaches here"),
  P("Scale tested: 16,203 HTTP requests (k6), 471 -> 865 events through a live crash-recovery cycle (chaos demo), and the full automated test suites (90 tests) exercising these algorithms directly. The 100,000-vehicle scale test for the simulation/ingestion pipeline itself is specified and pending execution (§5.3, §7)."),
  pageBreak(),
];

module.exports = { s4, s5, s6 };
