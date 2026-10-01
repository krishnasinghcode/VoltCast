# SHORT_BURST 100K-Vehicle Throughput Test — Results

Date: 2026-10-01  
Simulation ID: `6fc8324e-3183-4ecb-8d6f-a836cc3971f6`  
Mode: `SHORT_BURST`  
Vehicle count: 100,000  
Status after measurement: `PAUSED`

## Initial Snapshot

This snapshot was taken at the first pause, before the simulation was resumed for a measured interval.

| Measurement | Result |
|---|---:|
| Events produced (simulation API) | 2,314,187 |
| Events consumed total (ingestion metrics) | 1,040,444 |
| Consumer lag | 1,279,201 |
| Events rejected total | 5,231 |
| Events deduplicated total | 8,737 |
| API `elapsed_seconds` at retrieval | 518.6 s |
| Current simulation time at retrieval | 2026-12-01T00:00:23+00:00 |

## Measured Interval After Resume

Two snapshots were captured while the simulation was running. They provide one valid measured interval:

| Sample | Time (UTC) | Events produced | Events consumed total | Consumer lag |
|---|---|---:|---:|---:|
| Start | 2026-10-01T08:58:14.9704814+00:00 | 2,617,377 | 2,346,275 | 101,691 |
| End | 2026-10-01T08:59:22.8316876+00:00 | 3,829,081 | 2,475,024 | 1,214,261 |

| Derived result | Value |
|---|---:|
| Measurement window | 67.861 seconds |
| Production throughput | approximately **17,856 events/sec** |
| Ingestion throughput | approximately **1,897 events/sec** |
| Consumer lag change | **+1,112,570 events** |

Consumer metrics are in-process cumulative counters, but using counter deltas between these two snapshots gives an interval rate without requiring the counter to start at zero. Lag grew sharply during the interval, so ingestion was not keeping up with production.

## Configuration and Limitations

The runbook configured a 60-second burst targeting 100,000 events/sec. The simulation was manually paused; the configured burst duration is not itself an automatic stop condition. After resuming, one measured interval was captured. The simulation was subsequently paused with 5,545,659 events produced; no second measured interval was captured.

This is evidence of a 100,000-vehicle run and a large production/consumption gap, not evidence that the 100,000 events/sec NFR was met. Host specifications and a second interval to check repeatability were not recorded.

## Cleanup / Restore

Cleanup and any Postgres backup restoration were not verified as part of this report. Follow step 8 in `SHORT_BURST_100k_throughput_test.md` if restoring the pre-test database backup or removing raw Mongo telemetry.
