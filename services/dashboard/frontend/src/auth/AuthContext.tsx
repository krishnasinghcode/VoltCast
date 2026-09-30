import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api, loadAuth, onUnauthorized, saveAuth, type StoredAuth } from "../api/client";
import type { LoginResponse } from "../api/types";

interface AuthState {
  auth: StoredAuth | null;
  canWrite: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<StoredAuth | null>(() => loadAuth());
  const navigate = useNavigate();

  const logout = useCallback(() => {
    saveAuth(null);
    setAuth(null);
    navigate("/login", { replace: true });
  }, [navigate]);

  useEffect(() => {
    onUnauthorized(() => {
      setAuth(null);
      navigate("/login", { replace: true, state: { expired: true } });
    });
  }, [navigate]);

  // Log out when the token expires while the tab is open.
  useEffect(() => {
    if (!auth) return;
    const ms = auth.expiresAt - Date.now();
    const t = window.setTimeout(logout, Math.max(0, ms));
    return () => window.clearTimeout(t);
  }, [auth, logout]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await api.post<LoginResponse>("/auth/login", { email, password });
    const next: StoredAuth = {
      token: res.access_token,
      role: res.role,
      email: res.email,
      expiresAt: Date.now() + res.expires_in * 1000,
    };
    saveAuth(next);
    setAuth(next);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ auth, canWrite: auth?.role === "ADMIN" || auth?.role === "FLEET_MANAGER", login, logout }),
    [auth, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
