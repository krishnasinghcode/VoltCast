import { useState } from "react";
import { api, errorMessage } from "../api/client";
import type { SandboxResult } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Card, ErrorBanner, Spinner } from "../components/ui";
import { useApi } from "../hooks/useApi";

interface SandboxInfo {
  enabled: boolean;
  max_rows: number;
  statement_timeout: string;
  examples: { title: string; query: string }[];
}

export default function SqlSandboxPage() {
  const { canWrite } = useAuth();
  const info = useApi(() => api.get<SandboxInfo>("/sql-sandbox"), []);
  const [query, setQuery] = useState("SELECT archetype, COUNT(*) FROM vehicles GROUP BY archetype");
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.post<SandboxResult>("/sql-sandbox", { query }));
    } catch (e) {
      setResult(null);
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>SQL sandbox</h1>
          <p className="muted">
            Read-only SELECT/WITH queries, run as a SELECT-only database role
            {info.data && <> · {info.data.statement_timeout} timeout · max {info.data.max_rows} rows</>}. Everything is rolled back.
          </p>
        </div>
      </div>
      <ErrorBanner error={info.error} />
      <div className="sandbox-grid">
        <Card title="Query">
          <textarea
            className="sql-editor mono"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && canWrite) run();
            }}
            spellCheck={false}
            rows={8}
            aria-label="SQL query"
          />
          <div className="row gap">
            <button className="btn primary" onClick={run} disabled={busy || !canWrite || !query.trim()}>
              {busy ? "Running…" : "Run (Ctrl+Enter)"}
            </button>
            {!canWrite && <span className="muted small">Running queries needs a fleet manager or admin account.</span>}
          </div>
        </Card>
        <Card title="Examples">
          {info.loading && <Spinner />}
          <ul className="example-list">
            {info.data?.examples.map((ex) => (
              <li key={ex.title}>
                <button className="link-btn" onClick={() => setQuery(ex.query)}>{ex.title}</button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      {error && <div className="banner error">{error}</div>}
      {result && (
        <Card
          title={`${result.row_count} row${result.row_count === 1 ? "" : "s"}`}
          actions={<span className="muted small">{result.elapsed_ms} ms{result.limit_added && " · LIMIT 500 added"}{result.truncated && " · truncated"}</span>}
        >
          <div className="table-wrap tall">
            <table>
              <thead>
                <tr>{result.columns.map((c) => <th key={c}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {result.rows.map((r, i) => (
                  <tr key={i}>
                    {r.map((v, j) => (
                      <td key={j} className={typeof v === "number" ? "num" : ""}>{v === null ? <span className="muted">null</span> : String(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
