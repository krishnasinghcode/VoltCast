import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncState<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

/**
 * Runs `fn` when deps change (and every `intervalMs` if given). Stale responses from superseded calls are
 * dropped, and background refreshes don't flip `loading` back on.
 */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[], intervalMs?: number): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const run = useCallback((background: boolean) => {
    const id = ++seq.current;
    if (!background) setLoading(true);
    fnRef
      .current()
      .then((d) => {
        if (id === seq.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (id === seq.current) setError(e);
      })
      .finally(() => {
        if (id === seq.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    run(false);
    if (!intervalMs) return;
    const t = window.setInterval(() => run(true), intervalMs);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs, run]);

  const reload = useCallback(() => run(true), [run]);
  return { data, error, loading, reload };
}
