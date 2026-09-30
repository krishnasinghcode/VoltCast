# k6 API Load Test Results

Run date: 2026-10-01
Script: `infra/k6/burst_load_test.js`
Target: `api` service (dashboardMaster.md §15) — read endpoints under 200 concurrent virtual users
Config: ramp 0→200 VUs over 30s, hold 60s, ramp down 15s (`RATE_LIMIT_PER_MINUTE` temporarily raised to
avoid throttling the load test itself, restored to the normal 120/min default afterward)

## Result: PASSED both thresholds, comfortably

```
THRESHOLDS

checks{kind:read}
 'rate>0.99' rate=100.00%

http_req_duration{kind:read}
 'p(95)<200' p(95)=60.84ms
 'p(99)<500' p(99)=93.4ms
```

## Full summary

```
checks_total.......: 16203   152.427935/s
checks_succeeded...: 100.00% 16203 out of 16203
checks_failed......: 0.00%   0 out of 16203

HTTP
http_req_duration..............: avg=20.13ms min=2.4ms    med=12.43ms p(90)=45.1ms  p(95)=60.86ms p(99)=93.5ms max=364.1ms
  { kind:read }................: avg=20.11ms min=2.4ms    med=12.42ms p(90)=45.07ms p(95)=60.84ms p(99)=93.4ms max=179.48ms
http_req_failed................: 0.00%  0 out of 16206
http_reqs......................: 16206  152.456157/s

EXECUTION
iterations.....................: 16203  152.427935/s
vus_max........................: 200

NETWORK
data_received..................: 89 MB  836 kB/s
data_sent......................: 6.1 MB 58 kB/s
```

## Interpretation for the Solution Document's NFR table

| NFR | Target | Achieved |
|---|---|---|
| API Latency | p95 < 200ms, p99 < 500ms | p95 = **60.84ms**, p99 = **93.4ms** — 3x and 5x better than target |

16,203 requests across a realistic weighted mix (vehicle detail 35%, vehicle search 25%, fleet summary 20%,
alerts 20%) at up to 200 concurrent simulated dashboard users, 100% success rate, zero failed requests.

This measures **API read latency only**. Ingest throughput (the separate 100,000 events/sec NFR) is proven
independently via `data-gen`'s `SHORT_BURST` mode + `ingestion`'s `/metrics` — not by this test. See the
`SHORT_BURST` throughput test results (once run) for that number.
