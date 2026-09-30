import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { wsUrl } from "../api/client";
import type { Alert, FleetSummary, LiveMessage, LiveVehicleState } from "../api/types";
import { useAuth } from "../auth/AuthContext";

// One shared /ws/live connection for the whole app. Components read the latest pushed snapshot and can
// subscribe to per-vehicle live state; the server pushes every ~2 s (polling-based, see API README).

export type LiveStatus = "connecting" | "open" | "closed";

interface LiveState {
  status: LiveStatus;
  lastUpdate: string | null;
  fleetSummary: FleetSummary | null;
  recentAlerts: Alert[];
  vehicleStates: Record<string, LiveVehicleState>;
  subscribe: (vehicleId: string) => () => void;
}

const LiveContext = createContext<LiveState | null>(null);

export function LiveSocketProvider({ children }: { children: ReactNode }) {
  const { auth } = useAuth();
  const [status, setStatus] = useState<LiveStatus>("closed");
  const [lastUpdate, setLastUpdate] = useState<string | null>(null);
  const [fleetSummary, setFleetSummary] = useState<FleetSummary | null>(null);
  const [recentAlerts, setRecentAlerts] = useState<Alert[]>([]);
  const [vehicleStates, setVehicleStates] = useState<Record<string, LiveVehicleState>>({});
  const wsRef = useRef<WebSocket | null>(null);
  const subs = useRef<Map<string, number>>(new Map());

  const sendSubscriptions = useCallback(() => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: "subscribe", vehicle_ids: [...subs.current.keys()] }));
    }
  }, []);

  useEffect(() => {
    if (!auth) return;
    let stopped = false;
    let retry = 0;
    let timer: number | undefined;

    const connect = () => {
      setStatus("connecting");
      const ws = new WebSocket(wsUrl(auth.token));
      wsRef.current = ws;
      ws.onopen = () => {
        retry = 0;
        setStatus("open");
        sendSubscriptions();
      };
      ws.onmessage = (ev) => {
        let msg: LiveMessage;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "live") {
          if (msg.fleet_summary) setFleetSummary(msg.fleet_summary);
          if (msg.recent_alerts) setRecentAlerts(msg.recent_alerts);
          setLastUpdate(msg.ts ?? new Date().toISOString());
        }
        if (msg.vehicle_states && Object.keys(msg.vehicle_states).length) {
          setVehicleStates((prev) => ({ ...prev, ...msg.vehicle_states }));
        }
      };
      ws.onclose = (ev) => {
        setStatus("closed");
        wsRef.current = null;
        if (stopped || ev.code === 4401) return; // 4401 = bad token; the REST 401 handler will log out
        retry = Math.min(retry + 1, 6);
        timer = window.setTimeout(connect, 500 * 2 ** retry);
      };
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [auth, sendSubscriptions]);

  const subscribe = useCallback(
    (vehicleId: string) => {
      subs.current.set(vehicleId, (subs.current.get(vehicleId) ?? 0) + 1);
      sendSubscriptions();
      return () => {
        const n = (subs.current.get(vehicleId) ?? 1) - 1;
        if (n <= 0) subs.current.delete(vehicleId);
        else subs.current.set(vehicleId, n);
        sendSubscriptions();
      };
    },
    [sendSubscriptions],
  );

  return (
    <LiveContext.Provider value={{ status, lastUpdate, fleetSummary, recentAlerts, vehicleStates, subscribe }}>
      {children}
    </LiveContext.Provider>
  );
}

export function useLiveSocket(): LiveState {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLiveSocket must be used inside LiveSocketProvider");
  return ctx;
}

/** Subscribe to one vehicle's pushed Redis state for as long as the calling component is mounted. */
export function useLiveVehicle(vehicleId: string | undefined): LiveVehicleState | undefined {
  const { subscribe, vehicleStates } = useLiveSocket();
  useEffect(() => {
    if (!vehicleId) return;
    return subscribe(vehicleId);
  }, [vehicleId, subscribe]);
  return vehicleId ? vehicleStates[vehicleId] : undefined;
}
