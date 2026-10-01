# Remaining Work

## Test evidence
- [ ] `SHORT_BURST` 100K-vehicle throughput test — runbook ready at
      `test-evidence/SHORT_BURST_100k_throughput_test.md`, to be run on the other (beefier) laptop.
- [ ] **EXPLAIN ANALYZE before/after** — SQL optimization evidence for the Solution Document's §5.3/§6.5.
      Full runbook ready at `test-evidence/EXPLAIN_ANALYZE_query_optimization.md`, to be run on the other
      laptop (can be combined with the SHORT_BURST test in the same session — see that file's §0).

## Documentation
- [x] Architecture diagram + ER diagram — `docs/diagrams/architecture.png`, `docs/diagrams/er_diagram.png`
- [x] 3-5 ADRs — `docs/ADRs.md` (6 written)
- [x] STRIDE threat model write-up — `docs/STRIDE_threat_model.md`
- [x] Declarations section — `docs/DECLARATIONS.md`
- [x] README — `README.md` (quick start, frontend dev mode, day-to-day ops, troubleshooting)
- [x] The Solution Document — `Motorq_Hackathon_Solution_Document_CVI.docx`, 17 sections — once the two
      pending test-evidence items above are done, send the real numbers back to drop into §4/§5.3/§7/§9/§12
      and regenerate (`submission/assemble.js`).

## Submission mechanics
- [ ] Demo video (≤5 min) — script ready at `test-evidence/DEMO_VIDEO_SCRIPT.md` (untracked, local only)
- [ ] Final commit tag `v1.0-submission`
- [ ] Fill in team name/members/emails, repo URL, video link in the Solution Document's cover page and §13
- [ ] Confirm whether real cloud hosting is required, or repo + local demo + video is sufficient
