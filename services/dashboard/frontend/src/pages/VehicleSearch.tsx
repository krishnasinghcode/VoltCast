import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { Fleet, Page, VehicleListItem } from "../api/types";
import { Card, Empty, ErrorBanner, fmt, Pager, RiskBadge, Spinner, useCursorPager } from "../components/ui";
import { useApi } from "../hooks/useApi";

export default function VehicleSearch() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const search = params.get("search") ?? "";
  const fleetId = params.get("fleet_id") ?? "";
  const archetype = params.get("archetype") ?? "";
  const risk = params.get("risk_level") ?? "";
  const [draft, setDraft] = useState(search);
  const pager = useCursorPager();

  const fleets = useApi(() => api.get<Page<Fleet>>("/fleets", { limit: 200 }), []);
  const result = useApi(
    () =>
      api.get<Page<VehicleListItem>>("/vehicles", {
        search,
        fleet_id: fleetId,
        archetype,
        risk_level: risk,
        limit: 25,
        after: pager.cursor,
      }),
    [search, fleetId, archetype, risk, pager.cursor],
  );

  // New filters -> back to page 1
  const { reset } = pager;
  useEffect(() => reset(), [search, fleetId, archetype, risk, reset]);

  // Debounced search-as-you-type
  useEffect(() => {
    if (draft === search) return;
    const t = window.setTimeout(() => update("search", draft.trim()), 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  const items = result.data?.items ?? [];
  const exact = items.length === 1 && items[0].vehicle_id === search.toUpperCase();

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Vehicle search</h1>
          <p className="muted">Search by vehicle ID (e.g. EV-000038291) — prefix or any part of it.</p>
        </div>
      </div>

      <Card>
        <form
          className="filters"
          onSubmit={(e) => {
            e.preventDefault();
            if (exact) navigate(`/vehicles/${items[0].vehicle_id}`);
            else update("search", draft.trim());
          }}
        >
          <input
            className="search-input"
            placeholder="EV-0000…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
            aria-label="Vehicle ID"
          />
          <select value={fleetId} onChange={(e) => update("fleet_id", e.target.value)} aria-label="Fleet">
            <option value="">All fleets</option>
            {fleets.data?.items.map((f) => (
              <option key={f.fleet_id} value={f.fleet_id}>
                {f.name}
              </option>
            ))}
          </select>
          <select value={archetype} onChange={(e) => update("archetype", e.target.value)} aria-label="Archetype">
            <option value="">Any archetype</option>
            {["HEALTHY", "NORMAL", "STRESSED", "BAD"].map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
          <select value={risk} onChange={(e) => update("risk_level", e.target.value)} aria-label="Risk">
            <option value="">Any risk</option>
            {["HIGH", "MEDIUM", "LOW"].map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </form>
      </Card>

      <Card>
        <ErrorBanner error={result.error} onRetry={result.reload} />
        {result.loading && !result.data && <Spinner />}
        {result.data && items.length === 0 && <Empty>No vehicles match these filters.</Empty>}
        {items.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Vehicle</th>
                  <th>Fleet</th>
                  <th>Model</th>
                  <th>Archetype</th>
                  <th className="num">SOH</th>
                  <th className="num">SOH in 30d</th>
                  <th>Risk</th>
                </tr>
              </thead>
              <tbody>
                {items.map((v) => (
                  <tr key={v.vehicle_id} className="clickable" onClick={() => navigate(`/vehicles/${v.vehicle_id}`)}>
                    <td className="mono">{v.vehicle_id}</td>
                    <td>{v.fleet_name}</td>
                    <td>
                      {v.manufacturer} {v.model_name} <span className="muted">’{String(v.model_year).slice(-2)}</span>
                    </td>
                    <td>{v.archetype}</td>
                    <td className="num">{fmt.pct(v.soh_pct, 2)}</td>
                    <td className="num">{fmt.pct(v.predicted_soh_30d, 2)}</td>
                    <td>
                      <RiskBadge level={v.risk_level} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <Pager page={pager.page} nextCursor={result.data?.next_cursor} onNext={() => pager.next(result.data?.next_cursor ?? null)} onPrev={pager.prev} />
      </Card>
    </div>
  );
}
