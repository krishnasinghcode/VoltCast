# Architecture Decision Records

Format: Context → Options Considered → Decision → Consequences. These feed directly into the Solution
Document's §12 (Architecture Decisions, Risks & Future Enhancements).

---

## ADR-1: Polyglot storage — PostgreSQL + MongoDB + Redis, not a single database

**Context.** The platform has three fundamentally different data-access patterns: (a) stable, relational,
transactional entities (fleets, vehicles, batteries, predictions, alerts) that need ACID guarantees and
foreign-key integrity; (b) a high-volume, append-only telemetry firehose (potentially 100K+ events/sec) that
needs fast writes and time/vehicle-keyed reads, not joins; (c) a small set of "latest known state" values that
the dashboard polls constantly and needs sub-millisecond reads for.

**Options considered.**
- Single PostgreSQL instance for everything.
- PostgreSQL + Cassandra (wide-column store for telemetry).
- PostgreSQL + MongoDB + Redis (the chosen split).

**Decision.** PostgreSQL owns the relational core and ACID-sensitive data. MongoDB owns raw telemetry
(append-heavy, schema-flexible, time/vehicle-keyed access, naturally horizontally shardable later).
Redis owns latest-vehicle-state and rate-limit counters — disposable, derived, never a system of record.

**Consequences.** Write isolation between the transactional core and the telemetry firehose means a burst of
telemetry writes cannot starve dashboard queries or lock contention on business tables (a problem the problem
statement explicitly calls out for single-SQL designs). Trade-off: three datastores instead of one means more
operational surface (three connection pools, three sets of credentials, three things that can be down). We
accepted this because the alternative — one PostgreSQL instance under both transactional and firehose load —
is exactly the failure mode the case study is built around.

---

## ADR-2: No vector database in the MVP

**Context.** The problem statement requires "a combination of relational, NoSQL and (where useful) vector
storage" — vector storage is explicitly conditional on genuine need, not mandatory.

**Options considered.** pgvector (extension on the existing Postgres), a dedicated vector DB (Qdrant), or none.

**Decision.** None. The core decision — predicting 30-day battery degradation from engineered tabular features
— is a structured regression problem. There is no retrieval-augmented-generation or semantic-search feature in
this MVP that a vector store would serve.

**Consequences.** Faster build, one fewer moving part to operate and secure. Trade-off: if a future iteration
adds a genuine retrieval feature (e.g. "find vehicles with similar degradation patterns" via embeddings), this
would need to be added then — not a cost paid now, but a capability not yet built.

---

## ADR-3: Kafka with a plain in-process consumer, not Flink or Kafka Streams

**Context.** The platform needs durable, partitioned, replayable event streaming (Kafka, chosen and not
debated) feeding into windowed aggregation (5-min/hourly/daily rollups). The aggregation layer itself could be
a full stream-processing framework or a simpler custom consumer.

**Options considered.** Apache Flink, Kafka Streams, or a plain Python consumer doing in-memory windowing with
periodic flush to Postgres.

**Decision.** Plain consumer. Kafka itself (partitioning, replay, consumer groups, durability) is unchanged —
only the processing layer on top is simplified.

**Consequences.** Dramatically faster to build under the hackathon's time constraint, at the cost of losing a
real framework's built-in checkpointing: if the ingestion process crashes between flush cycles, in-flight
(not-yet-flushed) aggregate state since the last flush is lost from memory. This is a real, documented
limitation (`docs/ingestionMaster.md` §5) — but it's fully recoverable, since Kafka retains the raw events and
a fresh consumer group can replay from `auto.offset.reset=earliest` to rebuild aggregates. We traded "instant,
automatic recovery of partial in-flight state" for "fully recoverable, but requires a replay," which was the
right trade for a multi-day build.

---

## ADR-4: The simulator is the sole ground truth; longitudinal history is derived from telemetry, not
separately generated

**Context.** The project needs both high-frequency raw telemetry (for the ingestion/throughput story) and
multi-week longitudinal history (for ML training labels). A naive approach might generate these as two
independent datasets.

**Options considered.** Two independent generators (one for raw telemetry, one for longitudinal SOH history),
versus a single generator where longitudinal history is a downstream aggregation of the same event stream.

**Decision.** Single generator. The simulator alone owns ground-truth vehicle state (SOC, SOH, odometer,
etc.); raw telemetry and all aggregates (5-min/hourly/daily) are derived from that one stream, never
independently fabricated.

**Consequences.** Internal consistency is guaranteed by construction — there is no possibility of the raw
telemetry and the longitudinal history disagreeing with each other, because there is only one source of truth
underneath both. This is also what makes ML labels trustworthy: the 30-day degradation label is the actual
simulated outcome, not a second, potentially-inconsistent estimate.

---

## ADR-5: Minimal JWT authentication instead of full OAuth2/OIDC/mTLS

**Context.** The NFR table calls for OAuth2/OIDC, RBAC with tenant isolation, mTLS for devices, and a secrets
vault — a substantial security stack that would consume a large fraction of the available build time relative
to its grading weight versus the core data/ML pipeline.

**Options considered.** The full NFR-table security stack, versus a minimal scoped-down implementation: JWT
(HS256) issued by the API itself, role-based access control (ADMIN/FLEET_MANAGER/VIEWER), per-endpoint rate
limiting, and secrets kept in a gitignored `.env` (never committed).

**Decision.** Minimal JWT + RBAC + rate limiting, explicitly declared as a scope cut rather than presented as
production-complete.

**Consequences.** Faster build, and a real, working auth story (not a stub) — just not the full enterprise
stack. The upgrade path is narrow and clean: `auth.py`'s JWT issuance can be swapped for a real IdP's token
verification later without touching any `require_role(...)` call site, since the role-check dependency is
decoupled from how the token was issued. What's genuinely missing versus the NFR table: OAuth2/OIDC federation,
mTLS for device identity, and a secrets vault — declared honestly in the Security section rather than implied.

---

## ADR-6: Risk thresholds calibrated empirically from real prediction data, not set a priori

**Context.** The ML model's risk-level bucketing (LOW/MEDIUM/HIGH) needs threshold values on the predicted
30-day degradation percentage. Initial values (2.5% for HIGH, 1.0% for MEDIUM) were educated guesses made
before any real simulated data existed.

**Options considered.** Keep the a priori guessed thresholds, or recalibrate them once real prediction data was
available from an actual training run.

**Decision.** Recalibrated. After running a real 200-vehicle/90-day simulation and training the model, the
actual predicted-degradation distribution showed HEALTHY averaging 0.14%, NORMAL 0.21%, STRESSED 0.41% (p90
0.67%), and BAD 0.46% (p90 0.91%) — meaning the original 1.0%/2.5% thresholds meant literally nothing could
ever reach HIGH (BAD's observed maximum was 0.94%, still below 2.5%), and every single vehicle was
classified LOW regardless of archetype. Thresholds were changed to 0.20% (MEDIUM) / 0.45% (HIGH).

**Consequences.** This is not a cosmetic tuning choice — it's the difference between a risk feature that works
and one that silently does nothing. It also surfaces a broader lesson documented across this project: several
placeholder values (grace periods, thresholds, batch sizes) were initially set from reasoning alone and had to
be corrected once real data or real load was available; this ADR is the clearest, most consequential instance
of that pattern, and is disclosed here deliberately rather than presented as if the final values were always
known in advance.
