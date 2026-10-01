import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError, errorMessage } from "../api/client";
import type { Page, RiskItem, SimulationRow } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Card, Empty, ErrorBanner, fmt, InfoTip, Pager, RiskBadge, Spinner, useCursorPager } from "../components/ui";
import { useApi } from "../hooks/useApi";

type Level = "HIGH" | "MEDIUM" | "LOW";

export default function RiskPage() {
  const { canWrite } = useAuth();
  const [params, setParams] = useSearchParams();
  const level = ((params.get("level") ?? "HIGH").toUpperCase() as Level) || "HIGH";
  const pager = useCursorPager();
  const list = useApi(
    () => api.get<Page<RiskItem>>(`/risk/${level.toLowerCase()}`, { limit: 25, after: pager.cursor }),
    [level, pager.cursor],
  );
  const { reset } = pager;
  useEffect(() => reset(), [level, reset]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Risk &amp; ML</h1>
          <p className="muted">Vehicles grouped by their latest 30-day battery-degradation prediction.</p>
        </div>
      </div>

      <div className="risk-buttons">
        {(["HIGH", "MEDIUM", "LOW"] as Level[]).map((l) => (
          <button
            key={l}
            className={`btn risk-btn risk-btn-${l.toLowerCase()} ${level === l ? "active" : ""}`}
            onClick={() => setParams({ level: l }, { replace: true })}
          >
            {l === "HIGH" ? "High risk vehicles" : l === "LOW" ? "Low risk vehicles" : "Medium risk"}
          </button>
        ))}
      </div>

      {canWrite ? <MlControls onDone={list.reload} /> : (
        <p className="muted small">Training and prediction need a fleet manager or admin account.</p>
      )}

      <Card title={<><RiskBadge level={level} /> vehicles <InfoTip text="Each row is one vehicle's latest prediction. 'Degradation' is the model's predicted battery-health loss over the next 30 days; 'SOH in 30d' is current SOH minus that. If this list is empty, no prediction has been run yet for this risk bucket." /></>}>
        <ErrorBanner error={list.error} onRetry={list.reload} />
        {list.loading && !list.data && <Spinner />}
        {list.data && list.data.items.length === 0 && (
          <Empty>
            No {level.toLowerCase()}-risk vehicles. If the model has never run, train it and click <b>Run prediction</b>.
          </Empty>
        )}
        {list.data && list.data.items.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Vehicle</th>
                  <th>Fleet</th>
                  <th>Archetype</th>
                  <th className="num">SOH now</th>
                  <th className="num">SOH in 30d</th>
                  <th className="num">Degradation</th>
                  <th>Predicted</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((r) => (
                  <tr key={r.prediction_id}>
                    <td><Link to={`/vehicles/${r.vehicle_id}`} className="mono">{r.vehicle_id}</Link></td>
                    <td>{r.fleet_name}</td>
                    <td>{r.archetype}</td>
                    <td className="num">{fmt.pct(r.current_soh, 2)}</td>
                    <td className="num">{fmt.pct(r.predicted_soh_30d, 2)}</td>
                    <td className="num">−{fmt.num(r.predicted_degradation_30d_pct, 3)} pts</td>
                    <td className="small">{fmt.date(r.predicted_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager page={pager.page} nextCursor={list.data?.next_cursor} onNext={() => pager.next(list.data?.next_cursor ?? null)} onPrev={pager.prev} />
      </Card>
    </div>
  );
}

function MlControls({ onDone }: { onDone: () => void }) {
  const sims = useApi(() => api.get<Page<SimulationRow>>("/simulations", { limit: 50 }), []);
  const status = useApi(() => api.get<Record<string, unknown>>("/ml/train/status"), []);
  const [simId, setSimId] = useState("");
  const [horizon, setHorizon] = useState(30);
  const [busy, setBusy] = useState<"train" | "predict" | null>(null);
  const [msg, setMsg] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    if (!simId && sims.data?.items.length) setSimId(sims.data.items[0].simulation_id);
  }, [sims.data, simId]);

  async function train() {
    setBusy("train");
    setMsg(null);
    try {
      const res = await api.post<Record<string, unknown>>("/ml/train", { simulation_id: simId, horizon_days: horizon });
      setMsg({ kind: "success", text: `Model trained: ${summarise(res)}` });
      status.reload();
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(null);
    }
  }

  async function predict() {
    setBusy("predict");
    setMsg(null);
    try {
      const res = await api.post<Record<string, unknown>>("/ml/predict", { simulation_id: simId || null });
      setMsg({ kind: "success", text: `Predictions written: ${summarise(res)}` });
      onDone();
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(null);
    }
  }

  const notTrained = status.error instanceof ApiError && status.error.status === 404;
  const s = status.data;

  return (
    <Card
      title="Model"
      info="Retrain model: builds training features/labels from the selected simulation's aggregated history (needs at least ~2 weeks of simulated days) and fits a new model. Run prediction: scores every vehicle that has any aggregated history against the current model and writes a risk level for each — it does NOT require picking a specific simulation (it covers all vehicles with history), and it can take a while on a large fleet."
    >
      <div className="ml-row">
        <label className="field inline">
          <span>Simulation</span>
          <select value={simId} onChange={(e) => setSimId(e.target.value)}>
            {!sims.data?.items.length && <option value="">— none —</option>}
            {sims.data?.items.map((s) => (
              <option key={s.simulation_id} value={s.simulation_id}>
                {s.mode} · {s.duration_days}d · {s.status} · {s.simulation_id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        <label className="field inline">
          <span>Horizon (days)</span>
          <input type="number" min={1} max={365} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} />
        </label>
        <button className="btn" onClick={train} disabled={!!busy || !simId}>
          {busy === "train" ? "Training…" : "Retrain model"}
        </button>
        <button className="btn primary" onClick={predict} disabled={!!busy}>
          {busy === "predict" ? "Predicting…" : "Run prediction"}
        </button>
      </div>
      {sims.error ? <ErrorBanner error={sims.error} /> : null}
      <div className="muted small">
        {notTrained && "No model trained yet."}
        {status.error && !notTrained ? `ML service: ${errorMessage(status.error)}` : null}
        {s && (
          <>
            Current model: <span className="mono">{String(s.model_version ?? "?")}</span>
            {s.mae_model !== undefined && <> · MAE {fmtMaybe(s.mae_model)} vs baseline {fmtMaybe(s.mae_baseline)}</>}
            {s.trained_at !== undefined && <> · trained {fmt.date(String(s.trained_at))}</>}
          </>
        )}
      </div>
      {msg && <div className={`banner ${msg.kind}`}>{msg.text}</div>}
    </Card>
  );
}

function fmtMaybe(v: unknown): string {
  return typeof v === "number" ? v.toFixed(4) : String(v ?? "—");
}

function summarise(res: Record<string, unknown> | unknown[]): string {
  if (Array.isArray(res)) return `${res.length} rows`;
  const bits = Object.entries(res)
    .filter(([, v]) => typeof v !== "object" || v === null)
    .slice(0, 6)
    .map(([k, v]) => `${k}=${typeof v === "number" ? Number(v.toFixed(4)) : v}`);
  return bits.join(", ") || "ok";
}
