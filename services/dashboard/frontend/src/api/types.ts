export type Role = "ADMIN" | "FLEET_MANAGER" | "VIEWER";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";
export type Archetype = "HEALTHY" | "NORMAL" | "STRESSED" | "BAD";
export type AlertStatus = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
export type Severity = "INFO" | "WARNING" | "CRITICAL";

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  role: Role;
  user_id: string;
  email: string;
}

export interface Fleet {
  fleet_id: string;
  name: string;
  region: string;
  created_at: string;
  vehicle_count: number;
}

export interface FleetSummary {
  fleet_id: string | null;
  fleet_name: string | null;
  vehicle_count: number;
  avg_soh_pct: number | null;
  min_soh_pct: number | null;
  avg_soc_pct: number | null;
  risk_breakdown: Record<RiskLevel | "UNPREDICTED", number>;
  archetype_breakdown: Record<Archetype, number>;
  active_alerts: number;
  critical_alerts: number;
  generated_at: string;
  cached?: boolean;
}

export interface VehicleListItem {
  vehicle_id: string;
  fleet_id: string;
  fleet_name: string;
  region: string;
  archetype: Archetype;
  status: string;
  manufacture_date: string;
  manufacturer: string;
  model_name: string;
  model_year: number;
  risk_level: RiskLevel | null;
  predicted_soh_30d: number | null;
  soh_pct: number | null;
}

export interface CurrentState {
  source: "redis" | "postgres" | "none";
  soc_pct: number | null;
  temperature_c: number | null;
  is_charging: boolean | null;
  odometer_km: number | null;
  last_updated: string | null;
  soh_pct: number | null;
}

export interface Prediction {
  prediction_id: string;
  vehicle_id?: string;
  predicted_at: string;
  current_soh: number;
  predicted_degradation_30d_pct: number;
  predicted_soh_30d: number;
  risk_level: RiskLevel;
  horizon_days: number;
  model_version: string;
}

export interface VehicleDetail {
  vehicle: {
    vehicle_id: string;
    fleet_id: string;
    fleet_name: string;
    region: string;
    archetype: Archetype;
    status: string;
    manufacture_date: string;
    initial_odometer_km: number;
    manufacturer: string;
    model_name: string;
    model_year: number;
    battery_id: string;
    nominal_capacity_kwh: number;
    rated_voltage_v: number;
    installation_date: string;
    chemistry: string;
  };
  current_state: CurrentState;
  ground_truth: Record<string, number | string | null> | null;
  active_alert_flags: string[];
  open_alert_count: number;
  latest_prediction: Prediction | null;
}

export type HistoryRow = Record<string, number | string | null>;

export interface HistoryPage extends Page<HistoryRow> {
  vehicle_id: string;
  resolution: "daily" | "hourly" | "5min";
  time_field: string;
}

export interface Alert {
  alert_id: string;
  vehicle_id: string;
  created_at: string;
  alert_type: string;
  severity: Severity;
  status: AlertStatus;
  source: "REALTIME_RULE" | "ML_PREDICTION";
  prediction_id: string | null;
}

export interface RiskItem extends Prediction {
  vehicle_id: string;
  fleet_id: string;
  fleet_name: string;
  archetype: Archetype;
}

export interface ServiceHealth {
  name: string;
  status: string;
  latency_ms?: number;
  contract_version?: string | null;
  contract_ok?: boolean;
  error?: string;
}

export interface Observability {
  ingestion: {
    events_consumed_total: number | null;
    events_deduplicated_total: number | null;
    events_rejected_total: number | null;
    late_events_total?: number | null;
    consumer_lag: number | null;
    current_watermark_timestamp?: string | null;
    reachable: boolean;
    error?: string;
  };
  api: {
    requests_total: number;
    errors_total: number;
    client_errors_total: number;
    rate_limited_total: number;
    error_rate: number;
    avg_latency_ms: number;
    p95_latency_ms: number;
    p99_latency_ms: number;
    uptime_s: number;
  };
  fleet: { vehicle_count: number | null; active_alerts: number | null };
  services: ServiceHealth[];
  generated_at: string;
}

export interface SimulationStatus {
  simulation_id: string;
  status: string;
  mode: string;
  current_sim_time?: string;
  events_produced?: number;
  vehicles_count?: number;
  progress_pct?: number;
  eta_seconds?: number | null;
  elapsed_seconds?: number | null;
  [key: string]: unknown;
}

export interface SimulationRow {
  simulation_id: string;
  seed: number;
  mode: string;
  start_time: string;
  duration_days: number;
  simulation_speed: number;
  status: string;
  created_at: string;
}

export interface SandboxResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  limit_added: boolean;
  executed_sql: string;
}

export interface LiveMessage {
  type: "live" | "vehicle_states" | "pong" | "error";
  ts?: string;
  fleet_summary?: FleetSummary | null;
  recent_alerts?: Alert[];
  vehicle_states?: Record<string, LiveVehicleState>;
  error?: { code: string; message: string };
}

export interface LiveVehicleState {
  soc_pct?: number;
  temperature_c?: number;
  is_charging?: boolean;
  odometer_km?: number;
  last_updated?: string;
  alert_flags?: string[];
}
