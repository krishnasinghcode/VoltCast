# Remaining Work

## Test evidence
- [ ] `SHORT_BURST` 100K-vehicle throughput test — runbook ready at
      `test-evidence/SHORT_BURST_100k_throughput_test.md`, to be run on the other (beefier) laptop.
- [ ] **EXPLAIN ANALYZE before/after on the slowest dashboard query** — SQL optimization evidence for the
      Solution Document's Algorithms & SQL section. Not started. Likely candidate: the vehicle search
      endpoint (`GET /vehicles?search=...`) or the `agg_daily` date-range history query — pick whichever is
      actually slowest against the real (post-restore) dataset, run `EXPLAIN ANALYZE` before adding an index,
      add a composite/partial index, run it again, keep both query plans as the before/after evidence.

## Documentation
- [x] Architecture diagram + ER diagram — `docs/diagrams/architecture.png`, `docs/diagrams/er_diagram.png`
- [x] 3-5 ADRs — `docs/ADRs.md` (6 written)
- [x] STRIDE threat model write-up — `docs/STRIDE_threat_model.md`
- [x] Declarations section — `docs/DECLARATIONS.md`
- [x] README — `README.md`
- [ ] The Solution Document itself (17 sections, using the provided template) — next up

## Submission mechanics
- [ ] Demo video (≤5 min)
- [ ] Final commit tag `v1.0-submission`
- [ ] Confirm whether real cloud hosting is required, or repo + local demo + video is sufficient
