import { useEffect, useRef, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { Observability } from "../api/types";
import { Badge, Card, ErrorBanner, fmt, InfoTip, Spinner, StatCard } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface Point {
  t: string;
  eventsPerSec: number | null;
  lag: number | null;
  p95: number;
  avg: number;
}

const POLL_MS = 3000;
const MAX_POINTS = 60;

export default function ObservabilityPage() {
  const obs = useApi(() => api.get<Observability>("/observability"), [], POLL_MS);
  const [series, setSeries] = useState<Point[]>([]);
  const prev = useRef<{ consumed: number; at: number } | null>(null);

  // Turn cumulative counters into a rolling events/sec series, client-side.
  useEffect(() => {
    const d = obs.data;
    if (!d) return;
    const now = Date.now();
    const consumed = d.ingestion.events_consumed_total;
    let rate: number | null = null;
    if (consumed !== null && prev.current && now > prev.current.at) {
      rate = Math.max(0, (consumed - prev.current.consumed) / ((now - prev.current.at) / 1000));
    }
    if (consumed !== null) prev.current = { consumed, at: now };
    setSeries((s) =>
      [
        ...s,
        {
          t: new Date(now).toLocaleTimeString(),
          eventsPerSec: rate,
          lag: d.ingestion.consumer_lag,
          p95: d.api.p95_latency_ms,
          avg: d.api.avg_latency_ms,
        },
      ].slice(-MAX_POINTS),
    );
  }, [obs.data]);

  const d = obs.data;
  const last = series[series.length - 1];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Observability</h1>
          <p className="muted">Ingestion pipeline + API health. Polls /api/v1/observability every {POLL_MS / 1000} s.</p>
        </div>
      </div>
      <ErrorBanner error={obs.error} onRetry={obs.reload} />
      {!d && obs.loading && <Spinner />}
      {d && (
        <>
          <h3 className="section-title">
            Ingestion {!d.ingestion.reachable && <Badge kind="critical">unreachable</Badge>}
            <InfoTip text="Live counters from the Kafka-consuming ingestion service. These are cumulative totals since that service last started, not per-second rates." />
          </h3>
          <div className="stats-grid">
            <StatCard label="Events consumed" info="Total telemetry events successfully processed since ingestion last started." value={fmt.int(d.ingestion.events_consumed_total)} sub={last?.eventsPerSec != null ? `${fmt.int(last.eventsPerSec)} ev/s` : "—"} />
            <StatCard label="Deduplicated" info="Events dropped because they were already seen (Bloom filter + database unique-index backstop) — expected to be non-zero, not an error." value={fmt.int(d.ingestion.events_deduplicated_total)} sub="dropped as duplicates" />
            <StatCard label="Rejected" info="Events that failed schema/range validation and were discarded rather than corrupting downstream data. A small nonzero number is expected (deliberately injected malformed test events)." value={fmt.int(d.ingestion.events_rejected_total)} sub="failed validation" tone={(d.ingestion.events_rejected_total ?? 0) > 0 ? "warn" : undefined} />
            <StatCard label="Late events" info="Events that arrived out of order relative to already-processed ones, still handled correctly rather than crashing." value={fmt.int(d.ingestion.late_events_total)} sub="out of order" />
            <StatCard label="Consumer lag" info="How many produced events are still waiting to be consumed. A high but shrinking number means it's catching up; a number that keeps growing means production is outpacing consumption." value={fmt.int(d.ingestion.consumer_lag)} sub="messages behind" tone={(d.ingestion.consumer_lag ?? 0) > 10000 ? "warn" : undefined} />
          </div>

          <h3 className="section-title">
            API
            <InfoTip text="Request counters and latency for this dashboard's own backend API, tracked since it last started." />
          </h3>
          <div className="stats-grid">
            <StatCard label="Requests" value={fmt.int(d.api.requests_total)} sub={`up ${fmt.duration(d.api.uptime_s)}`} />
            <StatCard label="Errors (5xx)" info="Server-side errors (the API's own fault), not 4xx client errors like a bad request or missing auth." value={fmt.int(d.api.errors_total)} sub={`${(d.api.error_rate * 100).toFixed(2)}% error rate`} tone={d.api.errors_total > 0 ? "warn" : "good"} />
            <StatCard label="Avg latency" value={`${fmt.num(d.api.avg_latency_ms)} ms`} />
            <StatCard label="p95 latency" info="95% of requests complete faster than this. A better single number than the average for judging real user experience, since it isn't skewed by a few very fast requests." value={`${fmt.num(d.api.p95_latency_ms)} ms`} sub={`p99 ${fmt.num(d.api.p99_latency_ms)} ms`} tone={d.api.p95_latency_ms > 200 ? "warn" : "good"} />
            <StatCard label="Rate limited" info="Requests rejected with 429 because a user exceeded their per-endpoint request limit." value={fmt.int(d.api.rate_limited_total)} sub={`${fmt.int(d.api.client_errors_total)} total 4xx`} />
          </div>

          <div className="grid-2">
            <Card title="Throughput & lag" info="Live rolling chart built client-side from repeated polls of /observability — events/sec is derived from the change in the cumulative counter above between polls.">
              <MiniChart data={series} lines={[
                { key: "eventsPerSec", name: "events/s", color: "var(--accent)" },
                { key: "lag", name: "consumer lag", color: "var(--risk-medium)" },
              ]} />
            </Card>
            <Card title="API latency (ms)" info="Rolling average and p95 response time for this API, sampled every few seconds while this page is open.">
              <MiniChart data={series} lines={[
                { key: "avg", name: "avg", color: "var(--accent)" },
                { key: "p95", name: "p95", color: "var(--risk-high)" },
              ]} />
            </Card>
          </div>

          <Card title="Services" info="Health check result for each backend service this dashboard depends on, including whether its contract_version matches — a mismatch means two services were built from different versions of the shared schema and may disagree about data shapes.">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Status</th>
                    <th className="num">Health latency</th>
                    <th>Contract</th>
                  </tr>
                </thead>
                <tbody>
                  {d.services.map((s) => (
                    <tr key={s.name}>
                      <td>{s.name}</td>
                      <td><Badge kind={s.status === "ok" ? "risk-low" : "critical"}>{s.status}</Badge>{s.error && <span className="muted small"> {s.error}</span>}</td>
                      <td className="num">{s.latency_ms !== undefined ? `${fmt.num(s.latency_ms)} ms` : "—"}</td>
                      <td>
                        {s.contract_version === undefined ? "—" : s.contract_ok ? (
                          <span className="mono">{s.contract_version}</span>
                        ) : (
                          <Badge kind="critical">mismatch {s.contract_version ?? "?"}</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted small">
              Fleet: {fmt.int(d.fleet.vehicle_count)} vehicles · {fmt.int(d.fleet.active_alerts)} open alerts · updated {fmt.date(d.generated_at)}
            </p>
          </Card>
        </>
      )}
    </div>
  );
}

function MiniChart({ data, lines }: { data: Point[]; lines: { key: keyof Point; name: string; color: string }[] }) {
  if (data.length < 2) return <p className="muted small">Collecting samples…</p>;
  return (
    <div className="chart-box">
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
          <XAxis dataKey="t" tick={{ fill: "var(--muted)", fontSize: 11 }} minTickGap={40} />
          <YAxis tick={{ fill: "var(--muted)", fontSize: 11 }} width={56} />
          <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }} />
          <Legend />
          {lines.map((l) => (
            <Line key={l.key} type="monotone" dataKey={l.key} name={l.name} stroke={l.color} dot={false} strokeWidth={2} isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
