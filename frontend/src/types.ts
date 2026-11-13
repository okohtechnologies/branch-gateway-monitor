export type Status = "UP" | "DEGRADED" | "DOWN" | "PENDING" | "DISABLED";

/** One bucket of the last hour: average latency (null = no reply) and whether any check was DOWN. */
export interface SparkPoint {
  v: number | null;
  d: boolean;
}

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
  spark?: SparkPoint[];
}

export interface BranchesResponse {
  branches: Branch[];
  monitor_suspect: boolean;
  interval_s: number;
  alerts_pending?: number;
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

// ---- settings screen ----

export type SettingValue = number | string | boolean;

export interface SettingLimit {
  kind: "int" | "float" | "bool" | "host" | "choice";
  min?: number;
  max?: number;
  choices?: string[];
}

export interface SettingsData {
  values: Record<string, SettingValue>;
  defaults: Record<string, SettingValue>;
  custom: string[];
  limits: Record<string, SettingLimit>;
  warnings: string[];
}

export interface MonitorStatus {
  ok: boolean;
  database: boolean;
  probe_loop: { ok: boolean; cycles: number; last_cycle_age_s: number | null };
  interval_s: number;
  alerts_pending: number;
  uptime_s: number;
  heartbeat: { configured: boolean; failing: boolean; last_ok_age_s: number | null };
}

export interface TelegramStatus {
  configured: boolean;
  bot: { username: string | null; name: string | null } | null;
  bot_error: string | null;
  chat: string | null;
  pending: number;
  delivered_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
}

export type HostState = "online" | "offline" | "free";

export interface ScanHost {
  ip: string;
  /** online: answered now. offline: did not answer but is known (seen before, or marked assigned). free: never seen. */
  status: HostState;
  latency_ms: number | null;
  ports: number[];
  hostname: string | null;
  mac: string | null;
  /** Manufacturer from the MAC prefix; null when unknown or the address is randomized. */
  vendor: string | null;
  /** A randomized "private" MAC, as phones and many laptops use. */
  mac_private: boolean;
  /** How an online host was found: its replies, or only its ARP answer (it ignores pings). */
  via: "reply" | "arp" | null;
  /** A name someone gave this address. */
  label: string | null;
  /** Something is remembered about this address. */
  known: boolean;
  last_seen: string | null;
}

export interface ScanView {
  id: string;
  target: string;
  state: "running" | "done" | "cancelled" | "failed";
  /** While running: scanning addresses, then naming (waiting for device name lookups, which can be slow). */
  phase: "scanning" | "naming";
  error: string | null;
  total: number;
  done: number;
  online: number;
  offline: number;
  free: number;
  elapsed_s: number;
  /** Pass back as `since` to receive only rows added after this one. */
  next: number;
  /** Pass back as `version`: if it changed, the rows were revised and `reset` says to replace them all. */
  version: number;
  reset: boolean;
  results: ScanHost[];
}
