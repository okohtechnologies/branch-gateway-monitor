/** Pure helpers behind the screens. No React, no I/O: everything here is unit-tested. */
import type { Branch, BranchInput, HostState, Incident, Point, ScanHost, SettingLimit, SettingValue, SparkPoint, Status } from "./types";

export type StatusFilter = "all" | "down" | "degraded" | "up" | "pending" | "disabled";
export const STATUS_FILTERS: readonly StatusFilter[] = ["all", "down", "degraded", "up", "pending", "disabled"];

/** Problems first, healthy ones recede, disabled last. */
export const STATUS_ORDER: Record<Status, number> = { DOWN: 0, DEGRADED: 1, PENDING: 2, UP: 3, DISABLED: 4 };

export function parseFilter(value: string | null): StatusFilter {
  return STATUS_FILTERS.includes(value as StatusFilter) ? (value as StatusFilter) : "all";
}

export function sortBranches(branches: Branch[]): Branch[] {
  return [...branches].sort(
    (a, b) =>
      STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
      a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" }),
  );
}

export function filterBranches(branches: Branch[], query: string, filter: StatusFilter): Branch[] {
  const q = query.trim().toLowerCase();
  return branches.filter((b) => {
    if (filter !== "all" && b.status.toLowerCase() !== filter) return false;
    if (!q) return true;
    return b.name.toLowerCase().includes(q) || b.host.toLowerCase().includes(q) || b.location.toLowerCase().includes(q);
  });
}

export function countByStatus(branches: Branch[]): Record<Status, number> {
  const c: Record<Status, number> = { UP: 0, DEGRADED: 0, DOWN: 0, PENDING: 0, DISABLED: 0 };
  for (const b of branches) c[b.status]++;
  return c;
}

/** Data is stale when the live feed is down, or no probe cycle arrived for ~3 intervals. */
export function isStale(nowMs: number, lastCycleMs: number | null, intervalS: number | null, connected: boolean): boolean {
  if (!connected) return true;
  if (lastCycleMs === null) return false; // nothing yet: still waiting for the first cycle
  const allowedMs = ((intervalS ?? 30) * 3 + 5) * 1000;
  return nowMs - lastCycleMs > allowedMs;
}

export interface ChartPoint {
  t: number;
  latency: number | null;
  loss: number;
  down: boolean;
}

export function toChartPoints(points: Point[]): ChartPoint[] {
  return points.map((p) => ({
    t: new Date(p.ts).getTime(),
    latency: p.latency_ms,
    loss: p.loss_pct,
    down: p.status === "DOWN",
  }));
}

/**
 * Contiguous runs of DOWN points as [start, end] time ranges, padded by half a sample
 * so even a single failed sample is visible as a band on the chart.
 */
export function outageSegments(points: ChartPoint[]): Array<[number, number]> {
  if (points.length === 0) return [];
  const gaps = points
    .slice(1)
    .map((p, i) => p.t - points[i].t)
    .filter((g) => g > 0)
    .sort((a, b) => a - b);
  const half = (gaps.length ? gaps[Math.floor(gaps.length / 2)] : 30_000) / 2;
  const segs: Array<[number, number]> = [];
  let start: number | null = null;
  let last = 0;
  for (const p of points) {
    if (p.down) {
      if (start === null) start = p.t;
      last = p.t;
    } else if (start !== null) {
      segs.push([start - half, last + half]);
      start = null;
    }
  }
  if (start !== null) segs.push([start - half, last + half]);
  return segs;
}

export interface ChartSummary {
  samples: number;
  avgLatency: number | null;
  maxLatency: number | null;
  outages: number;
}

export function chartSummary(points: ChartPoint[]): ChartSummary {
  const lat = points.map((p) => p.latency).filter((v): v is number => v !== null);
  return {
    samples: points.length,
    avgLatency: lat.length ? lat.reduce((a, b) => a + b, 0) / lat.length : null,
    maxLatency: lat.length ? Math.max(...lat) : null,
    outages: outageSegments(points).length,
  };
}

/** "host: invalid IP; name: required" -> { host: "invalid IP", name: "required" }. Unmatched text goes under "_". */
export function parseFieldErrors(message: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of message.split(";")) {
    const m = part.trim().match(/^([a-z_]+):\s*(.+)$/i);
    if (m) out[m[1]] = m[2];
    else if (part.trim()) out._ = out._ ? `${out._}; ${part.trim()}` : part.trim();
  }
  return out;
}

export type IncidentRange = "24h" | "7d" | "30d" | "all";
const RANGE_MS: Record<Exclude<IncidentRange, "all">, number> = {
  "24h": 86_400_000,
  "7d": 7 * 86_400_000,
  "30d": 30 * 86_400_000,
};

export interface IncidentFilters {
  openOnly: boolean;
  branchId: number | null;
  severity: "all" | "DOWN" | "DEGRADED";
  range: IncidentRange;
}

export function filterIncidents(incidents: Incident[], f: IncidentFilters, nowMs: number): Incident[] {
  return incidents.filter((i) => {
    if (f.openOnly && i.ended_at !== null) return false;
    if (f.branchId !== null && i.branch_id !== f.branchId) return false;
    if (f.severity !== "all" && i.severity !== f.severity) return false;
    if (f.range !== "all" && nowMs - new Date(i.started_at).getTime() > RANGE_MS[f.range]) return false;
    return true;
  });
}

/** Open incidents are pinned above resolved ones; newest first within each group. */
export function sortIncidents(incidents: Incident[]): Incident[] {
  return [...incidents].sort((a, b) => {
    const ao = a.ended_at === null ? 0 : 1;
    const bo = b.ended_at === null ? 0 : 1;
    return ao - bo || new Date(b.started_at).getTime() - new Date(a.started_at).getTime();
  });
}

export interface IncidentSummary {
  total: number;
  open: number;
  avgResolvedMs: number | null;
}

export function summarizeIncidents(incidents: Incident[]): IncidentSummary {
  const resolved = incidents.filter((i) => i.ended_at !== null);
  const total = resolved.reduce(
    (sum, i) => sum + (new Date(i.ended_at as string).getTime() - new Date(i.started_at).getTime()),
    0,
  );
  return {
    total: incidents.length,
    open: incidents.length - resolved.length,
    avgResolvedMs: resolved.length ? total / resolved.length : null,
  };
}

const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
const HOSTNAME = /^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$/;

/** Fast, friendly checks before submitting. The server validates again and has the final say. */
export function validateBranchInput(f: BranchInput): Record<string, string> {
  const e: Record<string, string> = {};
  if (!f.name.trim()) e.name = "Enter a name for this branch.";
  else if (f.name.trim().length > 80) e.name = "Use 80 characters or fewer.";
  const host = f.host.trim();
  if (!host) e.host = "Enter the gateway's IP address or hostname.";
  else if (/^[\d.]+$/.test(host) && !IPV4.test(host)) e.host = "That is not a valid IP address.";
  else if (!IPV4.test(host) && !host.includes(":") && !HOSTNAME.test(host)) e.host = "Enter a valid IP address or hostname.";
  if (f.probe_type === "tcp" && (f.port === null || !Number.isInteger(f.port) || f.port < 1 || f.port > 65535)) {
    e.port = "TCP checks need a port between 1 and 65535.";
  }
  if (!(f.latency_warn_ms > 0)) e.latency_warn_ms = "Enter a number above 0.";
  if (!(f.loss_warn_pct >= 0 && f.loss_warn_pct <= 100)) e.loss_warn_pct = "Enter a percentage from 0 to 100.";
  return e;
}

export interface SparkGeometry {
  /** SVG path for the latency line; broken wherever there was no reply. */
  path: string;
  /** DOWN buckets, as small ticks along the bottom edge. */
  marks: Array<{ x: number; w: number }>;
  /** Height of the branch's latency warning threshold, or null when it is nowhere near the data. */
  thresholdY: number | null;
}

/**
 * Geometry for a card's latency sparkline, or null when there is not enough data to draw a trend.
 *
 * The vertical scale is anchored to the branch's own warning threshold (and grows only if the data
 * exceeds it), NOT to the card's own maximum. That keeps cards comparable: a steady 4 ms branch
 * sits low, a branch near its limit sits high, and a spike stands out. Scaling every card to its own
 * maximum would make a calm branch and a struggling one look identical.
 */
export function sparkGeometry(points: SparkPoint[] | undefined, warnMs: number | null, W = 96, H = 28): SparkGeometry | null {
  const pts = points ?? [];
  const nums = pts.map((p) => p.v).filter((v): v is number => v !== null);
  if (pts.length < 3 || (nums.length === 0 && !pts.some((p) => p.d))) return null;

  const top = 4;
  const bottom = H - 4;
  const axisMax = Math.max(warnMs ?? 0, ...nums, 1);
  const y = (v: number) => bottom - (v / axisMax) * (bottom - top);
  const step = W / (pts.length - 1);

  let path = "";
  let pen = false;
  pts.forEach((p, i) => {
    if (p.v === null || p.d) {
      pen = false; // lift the pen where there was no reply
      return;
    }
    path += `${pen ? "L" : "M"}${(i * step).toFixed(1)} ${y(p.v).toFixed(1)} `;
    pen = true;
  });

  const w = Math.min(Math.max(step, 2), 6);
  const marks = pts.flatMap((p, i) => (p.d ? [{ x: Math.min(Math.max(0, i * step - w / 2), W - w), w }] : []));

  // Only draw the threshold once the data gets within half of it; far below, it is just noise.
  const nearLimit = warnMs !== null && nums.length > 0 && Math.max(...nums) >= warnMs * 0.5;
  return { path: path.trim(), marks, thresholdY: nearLimit ? y(warnMs as number) : null };
}

// ---------------------------------------------------------------------------------------------
// Settings screen
// ---------------------------------------------------------------------------------------------

/** What the form holds while you type: numbers as text (so "5." and "" are possible), switches as booleans. */
export type Draft = Record<string, string | boolean>;

export function toDraft(values: Record<string, SettingValue>, keys: string[]): Draft {
  const d: Draft = {};
  for (const k of keys) d[k] = typeof values[k] === "boolean" ? (values[k] as boolean) : String(values[k] ?? "");
  return d;
}

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : String(n));

/** One field's problem, or null. Mirrors the server's rules so mistakes are caught before sending. */
export function settingError(raw: string | boolean, limit: SettingLimit | undefined): string | null {
  if (!limit || limit.kind === "bool") return null;
  if (limit.kind === "choice") return limit.choices?.includes(String(raw)) ? null : "Choose one of the options.";
  const text = String(raw).trim();
  if (limit.kind === "host") {
    if (!text) return "Enter an IP address or hostname.";
    if (/^[\d.]+$/.test(text) && !IPV4.test(text)) return "That is not a valid IP address.";
    if (!IPV4.test(text) && !text.includes(":") && !HOSTNAME.test(text)) return "Enter a valid IP address or hostname.";
    return null;
  }
  if (text === "") return "Enter a number.";
  const n = Number(text);
  if (!Number.isFinite(n)) return "Enter a number.";
  if (limit.kind === "int" && !Number.isInteger(n)) return "Use a whole number.";
  if (limit.min !== undefined && limit.max !== undefined && (n < limit.min || n > limit.max)) {
    return `Use a value between ${fmt(limit.min)} and ${fmt(limit.max)}.`;
  }
  return null;
}

export function settingsErrors(draft: Draft, keys: string[], limits: Record<string, SettingLimit>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of keys) {
    const msg = settingError(draft[k], limits[k]);
    if (msg) out[k] = msg;
  }
  return out;
}

/** The draft value of one field in its real type (number, boolean or trimmed text). */
export function parseSetting(raw: string | boolean, limit: SettingLimit): SettingValue {
  if (limit.kind === "bool") return Boolean(raw);
  if (limit.kind === "int" || limit.kind === "float") return Number(String(raw).trim());
  return String(raw).trim();
}

/** Has this field been edited away from what is currently saved? Compared by value, not by text ("5.0" equals 5). */
export function isChanged(raw: string | boolean, saved: SettingValue, limit: SettingLimit): boolean {
  if (settingError(raw, limit)) return true; // an invalid entry is still an edit
  return parseSetting(raw, limit) !== saved;
}

export function changedKeys(draft: Draft, saved: Record<string, SettingValue>, keys: string[], limits: Record<string, SettingLimit>): string[] {
  return keys.filter((k) => isChanged(draft[k], saved[k], limits[k]));
}

/**
 * What to send for the changed fields. A value equal to the default is sent as null, which RESETS the
 * setting (removes the override) instead of pinning today's default forever.
 */
export function settingsToSend(
  draft: Draft,
  saved: Record<string, SettingValue>,
  defaults: Record<string, SettingValue>,
  keys: string[],
  limits: Record<string, SettingLimit>,
): Record<string, SettingValue | null> {
  const out: Record<string, SettingValue | null> = {};
  for (const k of changedKeys(draft, saved, keys, limits)) {
    const v = parseSetting(draft[k], limits[k]);
    out[k] = v === defaults[k] ? null : v;
  }
  return out;
}

/** Worst case for one probe of an unreachable branch: every ping waits out its timeout. */
export function worstProbeSeconds(count: number, timeoutS: number): number {
  return count * (timeoutS + 0.2);
}

/** About how long an outage takes to be confirmed and alerted. */
export function detectionSeconds(confirm: number, intervalS: number): number {
  return confirm * intervalS;
}

export function humanSeconds(s: number): string {
  if (s < 120) return `${Math.round(s)} seconds`; // 90 seconds reads better than "2 minutes"
  if (s < 5400) return `${Math.round(s / 60)} minutes`;
  return `${(s / 3600).toFixed(1)} hours`;
}

/** Plain-language consequence of the monitoring numbers, or null while any of them is invalid. */
export function monitoringExplanation(draft: Draft): { detect: string; warning: string | null } | null {
  const interval = Number(draft.probe_interval_s);
  const confirm = Number(draft.confirm);
  const count = Number(draft.probe_count);
  const timeout = Number(draft.probe_timeout_s);
  if (![interval, confirm, count, timeout].every((n) => Number.isFinite(n) && n > 0)) return null;
  const worst = worstProbeSeconds(count, timeout);
  return {
    detect: `An outage is alerted after about ${humanSeconds(detectionSeconds(confirm, Math.max(interval, 0)))} (${confirm} matching checks, one every ${interval}s).`,
    warning:
      worst > interval
        ? `Probing an unreachable branch can take up to ${worst.toFixed(1)}s, longer than the ${interval}s interval, so checks will run back to back instead of on schedule. Lower the pings or the timeout, or raise the interval.`
        : null,
  };
}

export function retentionNote(days: number): string | null {
  if (!Number.isFinite(days)) return null;
  if (days === 0) return "Raw checks are kept forever. The database will keep growing.";
  if (days < 30) return `Uptime over 30 days will only cover the last ${days} day${days === 1 ? "" : "s"}, because older checks are deleted.`;
  return null;
}

export function checkPasswordChange(current: string, next: string, repeat: string): Record<string, string> {
  const e: Record<string, string> = {};
  if (!current) e.current = "Enter your current password.";
  if (next.length < 10) e.next = "Use at least 10 characters.";
  else if (next.length > 256) e.next = "Use 256 characters or fewer.";
  else if (current && next === current) e.next = "Choose a different password from the current one.";
  if (repeat !== next) e.repeat = "The two new passwords do not match.";
  return e;
}

/** Numeric value of a dotted IPv4 address, so "10.0.0.9" sorts before "10.0.0.10". */
export function ipToNumber(ip: string): number {
  return ip.split(".").reduce((n, part) => n * 256 + (Number(part) || 0), 0);
}

export type ScanFilter = "all" | HostState;

/** The name to show for a host: the one someone gave it, else what the network calls it. */
export const hostName = (h: ScanHost): string | null => h.label ?? h.hostname;

/** Rows matching the filter and search text (IP, name, MAC or maker), in IP order. */
export function filterScan(rows: ScanHost[], filter: ScanFilter, query: string): ScanHost[] {
  const q = query.trim().toLowerCase();
  const has = (v: string | null) => (v ?? "").toLowerCase().includes(q);
  return rows
    .filter((r) => filter === "all" || r.status === filter)
    .filter((r) => !q || r.ip.includes(q) || has(r.label) || has(r.hostname) || has(r.mac) || has(r.vendor))
    .sort((a, b) => ipToNumber(a.ip) - ipToNumber(b.ip));
}

export function countScan(rows: ScanHost[]): Record<HostState, number> {
  const out: Record<HostState, number> = { online: 0, offline: 0, free: 0 };
  for (const r of rows) out[r.status]++;
  return out;
}

/** After naming an address: a free one becomes assigned (offline), since someone now knows what it is. */
export function withLabel(rows: ScanHost[], ip: string, label: string): ScanHost[] {
  return rows.map((r) => (r.ip === ip ? { ...r, label, known: true, status: r.status === "free" ? "offline" : r.status } : r));
}

/** After forgetting an address: an offline one reads as free again; an online one just loses its name. */
export function withoutMemory(rows: ScanHost[], ip: string): ScanHost[] {
  return rows.map((r) => {
    if (r.ip !== ip) return r;
    const cleared = { ...r, label: null, known: false, last_seen: null };
    return r.status === "offline" ? { ...cleared, status: "free" as const, hostname: null, mac: null, vendor: null, mac_private: false } : cleared;
  });
}
