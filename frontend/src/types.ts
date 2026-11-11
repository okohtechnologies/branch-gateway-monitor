export type Status = "UP" | "DEGRADED" | "DOWN" | "PENDING" | "DISABLED";

export interface Branch {
  id: number;
  name: string;
  host: string;
  location: string;
  probe_type: "icmp" | "tcp";
  port: number | null;
  latency_warn_ms: number;
  loss_warn_pct: number;
  enabled: boolean;
  status: Status;
  latency_ms?: number | null;
  loss_pct?: number | null;
  last_check_at?: string | null;
  uptime_24h?: number | null;
  incident_id?: number | null;
  incident_started_at?: string | null;
  acknowledged_by?: string | null;
}

export interface Incident {
  id: number;
  branch_id: number;
  branch_name: string;
  severity: "DEGRADED" | "DOWN";
  started_at: string;
  ended_at: string | null;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
}

export interface BranchDetail extends Branch {
  uptime: { "24h": number | null; "7d": number | null; "30d": number | null };
  open_incident: Incident | null;
}

export interface Point {
  ts: string;
  latency_ms: number | null;
  loss_pct: number;
  status: "UP" | "DEGRADED" | "DOWN";
}

export type Range = "1h" | "24h" | "7d";

export interface LiveSnapshot {
  branch_id: number;
  name: string;
  status: Status;
  raw_status: string;
  avg_latency_ms: number | null;
  loss_pct: number;
  ts: string;
}

export interface BranchInput {
  name: string;
  host: string;
  location: string;
  probe_type: "icmp" | "tcp";
  port: number | null;
  latency_warn_ms: number;
  loss_warn_pct: number;
  enabled: boolean;
}
