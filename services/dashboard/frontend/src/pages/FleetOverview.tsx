import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import type { Fleet, FleetSummary, Page } from "../api/types";
import { Badge, Card, Empty, ErrorBanner, fmt, Spinner, StatCard } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useLiveSocket } from "../hooks/useLiveSocket";

const RISK_COLORS: Record<string, string> = {
  HIGH: "var(--risk-high)",
  MEDIUM: "var(--risk-medium)",
  LOW: "var(--risk-low)",
  UNPREDICTED: "var(--muted-fill)",
};
const ARCH_COLORS: Record<string, string> = {
  HEALTHY: "var(--risk-low)",
  NORMAL: "var(--accent)",
  STRESSED: "var(--risk-medium)",
  BAD: "var(--risk-high)",
};

export default function FleetOverview() {
  const [fleetId, setFleetId] = useState<string>("");
  const navigate = useNavigate();
  const live = useLiveSocket();
  const fleets = useApi(() => api.get<Page<Fleet>>("/fleets", { limit: 200 }), []);
  const summary = useApi(
    () => api.get<FleetSummary>(fleetId ? `/fleets/${fleetId}/summary` : "/fleets/summary"),
    [fleetId],
    fleetId ? 10_000 : undefined,
  );

  // All-fleets view: the WS pushes a fresh summary every ~2 s, merged over the initial REST fetch.
  const s: FleetSummary | null = !fleetId && live.fleetSummary ? live.fleetSummary : summary.data;
  const riskData = s
    ? (["HIGH", "MEDIUM", "LOW", "UNPREDICTED"] as const).map((k) => ({ name: k, value: s.risk_breakdown[k] }))
    : [];
  const archData = s
    ? (["HEALTHY", "NORMAL", "STRESSED", "BAD"] as const).map((k) => ({ name: k, value: s.archetype_breakdown[k] }))
    : [];
  const predicted = s ? s.vehicle_count - s.risk_breakdown.UNPREDICTED : 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Fleet overview</h1>
          <p className="muted">
            {fleetId ? "Refreshes every 10 s" : "Live — pushed over /ws/live"}
            {s?.generated_at && <> · computed {fmt.date(s.generated_at)}</>}
          </p>
        </div>
        <select value={fleetId} onChange={(e) => setFleetId(e.target.value)} aria-label="Fleet">
          <option value="">All fleets</option>
          {fleets.data?.items.map((f) => (
            <option key={f.fleet_id} value={f.fleet_id}>
              {f.name} ({f.region})
            </option>
          ))}
        </select>
      </div>

      <ErrorBanner error={summary.error} onRetry={summary.reload} />
      {!s && summary.loading && <Spinner />}

      {s && (
        <>
          <div className="stats-grid">
            <StatCard label="Vehicles" value={fmt.int(s.vehicle_count)} sub={s.fleet_name ?? "all fleets"} />
            <StatCard
              label="Average SOH"
              value={fmt.pct(s.avg_soh_pct)}
              sub={`min ${fmt.pct(s.min_soh_pct)}`}
              tone={s.avg_soh_pct !== null && s.avg_soh_pct < 85 ? "warn" : "good"}
            />
            <StatCard label="Average SOC" value={fmt.pct(s.avg_soc_pct)} sub="latest simulated state" />
            <StatCard
              label="High-risk vehicles"
              value={fmt.int(s.risk_breakdown.HIGH)}
              sub={predicted ? `${((100 * s.risk_breakdown.HIGH) / predicted).toFixed(1)}% of predicted` : "no predictions yet"}
              tone={s.risk_breakdown.HIGH > 0 ? "bad" : undefined}
            />
            <StatCard
              label="Open alerts"
              value={fmt.int(s.active_alerts)}
              sub={`${fmt.int(s.critical_alerts)} critical`}
              tone={s.critical_alerts > 0 ? "bad" : s.active_alerts > 0 ? "warn" : "good"}
            />
          </div>

          <div className="grid-2">
            <Card title="Predicted 30-day risk" actions={<Link to="/risk" className="small">Open risk page →</Link>}>
              {predicted === 0 && (
                <p className="muted small">
                  No predictions yet — train the model and click <b>Run prediction</b> on the Risk page.
                </p>
              )}
              <ChartBars data={riskData} colors={RISK_COLORS} onClick={(n) => n !== "UNPREDICTED" && navigate(`/risk?level=${n}`)} />
            </Card>
            <Card title="Driving archetypes">
              <ChartBars data={archData} colors={ARCH_COLORS} onClick={(n) => navigate(`/vehicles?archetype=${n}`)} />
            </Card>
          </div>
        </>
      )}

      <div className="grid-2">
        <Card title="Fleets">
          {fleets.loading && <Spinner />}
          <ErrorBanner error={fleets.error} />
          {fleets.data && fleets.data.items.length === 0 && <Empty>No fleets yet — run a simulation first.</Empty>}
          {fleets.data && fleets.data.items.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Fleet</th>
                    <th>Region</th>
                    <th className="num">Vehicles</th>
                  </tr>
                </thead>
                <tbody>
                  {fleets.data.items.map((f) => (
                    <tr key={f.fleet_id} className="clickable" onClick={() => setFleetId(f.fleet_id)}>
                      <td>{f.name}</td>
                      <td>{f.region}</td>
                      <td className="num">{fmt.int(f.vehicle_count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title="Latest alerts" actions={<Link to="/alerts" className="small">All alerts →</Link>}>
          {live.recentAlerts.length === 0 ? (
            <Empty>{live.status === "open" ? "No alerts yet." : "Waiting for live connection…"}</Empty>
          ) : (
            <ul className="alert-feed">
              {live.recentAlerts.map((a) => (
                <li key={a.alert_id}>
                  <Badge kind={a.severity}>{a.severity}</Badge>
                  <Link to={`/vehicles/${a.vehicle_id}`} className="mono">
                    {a.vehicle_id}
                  </Link>
                  <span>{a.alert_type.replace(/_/g, " ").toLowerCase()}</span>
                  <span className="muted small push">{fmt.date(a.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function ChartBars({ data, colors, onClick }: {
  data: { name: string; value: number }[];
  colors: Record<string, string>;
  onClick?: (name: string) => void;
}) {
  return (
    <div className="chart-box">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--grid)" />
          <XAxis dataKey="name" tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={false} tickLine={false} />
          <YAxis allowDecimals={false} tick={{ fill: "var(--muted)", fontSize: 12 }} axisLine={false} tickLine={false} width={48} />
          <Tooltip cursor={{ fill: "var(--hover)" }} contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }} />
          <Bar dataKey="value" radius={[4, 4, 0, 0]} onClick={(d: { name?: string }) => d.name && onClick?.(d.name)} cursor={onClick ? "pointer" : undefined}>
            {data.map((d) => (
              <Cell key={d.name} fill={colors[d.name]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
