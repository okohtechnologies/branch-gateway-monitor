export const fmtMs = (v: number | null | undefined): string =>
  v == null ? "—" : v >= 100 ? `${Math.round(v)} ms` : `${v.toFixed(1)} ms`;

export const fmtLoss = (v: number | null | undefined): string =>
  v == null ? "—" : `${Number.isInteger(v) ? v : v.toFixed(1)}%`;

/** Uptime never rounds up to 100% while any check has failed. */
export function fmtUptime(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v >= 100) return "100%";
  return `${Math.min(v, 99.99).toFixed(2)}%`;
}

export function timeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function duration(startIso: string, endIso?: string | null, now = Date.now()): string {
  const end = endIso ? new Date(endIso).getTime() : now;
  const s = Math.max(0, Math.round((end - new Date(startIso).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export const fmtDateTime = (iso: string): string =>
  new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
