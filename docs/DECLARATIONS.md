# Declarations

## AI tools used

**Claude (Anthropic)** was used extensively and substantively throughout this project's development,
including:
- Architecture design and the full set of module specification documents (`docs/CONTRACT.md` and the four
  master docs) that the four services were built from.
- Code generation across all four services (data-gen, ingestion, ml, dashboard api+frontend).
- Debugging and fixing real defects found during integration (a threading race condition in the windowed
  aggregator, a timezone bug in fleet reloading, a Postgres I/O throughput bottleneck, a risk-threshold
  miscalibration found only once real data existed — see `docs/ADRs.md` for the full list).
- Merging code changes made across two separate development machines.
- Running and verifying the automated test suites, the k6 load test, and the chaos/resilience demo.
- Drafting this documentation set (ADRs, STRIDE threat model, this declarations file, and the Solution
  Document itself).

This was not a "generate once and ship" use of AI — the process involved real iteration: running the actual
system, finding genuine bugs and miscalibrations against real data and real load, and fixing them, documented
transparently in `docs/ADRs.md` and the master docs rather than presented as if everything worked correctly on
the first attempt.

## Open-source components

Major dependencies by service (full exact versions and licenses can be generated on demand via
`pip freeze` inside each Python service's container, or `npm list` for the frontend, for an exact SBOM):

| Component | Used for | Typical license |
|---|---|---|
| FastAPI | HTTP API framework (ingestion, ml, data-gen, api) | MIT |
| SQLAlchemy | Postgres ORM/query layer | MIT |
| psycopg2 | Postgres driver | LGPL |
| pymongo | MongoDB driver | Apache 2.0 |
| redis-py | Redis client | MIT |
| confluent-kafka | Kafka client (librdkafka binding) | Apache 2.0 |
| pybloom-live | Bloom filter dedup | MIT |
| scikit-learn | ML model (GradientBoostingRegressor) | BSD 3-Clause |
| pandas / numpy | Feature engineering, vectorized simulation physics | BSD 3-Clause |
| pytest | Test framework, all four services | MIT |
| React / Vite | Frontend framework and build tool | MIT |
| Recharts | Dashboard charts | MIT |
| nginx | Frontend static serving + reverse proxy | BSD-like (2-clause) |
| PostgreSQL, MongoDB, Redis, Apache Kafka | Core infrastructure | PostgreSQL license, SSPL/AGPL (Mongo, community use), BSD 3-Clause, Apache 2.0 respectively |
| k6 (Grafana) | Load testing | AGPL 3.0 (used as an external tool, not linked into the codebase) |

All of the above are used as-is via their published packages/images; none were modified.

## Data declaration

All vehicle, telemetry, battery, and fleet data used anywhere in this project is **synthetically generated**
by this project's own simulator (`services/data-gen`), seeded deterministically for reproducibility. No real
vehicle, driver, or owner data of any kind was used at any point. No real personal data is processed, stored,
or displayed anywhere in the system.
