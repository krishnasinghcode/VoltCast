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
- [ ] Architecture diagram + ER diagram
- [ ] 3-5 ADRs (content already exists in `docs/INTEGRATION.md` §5, needs formatting)
- [ ] STRIDE threat model write-up (sketched in `docs/INTEGRATION.md` §6)
- [ ] Declarations section (AI tools used, open-source licenses/SBOM, confirm synthetic-data-only)
- [ ] README polish (one-command run, env vars, test commands, known issues)
- [ ] The Solution Document itself (17 sections, using the provided template)

## Submission mechanics
- [ ] Demo video (≤5 min)
- [ ] Final commit tag `v1.0-submission`
- [ ] Confirm whether real cloud hosting is required, or repo + local demo + video is sufficient
