/** Pure helpers behind the screens. No React, no I/O: everything here is unit-tested. */
import type { Branch, BranchInput, Incident, Point, Status } from "./types";

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
