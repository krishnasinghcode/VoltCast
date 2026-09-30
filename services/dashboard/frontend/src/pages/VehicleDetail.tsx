import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, errorMessage } from "../api/client";
import type { Alert, HistoryPage, Page, Prediction, VehicleDetail as Detail } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Card, Empty, ErrorBanner, fmt, RiskBadge, Spinner, StatCard } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { useLiveVehicle } from "../hooks/useLiveSocket";

type Resolution = "daily" | "hourly" | "5min";

// Which columns each aggregate table offers for the SOC / temperature / SOH chart.
const SERIES: Record<Resolution, { key: string; label: string; color: string; axis: "pct" | "temp" }[]> = {
  daily: [
    { key: "soh_pct", label: "SOH %", color: "var(--accent)", axis: "pct" },
    { key: "max_temp_c", label: "Max temp °C", color: "var(--risk-high)", axis: "temp" },
  ],
  hourly: [
    { key: "avg_soc", label: "Avg SOC %", color: "var(--accent)", axis: "pct" },
    { key: "max_temp_c", label: "Max temp °C", color: "var(--risk-high)", axis: "temp" },
  ],
  "5min": [
    { key: "avg_soc", label: "Avg SOC %", color: "var(--accent)", axis: "pct" },
    { key: "avg_temp_c", label: "Avg temp °C", color: "var(--risk-high)", axis: "temp" },
    { key: "max_temp_c", label: "Max temp °C", color: "var(--risk-medium)", axis: "temp" },
  ],
};
const DEFAULT_LIMIT: Record<Resolution, number> = { daily: 120, hourly: 168, "5min": 288 };

export default function VehicleDetail() {
  const { vehicleId = "" } = useParams();
  const { canWrite } = useAuth();
  const [resolution, setResolution] = useState<Resolution>("daily");
  const [predicting, setPredicting] = useState(false);
  const [actionMsg, setActionMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const detail = useApi(() => api.get<Detail>(`/vehicles/${vehicleId}`), [vehicleId], 15_000);
  const history = useApi(
    () => api.get<HistoryPage>(`/vehicles/${vehicleId}/history`, { resolution, limit: DEFAULT_LIMIT[resolution] }),
    [vehicleId, resolution],
  );
  const predictions = useApi(() => api.get<Page<Prediction>>(`/vehicles/${vehicleId}/predictions`, { limit: 10 }), [vehicleId]);
  const alerts = useApi(() => api.get<Page<Alert>>("/alerts", { vehicle_id: vehicleId, limit: 20 }), [vehicleId]);
  const live = useLiveVehicle(vehicleId);

  const chartData = useMemo(() => {
    if (!history.data) return [];
    const tf = history.data.time_field;
    return [...history.data.items].reverse().map((r) => ({ ...r, t: String(r[tf]) }));
  }, [history.data]);

  async function predictNow() {
    setPredicting(true);
    setActionMsg(null);
    try {
      await api.post(`/ml/predict/${vehicleId}`);
      setActionMsg({ kind: "ok", text: "Prediction stored." });
      predictions.reload();
      detail.reload();
    } catch (e) {
      setActionMsg({ kind: "error", text: errorMessage(e) });
    } finally {
      setPredicting(false);
    }
  }

  if (detail.loading && !detail.data) return <div className="page"><Spinner /></div>;
  if (detail.error && !detail.data)
    return (
      <div className="page">
        <Link to="/vehicles" className="small">← Vehicle search</Link>
        <ErrorBanner error={detail.error} onRetry={detail.reload} />
      </div>
    );
  if (!detail.data) return null;

  const { vehicle: v, current_state: base, latest_prediction: pred } = detail.data;
  // Merge live WS push over the REST snapshot.
  const cur = { ...base, ...(live ?? {}) };
  const flags = live?.alert_flags ?? detail.data.active_alert_flags;
  const tickFmt = (t: string) => (resolution === "daily" ? t.slice(5, 10) : new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));

  return (
    <div className="page">
      <Link to="/vehicles" className="small">← Vehicle search</Link>
      <div className="page-head">
        <div>
          <h1 className="mono">{v.vehicle_id}</h1>
          <p className="muted">
            {v.manufacturer} {v.model_name} ({v.model_year}) · {v.fleet_name}, {v.region} · {v.chemistry}{" "}
            {fmt.num(v.nominal_capacity_kwh, 0)} kWh · archetype <b>{v.archetype}</b>
          </p>
        </div>
        <div className="row gap">
          {flags.map((f) => (
            <Badge key={f} kind="warning">{f.replace(/_/g, " ")}</Badge>
          ))}
          {canWrite && (
            <button className="btn" onClick={predictNow} disabled={predicting}>
              {predicting ? "Predicting…" : "Predict now"}
            </button>
          )}
        </div>
      </div>
      {actionMsg && <div className={`banner ${actionMsg.kind === "ok" ? "success" : "error"}`}>{actionMsg.text}</div>}

      <div className="stats-grid">
        <StatCard
          label="State of charge"
          value={fmt.pct(cur.soc_pct)}
          sub={cur.is_charging ? "⚡ charging" : cur.is_charging === false ? "not charging" : "—"}
        />
        <StatCard
          label="Battery temp"
          value={cur.temperature_c === null || cur.temperature_c === undefined ? "—" : `${fmt.num(cur.temperature_c)} °C`}
          tone={(cur.temperature_c ?? 0) > 45 ? "bad" : (cur.temperature_c ?? 0) > 35 ? "warn" : undefined}
        />
        <StatCard label="State of health" value={fmt.pct(base.soh_pct, 2)} tone={(base.soh_pct ?? 100) < 80 ? "bad" : undefined} />
        <StatCard label="Odometer" value={`${fmt.int(cur.odometer_km)} km`} />
        <StatCard
          label="30-day outlook"
          value={pred ? fmt.pct(pred.predicted_soh_30d, 2) : "—"}
          sub={pred ? <RiskBadge level={pred.risk_level} /> : "not predicted yet"}
          tone={pred?.risk_level === "HIGH" ? "bad" : pred?.risk_level === "MEDIUM" ? "warn" : undefined}
        />
      </div>
      <p className="muted small">
        {live ? "● live" : `source: ${base.source}`} · last update {fmt.date(cur.last_updated)}
      </p>

      <Card
        title="History"
        actions={
          <div className="segmented">
            {(["daily", "hourly", "5min"] as Resolution[]).map((r) => (
              <button key={r} className={r === resolution ? "active" : ""} onClick={() => setResolution(r)}>
                {r}
              </button>
            ))}
          </div>
        }
      >
        <ErrorBanner error={history.error} onRetry={history.reload} />
        {history.loading && !history.data && <Spinner />}
        {history.data && chartData.length === 0 && <Empty>No {resolution} aggregates for this vehicle yet.</Empty>}
        {chartData.length > 0 && (
          <div className="chart-box">
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" />
                <XAxis dataKey="t" tickFormatter={tickFmt} tick={{ fill: "var(--muted)", fontSize: 12 }} minTickGap={24} />
                <YAxis yAxisId="pct" tick={{ fill: "var(--muted)", fontSize: 12 }} width={44} domain={["auto", "auto"]} />
                <YAxis yAxisId="temp" orientation="right" tick={{ fill: "var(--muted)", fontSize: 12 }} width={40} domain={["auto", "auto"]} />
                <Tooltip
                  labelFormatter={(t) => fmt.date(String(t))}
                  formatter={(val: number) => (typeof val === "number" ? val.toFixed(2) : val)}
                  contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }}
                />
                <Legend />
                {SERIES[resolution].map((s) => (
                  <Line key={s.key} yAxisId={s.axis} type="linear" dataKey={s.key} name={s.label} stroke={s.color} dot={chartData.length < 40} strokeWidth={2} connectNulls isAnimationActive={false} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <div className="grid-2">
        <Card title="Predictions">
          {predictions.data && predictions.data.items.length === 0 && <Empty>No predictions for this vehicle yet.</Empty>}
          <ErrorBanner error={predictions.error} />
          {predictions.data && predictions.data.items.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>When</th>
                    <th className="num">SOH now</th>
                    <th className="num">In 30d</th>
                    <th className="num">Δ</th>
                    <th>Risk</th>
                    <th>Model</th>
                  </tr>
                </thead>
                <tbody>
                  {predictions.data.items.map((p) => (
                    <tr key={p.prediction_id}>
                      <td>{fmt.date(p.predicted_at)}</td>
                      <td className="num">{fmt.num(p.current_soh, 2)}</td>
                      <td className="num">{fmt.num(p.predicted_soh_30d, 2)}</td>
                      <td className="num">−{fmt.num(p.predicted_degradation_30d_pct, 3)}</td>
                      <td><RiskBadge level={p.risk_level} /></td>
                      <td className="mono small">{p.model_version}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title={`Alerts (${detail.data.open_alert_count} open)`}>
          {alerts.data && alerts.data.items.length === 0 && <Empty>No alerts for this vehicle.</Empty>}
          <ErrorBanner error={alerts.error} />
          {alerts.data && alerts.data.items.length > 0 && (
            <ul className="alert-feed">
              {alerts.data.items.map((a) => (
                <li key={a.alert_id}>
                  <Badge kind={a.severity}>{a.severity}</Badge>
                  <span>{a.alert_type.replace(/_/g, " ").toLowerCase()}</span>
                  <Badge kind={a.status}>{a.status}</Badge>
                  <span className="muted small push">{fmt.date(a.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {detail.data.ground_truth && (
        <Card title="Simulator ground truth">
          <div className="kv-grid">
            {Object.entries(detail.data.ground_truth).map(([k, val]) => (
              <div key={k}>
                <div className="muted small">{k.replace(/_/g, " ")}</div>
                <div className="mono">{typeof val === "number" ? Number(val.toFixed(3)).toString() : String(val ?? "—")}</div>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
