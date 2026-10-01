import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Alert, Page } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Card, Empty, ErrorBanner, fmt, InfoTip, Pager, Spinner, useCursorPager } from "../components/ui";
import { useApi } from "../hooks/useApi";

export default function AlertsPage() {
  const { canWrite } = useAuth();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "OPEN";
  const severity = params.get("severity") ?? "";
  const source = params.get("source") ?? "";
  const vehicleId = params.get("vehicle_id") ?? "";
  const pager = useCursorPager();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const alerts = useApi(
    () =>
      api.get<Page<Alert>>("/alerts", {
        status: status === "ALL" ? "" : status,
        severity,
        source,
        vehicle_id: vehicleId.trim().toUpperCase(),
        limit: 50,
        after: pager.cursor,
      }),
    [status, severity, source, vehicleId, pager.cursor],
    10_000,
  );
  const { reset } = pager;
  useEffect(() => reset(), [status, severity, source, vehicleId, reset]);

  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  async function acknowledge(id: string) {
    setBusy(id);
    setMsg(null);
    try {
      await api.post(`/alerts/${id}/acknowledge`);
      alerts.reload();
    } catch (e) {
      setMsg(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Alerts</h1>
          <p className="muted">Real-time rule alerts from ingestion and ML high-risk alerts. Refreshes every 10 s.</p>
        </div>
      </div>
      <Card>
        <div className="filters">
          <select value={status} onChange={(e) => update("status", e.target.value)} aria-label="Status">
            {["OPEN", "ACKNOWLEDGED", "RESOLVED", "ALL"].map((s) => (
              <option key={s} value={s}>{s === "ALL" ? "Any status" : s}</option>
            ))}
          </select>
          <select value={severity} onChange={(e) => update("severity", e.target.value)} aria-label="Severity">
            <option value="">Any severity</option>
            {["CRITICAL", "WARNING", "INFO"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <select value={source} onChange={(e) => update("source", e.target.value)} aria-label="Source">
            <option value="">Any source</option>
            <option value="REALTIME_RULE">Real-time rule</option>
            <option value="ML_PREDICTION">ML prediction</option>
          </select>
          <input
            placeholder="Vehicle ID"
            defaultValue={vehicleId}
            onBlur={(e) => update("vehicle_id", e.target.value.trim())}
            onKeyDown={(e) => e.key === "Enter" && update("vehicle_id", (e.target as HTMLInputElement).value.trim())}
            aria-label="Vehicle ID"
          />
        </div>
      </Card>
      {msg && <div className="banner error">{msg}</div>}
      <Card
        title={<>Alerts <InfoTip text="'Rule' alerts fire instantly from live telemetry thresholds (e.g. high temperature). 'ML' alerts fire when a prediction crosses the HIGH risk threshold — only appear after a prediction run." /></>}
      >
        <ErrorBanner error={alerts.error} onRetry={alerts.reload} />
        {alerts.loading && !alerts.data && <Spinner />}
        {alerts.data && alerts.data.items.length === 0 && <Empty>No alerts match these filters.</Empty>}
        {alerts.data && alerts.data.items.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Vehicle</th>
                  <th>Type</th>
                  <th>Severity</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {alerts.data.items.map((a) => (
                  <tr key={a.alert_id}>
                    <td>{fmt.date(a.created_at)}</td>
                    <td><Link to={`/vehicles/${a.vehicle_id}`} className="mono">{a.vehicle_id}</Link></td>
                    <td>{a.alert_type.replace(/_/g, " ")}</td>
                    <td><Badge kind={a.severity}>{a.severity}</Badge></td>
                    <td className="small">{a.source === "ML_PREDICTION" ? "ML" : "Rule"}</td>
                    <td><Badge kind={a.status}>{a.status}</Badge></td>
                    <td className="num">
                      {a.status === "OPEN" && canWrite && (
                        <button className="btn small" disabled={busy === a.alert_id} onClick={() => acknowledge(a.alert_id)}>
                          {busy === a.alert_id ? "…" : "Acknowledge"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!canWrite && <p className="muted small">Viewers can't acknowledge alerts — sign in as a fleet manager.</p>}
        <Pager page={pager.page} nextCursor={alerts.data?.next_cursor} onNext={() => pager.next(alerts.data?.next_cursor ?? null)} onPrev={pager.prev} />
      </Card>
    </div>
  );
}
