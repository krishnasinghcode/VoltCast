# Remaining Work

## Test evidence
- [x] `SHORT_BURST` 100K-vehicle throughput test — done, real results in
      `test-evidence/SHORT_BURST_100k_throughput_results_20261001.md`. Honest finding: production ~17,856
      events/sec, ingestion only ~1,897 events/sec, lag growing (NFR not met at this scale) — written up in
      the Solution Document with the real numbers and the scaling path, not hidden.
- [ ] **EXPLAIN ANALYZE before/after** — SQL optimization evidence for the Solution Document's §5.3/§6.5.
      Full runbook ready at `test-evidence/EXPLAIN_ANALYZE_query_optimization.md`.

## Bugs fixed this session
- [x] `predict_all_vehicles()` hang/timeout — was looping over every row in `vehicles` with no filtering;
      against a leftover 100K-vehicle fleet with near-zero history this exceeded the proxy timeout and
      looked like a frontend error. Fixed in `services/ml/src/predict.py`.
- [x] Frontend usability — added hover/tap "i" tooltips across every page (Fleet Overview, Vehicle
      Search/Detail, Risk & ML, Alerts, Observability, Simulation Control, SQL Sandbox) explaining what each
      stat/chart/button does and why it might show empty.
- [ ] **You still need to rebuild + restore data** (see the commands already given) before recording the
      demo video, so the dashboard reflects the fixed ML service and your real trained dataset, not the
      leftover SHORT_BURST test data.

## Documentation
- [x] Architecture diagram + ER diagram — `docs/diagrams/architecture.png`, `docs/diagrams/er_diagram.png`
- [x] 3-5 ADRs — `docs/ADRs.md` (6 written)
- [x] STRIDE threat model write-up — `docs/STRIDE_threat_model.md`
- [x] Declarations section — `docs/DECLARATIONS.md`
- [x] README — `README.md` (quick start, frontend dev mode, day-to-day ops, troubleshooting)
- [x] The Solution Document — `Motorq_Hackathon_Solution_Document_CVI.docx`, 17 sections, now includes the
      real SHORT_BURST results and the ML bug fix. Only the EXPLAIN ANALYZE evidence remains [PENDING].

## Submission mechanics
- [ ] Demo video (≤5 min) — script ready at `test-evidence/DEMO_VIDEO_SCRIPT.md` (untracked, local only),
      updated to reflect the real throughput finding and the new tooltips
- [ ] Final commit tag `v1.0-submission`
- [ ] Fill in team name/members/emails, repo URL, video link in the Solution Document's cover page and §13
- [ ] Confirm whether real cloud hosting is required, or repo + local demo + video is sufficient
