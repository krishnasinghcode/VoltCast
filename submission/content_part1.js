const { HeadingLevel, Paragraph } = require("docx");
const { H1, H2, H3, P, PB, bold, ital, plain, bullet, pending, note, pageBreak, table, image } = require("./helpers");

const cover = [
  new Paragraph({ text: "Connected Vehicle Intelligence Hackathon", heading: HeadingLevel.TITLE, spacing: { after: 80 } }),
  new Paragraph({ text: "Solution Document", heading: HeadingLevel.HEADING_1, spacing: { after: 300 } }),
  P("Submission Format: PDF (export of this document)"),
  PB([bold("Team Name: "), plain("[TEAM NAME]")]),
  PB([bold("Team Members & Roles: "), plain("[Name – role – email, for each member]")]),
  PB([bold("Problem Space Chosen: "), plain("EV Charging and Battery Health — 30-day battery degradation prediction for connected-vehicle fleets")]),
  PB([bold("Repository URL: "), plain("[REPO URL]")]),
  PB([bold("Demo Video URL (≤ 5 min): "), plain("[VIDEO URL — see §13]")]),
  PB([bold("Date of Submission: "), plain("[DATE]")]),
  pageBreak(),
];

const toc = [
  H1("Table of Contents"),
  ...[
    "1. Executive Summary",
    "2. Problem Statement & Validation",
    "3. Solution Description",
    "4. Feature List",
    "5. Solution Architecture (High-Level Design)",
    "6. Low-Level Design",
    "7. Non-Functional Requirements & Performance Benchmarks",
    "8. Security & Compliance",
    "9. Test Strategy",
    "10. Observability",
    "11. AI / ML Component",
    "12. Architecture Decisions, Risks & Future Enhancements",
    "13. Demo Video",
    "14. Repository Checklist",
    "15. Conclusion",
    "16. Declarations",
    "17. Appendix",
  ].map((t) => bullet(t)),
  pageBreak(),
];

const s1 = [
  H1("1. Executive Summary"),
  P("Connected fleets today expose current battery state (state of charge, state of health) but almost nothing about where a battery is headed. Fleet managers, lenders monitoring vehicle collateral, and OEMs offering battery warranties are all forced into reactive maintenance — they find out a battery has degraded badly only after it already has. This project builds a platform that answers a genuinely forward-looking question: given a vehicle's recent connected telemetry, how much will its battery degrade over the next 30 days, and which vehicles in a fleet need attention now."),
  P("The solution is a full connected-vehicle data platform, not a model alone: a deterministic 100,000-vehicle simulator generates realistic telemetry (bounded-step physics, archetype-driven fleet composition, not i.i.d. noise); a Kafka-backed ingestion pipeline deduplicates, validates, and aggregates that telemetry into a polyglot store (PostgreSQL relational core, MongoDB raw firehose, Redis latest-state cache); a gradient-boosted regression model predicts 30-day battery degradation from engineered rolling features; and a JWT-secured, rate-limited dashboard with a guarded SQL sandbox surfaces live fleet state, risk-ranked vehicle lists, and real-time alerts."),
  H2("Key results achieved"),
  bullet("ML model beats a naive-persistence baseline by ~49% on held-out data: MAE 0.0165 vs baseline 0.0326 (RMSE 0.0252 vs 0.0542), on a real 200-vehicle/90-day simulated run (§11)."),
  bullet("API read latency, measured under 200 concurrent simulated dashboard users (k6, 16,203 requests): p95 = 60.84 ms, p99 = 93.4 ms — 3x and 5x better than the 200 ms / 500 ms targets, 100% success rate (§7)."),
  bullet("90 automated tests passing across all four services (data-gen 8, ingestion 24, ml 11, api 47) — verified, not just claimed (§9)."),
  bullet("Chaos/resilience test: an internal process crash in the ingestion service recovered automatically in 5.9 seconds with zero data loss (events_consumed_total continued climbing through the crash) (§9)."),
  bullet("A fully reproducible, deterministic data simulator — the same seed always produces the same fleet and the same telemetry, which is what makes the ML labels trustworthy (§11, ADR-4)."),
  H2("What is unique about this solution"),
  bullet("Telemetry realism by construction: every continuous field evolves by a bounded step toward a physically-motivated target each tick (a control-systems convergence pattern), not by independent random sampling — enforced by an automated test, not just a design intent."),
  bullet("A population-level realism technique: vehicles are assigned an archetype (Healthy/Normal/Stressed/Bad) at creation that biases their stress exposure, producing a realistic, right-skewed fleet distribution that is neither trivially separable nor flat/unlearnable — and this was verified directly against real data, not assumed."),
  bullet("An honestly-documented empirical correction: the risk thresholds were originally set before any real data existed, and were found — once real predictions were examined — to make every single vehicle read LOW regardless of archetype. They were recalibrated against the real predicted-value distribution. This correction, and several others like it (a timezone bug, a threading race condition, a throughput bottleneck), are documented transparently in the Architecture Decision Records (§12) rather than hidden."),
  bullet("A guarded, defense-in-depth SQL sandbox: a comment- and string-literal-aware SQL parser (not a naive keyword grep), a genuinely separate read-only Postgres role with table-level revokes, a hard statement timeout, and an always-rolled-back transaction — verified by automated tests including SQL-injection-style bypass attempts."),
  pageBreak(),
];

const s2 = [
  H1("2. Problem Statement & Validation"),
  H2("2.1 Problem Statement"),
  P("Fleet managers operating electric vehicle fleets need a way to know which vehicles' batteries are likely to degrade significantly in the near future, because today's connected-vehicle tooling only exposes current battery state (SOC/SOH), not forward-looking risk — which today costs them reactive, unplanned maintenance, unexpected vehicle downtime, and a higher total cost of ownership from batteries that are run to failure rather than proactively managed."),
  PB([bold("Primary User: "), plain("Fleet manager operating a mixed or fully-electric commercial fleet.")]),
  PB([bold("Secondary Stakeholders: "), plain("Lenders monitoring vehicle collateral health; OEMs managing battery warranty exposure; sustainability teams tracking premature battery replacement/e-waste.")]),
  H2("2.2 Evidence & Validation"),
  table(
    ["Evidence / Assumption", "Source or Method", "What It Shows", "Confidence"],
    [
      ["Connected vehicles continuously emit SOC, voltage, current, temperature and charging telemetry", "Industry context supplied in the problem statement (Motorq: 250B+ signals/month across 500K+ vehicles)", "The raw signal needed to assess battery health is already routinely collected in this industry", "High"],
      ["Battery degradation is driven by measurable stress factors — calendar age, cycle count, thermal exposure, fast-charging frequency, deep-discharge frequency", "Established battery engineering principles (calendar + cycle aging models), implemented as this project's synthetic degradation formula (CONTRACT.md §9)", "A forward degradation prediction is plausible in principle from telemetry-derived features, not just current SOH", "High (well-established domain assumption; not independently re-derived from first principles here)"],
      ["A model trained on these features can predict 30-day degradation meaningfully better than a naive baseline", "Direct simulation + held-out evaluation on a real 200-vehicle/90-day run (this project's own experiment)", "MAE 0.0165 vs baseline 0.0326 — a genuine, measured improvement, not an assumption", "Medium-High for the synthetic setting; real-world transfer would need calibration against actual fleet telemetry"],
      ["Existing OEM portals and aftermarket tools show current state and operational flags (e.g. idling, Motorq Fuse-style alerts) but not a forward battery-degradation prediction", "Problem statement's own description of Motorq's product positioning and the broader OEM-portal category", "This project's core decision (30-day degradation forecast) is not already solved by the referenced tools", "Medium (based on supplied case-study material, not independent market research)"],
    ],
    [2600, 2300, 2500, 1950]
  ),
  H2("2.3 Impact & Success Metrics"),
  table(
    ["Metric", "Baseline Today", "Target", "How Measured / Estimated"],
    [
      ["Forward visibility into battery risk", "None — only current SOC/SOH available, no predictive signal", "Every vehicle classified LOW/MEDIUM/HIGH risk 30 days ahead", "Model MAE 0.0165 vs naive-persistence baseline MAE 0.0326 (49% improvement) on held-out data"],
      ["Proactive vs reactive maintenance scheduling", "Reactive (fix after failure/complaint)", "High-risk vehicles flagged via dashboard + alert before failure", "battery_predictions + alerts tables populated automatically on each prediction run; verified via dashboard Risk page"],
    ],
    [2600, 2300, 2500, 1950]
  ),
  P("Scale of impact: the architecture is designed to operate at 100,000 vehicles (the persistent fleet registry always generates at the full configured size regardless of which mode is used to build ML training history). The 30-day-degradation decision is per-vehicle and does not degrade in quality as fleet size grows; the throughput and latency NFRs (§7) are what actually gate how large a fleet this deployment can serve in real time."),
  P("Wider impact: earlier detection of degrading batteries supports safer operation (reduced risk of unexpected range loss or thermal incidents), lower fleet operating cost (planned rather than emergency battery service), and an environmental benefit (extending useful battery life reduces premature replacement and the associated raw-material/manufacturing footprint)."),
  pageBreak(),
];

const s3 = [
  H1("3. Solution Description"),
  H2("3.1 Solution Overview & User Journey"),
  P("What it does: the platform ingests simulated connected-vehicle telemetry at scale, maintains live per-vehicle state, derives a 30-day battery-degradation prediction per vehicle, classifies that prediction into a LOW/MEDIUM/HIGH risk level, and surfaces all of it — live fleet state, a searchable vehicle registry, real-time and ML-driven alerts, and risk-ranked lists — through a secured web dashboard."),
  H3("User journey"),
  bullet("A vehicle emits a telemetry event (SOC, voltage, current, temperature, charging state, odometer) -> published to Kafka."),
  bullet("The ingestion service deduplicates it (Bloom filter + a durable Mongo unique-index backstop), validates it against the contract's field ranges, and fans it out: raw copy to MongoDB, latest state to Redis, and folds it into an in-memory 5-minute/hourly/daily aggregation window."),
  bullet("On a periodic flush, closed aggregation windows are written to PostgreSQL — this is the feature source for the ML model."),
  bullet("On a training/prediction run, the ML service builds leakage-safe rolling features from those aggregates, predicts 30-day degradation with a trained model, and derives a risk level from a rule on top of that prediction."),
  bullet("A fleet manager logs into the dashboard, sees the live fleet overview, opens the Risk & ML page to see which vehicles are HIGH risk, drills into a specific vehicle's history and prediction, and acknowledges any open alert once handled."),
  H3("Screenshot"),
  P("The Fleet Overview page, showing real simulated fleet data (500 vehicles, live average SOH/SOC, open alerts, and archetype breakdown), confirmed working end-to-end during development. The architecture diagram is shown below as this section's illustration; a live dashboard screenshot and walkthrough are captured in the demo video (§13)."),
  image("docs/diagrams/architecture.png", 560, 314),
  H2("3.2 Key Value Proposition"),
  table(
    ["Aspect", "Detail"],
    [
      ["Customer Job", "Know which vehicles in the fleet need battery attention before they fail or degrade to the point of costly replacement."],
      ["Pain Relieved", "Reactive-only maintenance driven by current-state-only tooling; no advance warning of degrading vehicles."],
      ["Gain Created", "A prioritized, risk-ranked view of the whole fleet's future battery health, 30 days ahead, refreshed on demand."],
      ["Differentiation", "Purpose-built forward-looking degradation prediction (not an operational-anomaly flag like idling detection); fully open, self-hosted architecture with no vendor lock-in and genuine cloud portability (no cloud-specific SDK calls anywhere in the codebase)."],
    ],
    [2400, 6950]
  ),
  H2("3.3 Innovative Ideas"),
  H3("1. Physically-motivated, bounded-step telemetry generation"),
  P("Every continuous telemetry field (SOC, voltage, current, temperature, speed) evolves each simulated tick by a small bounded step toward a physically-motivated target, never by independent random sampling — the same mathematical pattern used in control-systems convergence. This is what prevents the classic synthetic-data failure mode (a field jumping incoherently between ticks) and is verified by an automated test (test_bounded_steps_driving_then_charging, part of the passing data-gen suite), not just claimed as a design intent."),
  H3("2. Archetype-driven, population-level realism"),
  P("Rather than generating a uniform or purely random fleet, each vehicle is assigned an archetype (HEALTHY/NORMAL/STRESSED/BAD) at creation, biasing its stress-exposure behavior (thermal, fast-charging, deep-discharge frequency). This produces a realistic, right-skewed fleet distribution — confirmed directly against real generated data: minimum observed SOH ordered exactly as expected across archetypes (HEALTHY 95.65% -> NORMAL 94.56% -> STRESSED 94.11% -> BAD 93.2%) — strong enough signal for the ML model to learn, without being a trivially-separable four-cluster toy dataset."),
  H3("3. A contract-first, independently-parallelizable build process"),
  P("The entire platform was specified before any code was written as one shared machine-readable contract (CONTRACT.md: schemas, ports, event formats) plus four independent module specifications, explicitly designed so each of the four services could be built by a separate development session — including literally on two different physical machines — without shared context. This was executed for real, not just planned: the four services were built independently and merged cleanly, and the process caught a genuine cross-module bug in specification review alone (an inconsistency in which table the ML service should read historical battery state from) before any conflicting code was ever written."),
  pageBreak(),
];

module.exports = { cover, toc, s1, s2, s3 };
