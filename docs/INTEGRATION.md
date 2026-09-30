# INTEGRATION.md — Build Order, Wiring, and Conflict Resolution

This is the file YOU (the human) use to assemble the four zips into one running system, and the file used to
resolve any disagreement between modules. Read `CONTRACT.md` alongside this — CONTRACT.md is the schema
authority, this file is the sequencing/ops authority.

---

## 1. Build order

You do not need to build in strict sequence with four different AI sessions — CONTRACT.md is written so all
four modules can be built **in parallel**, each against `tools/fake_producer.py` / fixture data / mocked
upstream where noted in their own doc. But **integration/running order** is sequential:

1. **Infra first.** Get `docker-compose.yml` (§3 below), `.env` (copy from `.env.example`, fill real local
   secrets), and `infra/postgres/init.sql` (CONTRACT.md §3, save it into that path) in place. Bring up just
   the four infra containers and confirm they're healthy:
   ```bash
   docker compose up -d kafka postgres mongo redis
   docker compose ps   # all "healthy" or "running"
   ```

2. **Drop in each module's zip** into its `services/<name>/` (or `frontend/`) folder per the repo layout in
   CONTRACT.md §0. Do this for all four now — order of dropping-in doesn't matter, order of starting does.

3. **Start ingestion before data-gen** (ingestion needs to be consuming — with `auto.offset.reset=earliest` —
   before or right as data-gen starts producing, so nothing is missed; if ingestion starts late it'll just
   replay from the beginning of the topic, which is fine, just don't delete the topic in between):
   ```bash
   docker compose up -d ingestion
   docker compose logs -f ingestion   # confirm it's connected, consumer group registered
   ```

4. **Start data-gen, create and run a LONGITUDINAL simulation** to build real history:
   ```bash
   docker compose up -d data-gen
   curl -X POST localhost:8001/api/v1/simulation -d '{...LONGITUDINAL config, ~60-90 duration_days...}'
   curl -X POST localhost:8001/api/v1/simulation/<id>/start
   # wait for it to reach COMPLETED (watch GET /simulation/{id}, or docker compose logs -f data-gen)
   ```
   Then verify data landed: `SELECT count(*) FROM agg_daily;` should be large (vehicles × days).

5. **Bring up ML, train, predict:**
   ```bash
   docker compose up -d ml
   curl -X POST localhost:8003/api/v1/train -d '{"simulation_id":"<id>","horizon_days":30}'
   curl localhost:8003/api/v1/train/status   # confirm mae_model < mae_baseline
   curl -X POST localhost:8003/api/v1/predict -d '{"simulation_id":"<id>"}'
   ```

6. **Bring up API + frontend:**
   ```bash
   docker compose up -d api frontend
   ```
   `seed_users.py` (demo users + the SQL-sandbox read-only Postgres role) runs automatically on every `api`
   container boot — no manual step needed; safe to re-run by hand too, it's idempotent.
   Open `localhost:3000` (prod nginx build), log in with `admin@cvi.local` / the `SEED_ADMIN_PASSWORD` from
   `.env` (default `admin123`), click through Fleet Overview → Vehicle Search → Risk page → Observability →
   SQL Sandbox.

7. **Run a SHORT_BURST simulation separately** (fresh `simulation_id`, small `duration_days=1`,
   `burst.enabled=true`, heavier `fault_injection` rates) purely to generate the throughput number for the
   NFR table — watch `ingestion`'s `/metrics` during the burst.

8. **Run k6** (`infra/k6/burst_load_test.js`) against the now-populated API for the latency numbers.

---

## 2. Known cross-module assumption fixes (resolved here so no module needs to be told to "go fix" the other)

- **`simulation_vehicle_state` is NOT a history table** (CONTRACT.md §3 gives it PK
  `(simulation_id, vehicle_id)` — one row per vehicle, overwritten each tick, data-gen's own "current state"
  view). ML's historical SOH series comes from `agg_daily.soh_pct` instead (append-only by
  `(vehicle_id, date)`). This is already called out inside `mlMaster.md` §8 — if the ML module was built
  before that note was added, or a builder missed it, this is the fix: point `label_construction.py` at
  `agg_daily`, not `simulation_vehicle_state`, for anything historical.
- **`agg_daily.soh_pct` is populated by ingestion, sourced from data-gen's per-day snapshot** — if ingestion
  was built without wiring this (it's a small detail in `ingestionMaster.md` §5, easy to miss), the symptom
  is `agg_daily.soh_pct` being NULL for all rows, and ML's `/train` will fail loudly with an empty label set.
  Fix: ingestion's daily-bucket flush must read `simulation_vehicle_state.soh_pct` for that vehicle at flush
  time and copy it in.
- **Contract version mismatch:** if any service's `/health` reports a different `contract_version` than
  `1.0.0`, stop and fix that service — do not let mismatched services talk to each other, the field ranges or
  table shapes may have silently drifted.
- **Empty risk pages / empty predictions on first boot:** expected until step 5 above runs `/predict` at
  least once. Not a bug.

---

## 3. `docker-compose.yml` (canonical — the human assembles this once, in the repo root)

```yaml
services:
  kafka:
    image: apache/kafka:3.7.0
    ports: ["9092:9092"]
    environment:
      - KAFKA_NODE_ID=1
      - KAFKA_PROCESS_ROLES=broker,controller
      - KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093
      - KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://kafka:9092
      - KAFKA_CONTROLLER_QUORUM_VOTERS=1@kafka:9093
      - KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER
      - KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
      - KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT
      - KAFKA_AUTO_CREATE_TOPICS_ENABLE=true
      - KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1
    networks: [cvi-net]

  # NOTE 1: originally specced as bitnami/kafka:3.7 — that tag was pulled from Docker Hub (Bitnami tag churn).
  # Switched to the official apache/kafka image with native KRaft support, no Zookeeper needed either way.
  # NOTE 2: do NOT set a plain-string CLUSTER_ID (e.g. "cvi-local-cluster-1") — KRaft's storage-format step
  # requires a 22-char base64-URL-encoded UUID, and an invalid value makes the container crash-loop on boot,
  # which surfaces downstream as "Failed to resolve 'kafka:9092'" DNS errors in every other service, not as
  # an obvious kafka-side error. Leave CLUSTER_ID unset — the image's entrypoint auto-generates a valid one
  # on each boot, which is fine since this compose file mounts no persistent volume for kafka's own data.

  postgres:
    image: postgres:16
    ports: ["5432:5432"]
    environment:
      - POSTGRES_DB=${POSTGRES_DB}
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./infra/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    networks: [cvi-net]

  mongo:
    image: mongo:7
    ports: ["27017:27017"]
    environment:
      - MONGO_INITDB_DATABASE=${MONGO_DB}
    volumes: [mongodata:/data/db]
    networks: [cvi-net]

  redis:
    image: redis:7
    ports: ["6379:6379"]
    networks: [cvi-net]

  data-gen:
    build: ./services/data-gen
    ports: ["8001:8001"]
    env_file: .env
    depends_on: [kafka, postgres]
    networks: [cvi-net]

  ingestion:
    build: ./services/ingestion
    ports: ["8002:8002"]
    env_file: .env
    depends_on: [kafka, postgres, mongo, redis]
    networks: [cvi-net]

  ml:
    build: ./services/ml
    ports: ["8003:8003"]
    env_file: .env
    volumes: [./services/ml/models:/app/models]
    depends_on: [postgres]
    networks: [cvi-net]

  # api and frontend: see the repo's actual docker-compose.yml for the current, authoritative
  # definitions -- they moved to services/dashboard/api and services/dashboard/frontend (not the
  # services/api + top-level frontend/ paths originally specced here), gained a healthcheck on api,
  # and api's depends_on was narrowed to [postgres, redis] only -- data-gen/ml are called over HTTP
  # at request time (proxied), not required to be up before api starts. See CONTRACT.md §0's note.

networks:
  cvi-net:

volumes:
  pgdata:
  mongodata:
  kafkadata:
```

One-command run (satisfies the repository checklist's "One-Command Run"):
```bash
docker compose up --build
```

---

## 4. CI (`.github/workflows/ci.yml`) — minimal, real

```yaml
name: CI
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        service: [data-gen, ingestion, ml, api]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r services/${{ matrix.service }}/requirements.txt
      - run: pip install pytest
      - run: pytest services/${{ matrix.service }}/tests
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: cd frontend && npm install && npm run build
```

This is "tests run in CI on every push" — real, minimal, no framework overreach.

---

## 5. Turning `CONTRACT.md` §9's decision log + this project's locked decisions into ADRs

Format each as: **Context → Options Considered → Decision → Consequences.** Suggested 5, pulled directly from
already-made decisions (no new thinking needed, just formatting for the Solution Document §12):

1. Polyglot storage split (Postgres relational core / Mongo raw telemetry / Redis latest-state) — options
   considered: single Postgres, Postgres+Cassandra, Postgres+Mongo+Redis. Consequences: write isolation
   between transactional and firehose workloads, extra operational surface (3 datastores instead of 1).
2. No vector DB in MVP — options: pgvector, Qdrant, none. Consequences: faster build, no
   retrieval/embedding feature possible without a follow-up iteration.
3. Kafka + plain in-process consumer instead of Flink/Kafka Streams — options: Flink, Kafka Streams, plain
   consumer. Consequences: much faster to build, no checkpointing (documented limitation in
   `ingestionMaster.md` §5), fully recoverable via replay anyway.
4. Simulator as sole ground truth, longitudinal history derived from telemetry rather than separately
   generated — options: two independent generators, single generator. Consequences: internal consistency
   guaranteed, ML labels are trustworthy by construction.
5. Minimal JWT auth instead of full OAuth2/OIDC/mTLS — options: full NFR-table security stack, minimal JWT.
   Consequences: faster build, explicitly documented as a scope cut with a stated upgrade path (swap the
   `auth.py` JWT issuance for a real IdP later without touching `require_role` call sites).

---

## 6. Final checklist against the problem statement's Deliverables (§13) before submission

- [ ] Solution Document filled from the template, referencing this repo's ADRs/diagrams
- [ ] `docker compose up --build` works from clean clone + seeded 100K-vehicle dataset (run a LONGITUDINAL
      simulation once and commit a Postgres dump / document the exact commands to regenerate it)
- [ ] Architecture diagram (draw from CONTRACT.md §2 service map + the flow in this file), ER diagram (from
      CONTRACT.md §3 DDL), 3-5 ADRs (§5 above)
- [ ] Working end-to-end demo on the simulated stream (steps 3-6 above)
- [ ] Test evidence: `pytest` output/coverage per service, k6 summary, one manual chaos demo recording
      (`ingestionMaster.md` §14)
- [ ] DevOps pack: Dockerfiles (done per service), `docker-compose.yml` (done), STRIDE threat model (short
      write-up: attacker = malicious API client / compromised OEM feed / insider; walk the ingestion path and
      public API through Spoofing/Tampering/Repudiation/Info-disclosure/DoS/Elevation, note which are
      mitigated — JWT+RBAC for spoofing/elevation, validation+rate-limit for tampering/DoS — and which are
      explicitly out of scope — mTLS, vault)
- [ ] Algorithms & SQL write-up: Bloom filter dedup + windowed aggregation + charging-session segmentation
      (all already built, just document them, per `ingestionMaster.md` §5-6) + one EXPLAIN ANALYZE
      before/after on the slowest dashboard query (likely `vehicles` search or `agg_daily` range scan — add
      the composite index if missing, show the plan diff)
- [ ] Demo video ≤5 min following the template's timing breakdown
- [ ] Tag final commit `v1.0-submission`
