// Fetch wrapper: attaches the JWT, parses the contract error shape, and on 401 clears the session and
// redirects to /login.

const TOKEN_KEY = "cvi.auth";

export const API_BASE: string = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export interface StoredAuth {
  token: string;
  role: "ADMIN" | "FLEET_MANAGER" | "VIEWER";
  email: string;
  expiresAt: number;
}

export function loadAuth(): StoredAuth | null {
  try {
    const raw = localStorage.getItem(TOKEN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredAuth;
    if (!parsed.token || parsed.expiresAt < Date.now()) {
      localStorage.removeItem(TOKEN_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function saveAuth(auth: StoredAuth | null) {
  try {
    if (auth) localStorage.setItem(TOKEN_KEY, JSON.stringify(auth));
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable — session lives in memory only */
  }
}

let unauthorizedHandler: (() => void) | null = null;
export function onUnauthorized(handler: () => void) {
  unauthorizedHandler = handler;
}

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function buildUrl(path: string, query?: Query): string {
  const qs = new URLSearchParams();
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    }
  }
  const s = qs.toString();
  return `${API_BASE}/api/v1${path}${s ? `?${s}` : ""}`;
}

async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const auth = loadAuth();
  if (auth) headers.Authorization = `Bearer ${auth.token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  let resp: Response;
  try {
    resp = await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Cannot reach the API server");
  }

  const text = await resp.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }

  if (!resp.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })?.error;
    const apiErr = new ApiError(
      resp.status,
      err?.code ?? `HTTP_${resp.status}`,
      err?.message ?? (typeof data === "string" && data ? data : resp.statusText || "Request failed"),
      err?.details ?? {},
    );
    if (resp.status === 401 && path !== "/auth/login") {
      saveAuth(null);
      unauthorizedHandler?.();
    }
    throw apiErr;
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", path, { query }),
  post: <T>(path: string, body?: unknown, query?: Query) => request<T>("POST", path, { body, query }),
};

export function wsUrl(token: string): string {
  const base = API_BASE
    ? API_BASE.replace(/^http/, "ws")
    : `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
  return `${base}/ws/live?token=${encodeURIComponent(token)}`;
}

export function errorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "UPSTREAM_UNAVAILABLE") return `${err.message} — is that service running?`;
    if (err.code === "RATE_LIMITED") return "Too many requests — slow down and try again in a moment.";
    return err.message;
  }
  return err instanceof Error ? err.message : String(err);
}
