import { useEffect, useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import type { Page, SimulationRow, SimulationStatus } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Badge, Card, Empty, ErrorBanner, fmt, Spinner, StatCard } from "../components/ui";
import { useApi } from "../hooks/useApi";

type Mode = "LONGITUDINAL" | "SHORT_BURST" | "DEMO";

const PRESETS: Record<Mode, { vehicle_count: number; duration_days: number; simulation_speed: number }> = {
  LONGITUDINAL: { vehicle_count: 100000, duration_days: 90, simulation_speed: 2880 },
  SHORT_BURST: { vehicle_count: 100000, duration_days: 1, simulation_speed: 1 },
  DEMO: { vehicle_count: 1000, duration_days: 7, simulation_speed: 1440 },
};

const STATUS_KIND: Record<string, string> = {
  RUNNING: "risk-low",
  COMPLETED: "info",
  PAUSED: "warning",
  FAILED: "critical",
  CREATED: "none",
  RESET: "none",
};

export default function SimulationControlPage() {
  const { canWrite } = useAuth();
  const sims = useApi(() => api.get<Page<SimulationRow>>("/simulations", { limit: 50 }), [], 10_000);
  const [selected, setSelected] = useState<string>("");
  const status = useApi(
    () => (selected ? api.get<SimulationStatus>(`/simulation/${selected}`) : Promise.resolve(null)),
    [selected],
    selected ? 2000 : undefined,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    if (!selected && sims.data?.items.length) setSelected(sims.data.items[0].simulation_id);
  }, [sims.data, selected]);

  async function control(action: "start" | "pause" | "resume" | "reset") {
    if (!selected) return;
    if (action === "reset" && !window.confirm("Reset this simulation? Its runner state is discarded.")) return;
    setBusy(action);
    setMsg(null);
    try {
      await api.post(`/simulation/${selected}/${action}`);
      status.reload();
      sims.reload();
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(null);
    }
  }

  const st = status.data
    ? { ...status.data, progress_pct: status.data.progress_pct ?? (status.data.status === "COMPLETED" ? 100 : undefined) }
    : null;
  const running = st?.status === "RUNNING";
  const paused = st?.status === "PAUSED";

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Simulation control</h1>
          <p className="muted">Drives the data-gen service through the API proxy.</p>
        </div>
      </div>
      {msg && <div className={`banner ${msg.kind}`}>{msg.text}</div>}

      <div className="grid-2">
        <Card title="Current simulation">
          <ErrorBanner error={sims.error} onRetry={sims.reload} />
          {sims.loading && !sims.data && <Spinner />}
          {sims.data && sims.data.items.length === 0 && <Empty>No simulations yet — create one.</Empty>}
          {sims.data && sims.data.items.length > 0 && (
            <>
              <select value={selected} onChange={(e) => setSelected(e.target.value)} className="block" aria-label="Simulation">
                {sims.data.items.map((s) => (
                  <option key={s.simulation_id} value={s.simulation_id}>
                    {s.mode} · {s.duration_days}d · seed {s.seed} · {s.simulation_id.slice(0, 8)}
                  </option>
                ))}
              </select>
              <ErrorBanner error={status.error} />
              {st && (
                <>
                  <div className="row gap wrap sim-status">
                    <Badge kind={STATUS_KIND[st.status] ?? "none"}>{st.status}</Badge>
                    <span className="muted small">{st.mode}</span>
                    <span className="muted small">sim time {fmt.date(st.current_sim_time)}</span>
                  </div>
                  <div className="progress" aria-label="Progress">
                    <div className="progress-bar" style={{ width: `${Math.min(100, st.progress_pct ?? 0)}%` }} />
                  </div>
                  <div className="stats-grid compact">
                    <StatCard label="Progress" value={fmt.pct(st.progress_pct)} sub={st.eta_seconds != null ? `ETA ${fmt.duration(st.eta_seconds)}` : "—"} />
                    <StatCard label="Events produced" value={fmt.int(st.events_produced)} sub={`elapsed ${fmt.duration(st.elapsed_seconds)}`} />
                    <StatCard label="Vehicles" value={fmt.int(st.vehicles_count)} />
                  </div>
                </>
              )}
              {canWrite && (
                <div className="row gap wrap">
                  <button className="btn primary" disabled={!!busy || running || st?.status === "COMPLETED"} onClick={() => control(paused ? "resume" : "start")}>
                    {busy === "start" || busy === "resume" ? "…" : paused ? "Resume" : "Start"}
                  </button>
                  <button className="btn" disabled={!!busy || !running} onClick={() => control("pause")}>
                    {busy === "pause" ? "…" : "Pause"}
                  </button>
                  <button className="btn danger" disabled={!!busy} onClick={() => control("reset")}>
                    {busy === "reset" ? "…" : "Reset"}
                  </button>
                </div>
              )}
            </>
          )}
        </Card>
        {canWrite ? (
          <CreateSimulation
            onCreated={(id) => {
              setSelected(id);
              sims.reload();
              setMsg({ kind: "success", text: `Simulation ${id.slice(0, 8)} created — press Start.` });
            }}
          />
        ) : (
          <Card title="Create simulation">
            <p className="muted">Viewers can watch simulations but not create or control them.</p>
          </Card>
        )}
      </div>
    </div>
  );
}

function CreateSimulation({ onCreated }: { onCreated: (id: string) => void }) {
  const [mode, setMode] = useState<Mode>("DEMO");
  const [form, setForm] = useState({ seed: 42, ...PRESETS.DEMO, start_time: "2026-01-01T00:00" });
  const [burst, setBurst] = useState({ enabled: false, duration_seconds: 60, target_events_per_sec: 100000 });
  const [faults, setFaults] = useState({ duplicate_rate: 0, out_of_order_rate: 0, malformed_rate: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function pickMode(m: Mode) {
    setMode(m);
    setForm((f) => ({ ...f, ...PRESETS[m] }));
    setBurst((b) => ({ ...b, enabled: m === "SHORT_BURST" }));
    setFaults(m === "SHORT_BURST" ? { duplicate_rate: 0.01, out_of_order_rate: 0.01, malformed_rate: 0.001 } : { duplicate_rate: 0, out_of_order_rate: 0, malformed_rate: 0 });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<{ simulation_id: string }>("/simulation", {
        mode,
        seed: form.seed,
        vehicle_count: form.vehicle_count,
        duration_days: form.duration_days,
        simulation_speed: form.simulation_speed,
        start_time: new Date(form.start_time + "Z").toISOString(),
        burst,
        fault_injection: faults,
      });
      onCreated(res.simulation_id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const num = (v: string) => (v === "" ? 0 : Number(v));

  return (
    <Card title="Create simulation">
      <form onSubmit={submit} className="form-grid">
        <div className="segmented full">
          {(["DEMO", "LONGITUDINAL", "SHORT_BURST"] as Mode[]).map((m) => (
            <button type="button" key={m} className={m === mode ? "active" : ""} onClick={() => pickMode(m)}>
              {m.replace("_", " ").toLowerCase()}
            </button>
          ))}
        </div>
        <label className="field"><span>Vehicles</span><input type="number" min={1} value={form.vehicle_count} onChange={(e) => setForm({ ...form, vehicle_count: num(e.target.value) })} /></label>
        <label className="field"><span>Duration (days)</span><input type="number" min={1} value={form.duration_days} onChange={(e) => setForm({ ...form, duration_days: num(e.target.value) })} /></label>
        <label className="field"><span>Speed (sim-sec per real-sec)</span><input type="number" min={0.1} step="any" value={form.simulation_speed} onChange={(e) => setForm({ ...form, simulation_speed: num(e.target.value) })} /></label>
        <label className="field"><span>Seed</span><input type="number" value={form.seed} onChange={(e) => setForm({ ...form, seed: num(e.target.value) })} /></label>
        <label className="field full"><span>Simulated start (UTC)</span><input type="datetime-local" value={form.start_time} onChange={(e) => setForm({ ...form, start_time: e.target.value })} /></label>
        {mode === "SHORT_BURST" && (
          <>
            <label className="field"><span>Burst duration (s)</span><input type="number" min={1} value={burst.duration_seconds} onChange={(e) => setBurst({ ...burst, duration_seconds: num(e.target.value) })} /></label>
            <label className="field"><span>Target events/s</span><input type="number" min={1} value={burst.target_events_per_sec} onChange={(e) => setBurst({ ...burst, target_events_per_sec: num(e.target.value) })} /></label>
            <label className="field"><span>Duplicate rate</span><input type="number" min={0} max={1} step="0.001" value={faults.duplicate_rate} onChange={(e) => setFaults({ ...faults, duplicate_rate: num(e.target.value) })} /></label>
            <label className="field"><span>Out-of-order rate</span><input type="number" min={0} max={1} step="0.001" value={faults.out_of_order_rate} onChange={(e) => setFaults({ ...faults, out_of_order_rate: num(e.target.value) })} /></label>
            <label className="field"><span>Malformed rate</span><input type="number" min={0} max={1} step="0.001" value={faults.malformed_rate} onChange={(e) => setFaults({ ...faults, malformed_rate: num(e.target.value) })} /></label>
          </>
        )}
        {error && <div className="banner error full">{error}</div>}
        <button className="btn primary full" disabled={busy}>{busy ? "Creating… (building fleet)" : "Create simulation"}</button>
      </form>
    </Card>
  );
}
