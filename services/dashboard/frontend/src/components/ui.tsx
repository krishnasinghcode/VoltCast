import { useCallback, useState, type ReactNode } from "react";
import { errorMessage } from "../api/client";

export const fmt = {
  num(v: number | null | undefined, digits = 1): string {
    return v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);
  },
  int(v: number | null | undefined): string {
    return v === null || v === undefined ? "—" : Math.round(v).toLocaleString();
  },
  pct(v: number | null | undefined, digits = 1): string {
    return v === null || v === undefined ? "—" : `${v.toFixed(digits)}%`;
  },
  date(v: string | null | undefined): string {
    if (!v) return "—";
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? v : d.toLocaleString();
  },
  day(v: string | null | undefined): string {
    if (!v) return "—";
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? v : d.toLocaleDateString();
  },
  duration(s: number | null | undefined): string {
    if (s === null || s === undefined) return "—";
    if (s < 60) return `${Math.round(s)}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  },
};

export function StatCard(props: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "warn" | "bad" }) {
  return (
    <div className={`card stat ${props.tone ? `tone-${props.tone}` : ""}`}>
      <div className="stat-label">{props.label}</div>
      <div className="stat-value">{props.value}</div>
      {props.sub !== undefined && <div className="stat-sub">{props.sub}</div>}
    </div>
  );
}

export function Badge({ kind, children }: { kind: string; children: ReactNode }) {
  return <span className={`badge badge-${kind.toLowerCase()}`}>{children}</span>;
}

export function RiskBadge({ level }: { level: string | null | undefined }) {
  if (!level) return <span className="badge badge-none">n/a</span>;
  return <Badge kind={`risk-${level}`}>{level}</Badge>;
}

export function Card({ title, actions, children, className }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="banner error">
      <span>{errorMessage(error)}</span>
      {onRetry && (
        <button className="btn small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="spinner-row">
      <span className="spinner" /> {label}
    </div>
  );
}

/** Keyset pagination helper: keeps a stack of cursors so "Previous" works without offsets. */
export function useCursorPager() {
  const [stack, setStack] = useState<(string | null)[]>([null]);
  const cursor = stack[stack.length - 1];
  const next = useCallback((c: string | null) => c && setStack((s) => [...s, c]), []);
  const prev = useCallback(() => setStack((s) => (s.length > 1 ? s.slice(0, -1) : s)), []);
  const reset = useCallback(() => setStack([null]), []);
  return { cursor, page: stack.length, next, prev, reset };
}

export function Pager(props: { page: number; nextCursor: string | null | undefined; onNext: () => void; onPrev: () => void }) {
  return (
    <div className="pager">
      <button className="btn small" disabled={props.page <= 1} onClick={props.onPrev}>
        ← Previous
      </button>
      <span className="muted small">Page {props.page}</span>
      <button className="btn small" disabled={!props.nextCursor} onClick={props.onNext}>
        Next →
      </button>
    </div>
  );
}
