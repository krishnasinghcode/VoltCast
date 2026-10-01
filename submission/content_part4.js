const { H1, H2, H3, P, PB, bold, ital, plain, bullet, pending, note, pageBreak, table, image } = require("./helpers");

const s11 = [
  H1("11. AI / ML Component"),
  H2("Purpose"),
  P("The model turns accumulated per-vehicle telemetry history into one forward decision: predicted battery degradation over the next 30 simulated days. This is not a decision rules alone can make well — the degradation formula this project's own simulator uses to generate ground truth combines five distinct physical stress factors (calendar aging, cycle count, thermal exposure, fast-charging frequency, deep-discharge frequency), each contributing nonlinearly and multiplicatively (via an archetype-driven stress multiplier) to the outcome. A single hand-written rule would have to either ignore most of that interaction or become an unmaintainable pile of nested conditionals; a learned regression model captures the interaction directly from data."),
  H2("Data & Features"),
  P("Training/evaluation run used: 200 vehicles, 90 simulated days (seed-reproducible). Features are built from PostgreSQL's agg_daily rollups over rolling 7-day and 30-day windows (current SOH, vehicle age, battery capacity/chemistry factors, rolling exposure sums for high-temperature hours, fast-charge sessions, deep-discharge events, distance, energy, equivalent full cycles, and a 30-day SOH trend). The label is the actual simulated outcome 30 days later (soh_now - soh_future), not a second independently-estimated value — this is what makes the label trustworthy by construction (ADR-4)."),
  PB([bold("Leakage check: "), plain("every feature build asserts feature_window_end <= as_of_day before constructing a training sample, and this assertion is exercised by an automated test (test_leakage_check_trips_when_feature_window_extends_past_as_of_day) that deliberately tries to violate it and confirms the code catches it.")]),
  P("Resulting training set: 3,600 samples (2,880 train / 720 test, an 80/20 split)."),
  H2("Model / Design"),
  P("Baseline: a naive-persistence formula (no fitting) — assume the vehicle's recently observed degradation rate continues linearly for 30 more days. Model: scikit-learn's GradientBoostingRegressor (200 estimators, max depth 3, learning rate 0.05) on the 17-column engineered feature vector. No agent, no tool-calling, no external LLM call anywhere in the prediction path — a deliberate choice, since this is a structured regression problem, not a reasoning or retrieval task, and adding agentic complexity here would be unjustified cost without a corresponding benefit."),
  H2("Evaluation Against Baseline"),
  table(
    ["Metric", "Baseline", "Model", "Improvement"],
    [
      ["MAE", "0.0326", "0.0165", "~49% lower"],
      ["RMSE", "0.0542", "0.0252", "~53% lower"],
    ],
    [2400, 2300, 2300, 2350]
  ),
  P("beats_baseline: true, recorded directly in the saved model's metrics file (model_1790791779.metrics.json), not just asserted here."),
  H2("Risk Derivation — a Rule on Top of the Prediction, Not a Second Model"),
  P("predicted_degradation_30d_pct >= 0.45 -> HIGH; >= 0.20 -> MEDIUM; else LOW. These values are explicitly project/demo policy, not an industry standard, and — importantly — were not guessed once and left alone. The original values (1.0 / 2.5) were set before any real data existed and, once examined against the real prediction distribution from the actual training run, turned out to make every single prediction classify as LOW regardless of archetype (the maximum observed value for the worst-behaved archetype, BAD, was 0.94 — still under the old 1.0 MEDIUM threshold). The thresholds were recalibrated against the real observed distribution (HEALTHY avg 0.14, NORMAL avg 0.21, STRESSED avg 0.41/p90 0.67, BAD avg 0.46/p90 0.91), producing a real, usable spread after re-running prediction: LOW 112 (56%), MEDIUM 69 (34.5%), HIGH 19 (9.5%) across the 200-vehicle fleet. This correction is documented as ADR-6, not hidden."),
  H2("Guardrails & Cost"),
  bullet("Ground truth is deterministic and simulator-controlled — there is no hallucination risk of the kind an LLM-based component would carry."),
  bullet("Risk thresholds are explicitly declared as policy, not universal standards, both here and in the underlying code's own docstring."),
  bullet("Human-in-the-loop: predictions and alerts inform the fleet manager; no automated action is taken on a vehicle without a human decision — the dashboard surfaces risk, it does not act on it."),
  bullet("Cost/latency: training on this dataset size completes in seconds on commodity hardware; inference is a single scikit-learn predict() call per vehicle with no external API calls, so there is no per-prediction token or network cost to manage."),
  pageBreak(),
];

const s12 = [
  H1("12. Architecture Decisions, Risks & Future Enhancements"),
  H2("Architecture Decision Records"),
  P("Full records with Context -> Options Considered -> Decision -> Consequences: docs/ADRs.md. Summary:"),
  table(
    ["ADR", "Decision"],
    [
      ["ADR-1", "Polyglot storage: PostgreSQL (relational core) + MongoDB (raw telemetry) + Redis (latest-state cache), not a single database — write isolation between transactional and firehose workloads."],
      ["ADR-2", "No vector database in the MVP — the core decision is structured tabular regression, not retrieval/semantic search."],
      ["ADR-3", "Kafka with a plain in-process consumer, not Flink/Kafka Streams — much faster to build; documented trade-off is loss of in-flight state on a crash, fully recoverable via Kafka replay."],
      ["ADR-4", "The simulator is the sole ground truth; longitudinal history is derived from telemetry, not separately generated — guarantees internal consistency and trustworthy ML labels."],
      ["ADR-5", "Minimal JWT auth instead of full OAuth2/OIDC/mTLS — a declared, time-driven scope cut with a clean upgrade path."],
      ["ADR-6", "Risk thresholds recalibrated empirically from real prediction data, not set a priori — the original guessed thresholds made every prediction read LOW; real data fixed it."],
    ],
    [1100, 8250]
  ),
  H2("Risks & Technical Debt (declared, not hidden)"),
  bullet("No Flink/Spark: in-flight aggregation state can be lost on an ingestion crash between flush cycles; recoverable via Kafka replay, not instant (ADR-3)."),
  bullet("No OAuth2/OIDC/mTLS/secrets vault: minimal JWT + RBAC only (ADR-5, STRIDE §8)."),
  bullet("No per-tenant isolation: single-tenant MVP."),
  bullet("No database replication/HA: each datastore is a single instance; availability NFR is only partially met (§7)."),
  bullet("build_or_load_fleet() cannot grow an existing persisted fleet to a larger vehicle_count without first truncating the fleet tables — a real limitation discovered and documented during integration (dataGenMaster.md)."),
  bullet("A Docker/WSL2-level quirk observed during the chaos demo: docker inspect's RestartCount field read 0 despite a confirmed real restart (container StartedAt timestamp and functional recovery both indicate a restart occurred) — noted honestly in the chaos demo report rather than silently presenting a clean but possibly-misleading number."),
  bullet("SHORT_BURST 100K-vehicle throughput test and EXPLAIN ANALYZE query-optimization evidence: both have ready runbooks but were not executed before this document was drafted (§4, §5.3, §7, §9) — to be completed and the real numbers inserted before final submission."),
  H2("Future Enhancements (next three steps from prototype to pilot)"),
  bullet("1. Run the 100K-vehicle SHORT_BURST throughput test at full scale, and if the measured ceiling is below the 100K events/sec target, add horizontal consumer parallelism (multiple ingestion instances, one per subset of the 12 Kafka partitions already provisioned) to close the gap — the partitioning scheme already supports this without a schema change."),
  bullet("2. Add datastore replication/HA (Postgres streaming replica, MongoDB/Redis replica sets) and a real cloud deployment (Terraform + a managed Kubernetes service), since the codebase already has no cloud-specific SDK calls blocking this."),
  bullet("3. Upgrade authentication to OAuth2/OIDC with mTLS for inter-service traffic and a real secrets vault, following the clean upgrade path already identified in ADR-5 (swap JWT issuance without touching any require_role call site)."),
  pageBreak(),
];

const s13 = [
  H1("13. Demo Video (5 Minutes Maximum)"),
  pending("record and link the demo video. Suggested shot list, following the template's own timing breakdown and reusing the artifacts already produced in this project:"),
  table(
    ["Time", "Segment", "What to Show"],
    [
      ["0:00–0:30", "Problem", "A fleet manager only sees current SOC/SOH today, not forward risk — state the one number that proves it matters: every single vehicle reads LOW risk under the original, un-calibrated thresholds (a real finding from this project), motivating why a learned, calibrated prediction matters."],
      ["0:30–1:00", "Solution", "One-line pitch: predict 30-day battery degradation per vehicle, surface it as fleet-wide risk, from real connected-vehicle-style telemetry."],
      ["1:00–3:00", "Live Demo", "Log into the dashboard, show Fleet Overview (live), Vehicle Search -> Detail (pick a BAD-archetype vehicle), Risk & ML page showing the real 112/69/19 LOW/MEDIUM/HIGH split, trigger a live simulation on the Simulation Control page to show one real-time event flow end to end."],
      ["3:00–4:15", "Under the Hood", "Architecture diagram walkthrough; show the k6 load test result (p95 60.84ms) or the chaos demo recovering in 5.9s live on screen — both are real, already-produced artifacts, not re-enactments."],
      ["4:15–5:00", "Impact & Next Steps", "Restate the measured MAE improvement (49% over baseline), the declared next steps (§12), and introduce the team."],
    ],
    [1300, 1500, 6550]
  ),
  PB([bold("Video Link: "), plain("[URL — to be added once recorded]")]),
  pageBreak(),
];

const s14 = [
  H1("14. Repository Checklist"),
  table(
    ["Item", "Status"],
    [
      ["README: problem, architecture image, quick start, environment variables, test commands, known issues", "Done — README.md"],
      ["One-Command Run: docker compose up (with the data simulator)", "Done — docker compose up --build brings up the full 9-container stack"],
      ["Structure: clear service folders, plus /docs, /infra and /tests", "Done — services/{data-gen,ingestion,ml,dashboard}, docs/, infra/{k6,postgres}, test-evidence/"],
      ["CI Pipeline: build, lint, all test suites and security scans on every push", "Partial — .github/workflows/ci.yml runs all four services' pytest suites plus a frontend build on every push; no lint step and no security scan (Semgrep/ZAP/Trivy) wired in (declared gap, §9)"],
      ["Hygiene: no secrets committed, .env.example provided, commits from all members", "Partial — .env is gitignored and .env.example is committed with placeholder values only; commit history currently reflects one contributor's account (team commit history to be added as other members contribute)"],
      ["Final Tag: v1.0-submission", "PENDING — to be applied once the remaining evidence (§4, §7) is complete and the document is finalized"],
    ],
    [3700, 5650]
  ),
  pageBreak(),
];

const s15 = [
  H1("15. Conclusion"),
  H2("Summary of Key Learnings"),
  P("The most important lesson from building this project was how many real defects only surfaced once the system was run against real data and real load, not when it was merely designed or code-reviewed: a timezone bug that only appeared on a fleet's second simulation run (not its first), a threading race condition in the aggregation pipeline that only manifested under concurrent load, a throughput bottleneck that only became visible once consumer lag was actually measured climbing, and — most consequentially — a risk-classification feature that silently classified every single vehicle as LOW risk until the real prediction distribution was examined. Each of these was found, diagnosed, fixed, and documented rather than left unexamined, which is itself the point: a system that looks correct on paper and a system verified against real behavior are not the same thing."),
  H2("Strengths of the Solution"),
  bullet("A complete, working system end to end — not slides describing an architecture, but a running four-service platform with real data flowing through it, verified by 90 passing automated tests plus a load test and a chaos test, both with real measured numbers."),
  bullet("Honest documentation throughout — every scope cut, every limitation, and every correction made along the way is written down (ADRs, STRIDE gaps, this conclusion itself), rather than presented as if everything was correct from the first attempt."),
  bullet("A genuinely parallelizable, contract-first build process that was actually used, across two physical machines, with a clean merge and a real cross-module bug caught during specification review alone."),
  H2("Challenges Tackled and Solved"),
  bullet("Coordinating database state across two physical machines (via Postgres dumps and a documented restore procedure) while a long-running data-generation job executed on one of them."),
  bullet("Getting Docker and Kafka operational semantics right under real conditions: a Kafka image tag disappearing mid-build, a KRaft cluster-ID format requirement, topic partition counts needing to be set explicitly rather than relying on auto-create defaults, and the specific difference between an externally-issued docker kill (correctly not auto-restarted) and an internal process crash (correctly auto-restarted) under a restart: unless-stopped policy."),
  bullet("Calibrating a machine-learning risk feature with no prior real-world reference point to calibrate against — solved by generating real data first and calibrating against its actual observed distribution, rather than guessing and leaving the guess unverified."),
  pageBreak(),
];

const s16 = [
  H1("16. Declarations"),
  P("Full text: docs/DECLARATIONS.md. Summary below."),
  H2("Open-Source Components"),
  P("FastAPI, SQLAlchemy, psycopg2, pymongo, redis-py, confluent-kafka, pybloom-live, scikit-learn, pandas, numpy, pytest (MIT/BSD/Apache-2.0-licensed Python libraries); React, Vite, Recharts, nginx (MIT/BSD-licensed frontend/serving); PostgreSQL, MongoDB, Redis, Apache Kafka (core infrastructure, respective open-source licenses); k6 (Grafana, AGPL 3.0, used as an external load-testing tool, not linked into the codebase). None were modified from their published form. Full exact versions can be generated on demand via pip freeze / npm list for an exact SBOM."),
  H2("AI Tools Used"),
  P("Claude (Anthropic) was used extensively and substantively: architecture design and all module specification documents, code generation across all four services, debugging and fixing real defects found during integration (see §12, §15), merging code across two development machines, running and verifying all automated tests/load test/chaos demo, and drafting this documentation set including this Solution Document. This was an iterative process involving real bugs found against real data and real load, documented transparently rather than presented as flawless on the first attempt."),
  H2("Data"),
  P("All vehicle, telemetry, battery, and fleet data anywhere in this project is synthetically generated by this project's own simulator, seeded deterministically for reproducibility. No real vehicle, driver, or owner data of any kind was used. No real personal data is processed, stored, or displayed anywhere in the system."),
  pageBreak(),
];

const s17 = [
  H1("17. Appendix"),
  H2("Additional diagrams"),
  P("Architecture diagram and ER diagram are included inline in §5.1 and §5.3 respectively; source HTML files for both are kept alongside the rendered PNGs at docs/diagrams/ for re-rendering if the architecture changes."),
  H2("Full technical documentation"),
  P("The complete, detailed specification this project was built from is kept in docs/: CONTRACT.md (the shared schema/contract all four services were built against), dataGenMaster.md, ingestionMaster.md, mlMaster.md, dashboardMaster.md (one detailed specification per service), INTEGRATION.md (build order, docker-compose.yml, cross-module integration notes), ADRs.md, STRIDE_threat_model.md, and DECLARATIONS.md. A reusable, project-agnostic write-up of the contract-first development methodology itself is kept at inital notes/ModularAIBuildMethodology.md."),
  H2("Full test logs and raw evidence"),
  P("test-evidence/ contains: k6_load_test_results.md (full k6 summary), chaos_demo.ps1 (the reusable, automated chaos-test script) and its generated timestamped report, and SHORT_BURST_100k_throughput_test.md (the runbook for the still-pending 100K-vehicle throughput measurement)."),
  P("TODO.md tracks every remaining item at the time of this document's drafting, for full transparency about what is and is not yet complete."),
];

module.exports = { s11, s12, s13, s14, s15, s16, s17 };
