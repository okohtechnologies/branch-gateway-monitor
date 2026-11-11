import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { fmtLoss, fmtMs, fmtUptime, timeAgo, duration } from "../format";
import { useLive } from "../live";
import { StatusPill } from "../components/StatusPill";
import type { Branch, Status } from "../types";

const ORDER: Record<Status, number> = { DOWN: 0, DEGRADED: 1, PENDING: 2, UP: 3, DISABLED: 4 };
const FILTERS: Array<"ALL" | Status> = ["ALL", "DOWN", "DEGRADED", "UP", "PENDING", "DISABLED"];

export function Overview() {
  const { snapshots, transitionTick, monitorSuspect, connected, lastCycleAt } = useLive();
  const [branches, setBranches] = useState<Branch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"ALL" | Status>("ALL");
  const [, setNow] = useState(0);

  const load = useCallback(() => {
    api
      .branches()
      .then((r) => {
        setBranches(r.branches);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  // Refetch on status changes and every minute (uptime moves slowly).
  useEffect(load, [load, transitionTick]);
  useEffect(() => {
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  // A branch we have not loaded yet appears in the live feed: fetch the list again.
  const unknown = useMemo(
    () => branches !== null && Object.keys(snapshots).some((id) => !branches.some((b) => b.id === Number(id))),
    [branches, snapshots],
  );
  useEffect(() => {
    if (unknown) load();
  }, [unknown, load]);

  // Re-render every 15s so "x ago" labels stay honest between updates.
  useEffect(() => {
    const t = setInterval(() => setNow((n) => n + 1), 15_000);
    return () => clearInterval(t);
  }, []);

  const merged = useMemo(
    () =>
      (branches ?? []).map((b) => {
        const s = snapshots[b.id];
        if (!s || !b.enabled) return b;
        return { ...b, latency_ms: s.avg_latency_ms, loss_pct: s.loss_pct, last_check_at: s.ts, status: s.status };
      }),
    [branches, snapshots],
  );

  const counts = useMemo(() => {
    const c = { UP: 0, DEGRADED: 0, DOWN: 0, PENDING: 0, DISABLED: 0 };
    for (const b of merged) c[b.status]++;
    return c;
  }, [merged]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return merged
      .filter((b) => (filter === "ALL" || b.status === filter) && (!q || b.name.toLowerCase().includes(q)))
      .sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.name.localeCompare(b.name));
  }, [merged, query, filter]);

  if (error && branches === null) return <p role="alert" className="text-down">Could not load branches: {error}</p>;
  if (branches === null) return <p className="text-muted">Loading…</p>;

  return (
    <div>
      {monitorSuspect && (
        <div role="alert" className="mb-4 rounded-lg border border-degraded/50 bg-degraded/10 p-3 text-sm">
          <strong className="text-degraded">Monitor may be offline.</strong> Every branch and the external canary are
          unreachable at once, so the monitor's own network is the likely cause. Per-branch alerts are paused.
        </div>
      )}

      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Summary">
        <Stat label="Up" value={counts.UP} tone="text-up" />
        <Stat label="Degraded" value={counts.DEGRADED} tone="text-degraded" />
        <Stat label="Down" value={counts.DOWN} tone="text-down" />
        <Stat label="Total" value={branches.length} tone="text-ink" />
      </div>

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search branches"
          aria-label="Search branches by name"
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm placeholder:text-muted sm:max-w-xs"
        />
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
          {FILTERS.map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              aria-pressed={filter === f}
              className={`rounded-full border px-3 py-1 text-xs font-medium ${
                filter === f ? "border-accent bg-accent/15 text-ink" : "border-line text-muted hover:text-ink"
              }`}
            >
              {f === "ALL" ? "All" : f[0] + f.slice(1).toLowerCase()}
            </button>
          ))}
        </div>
        <span className="text-xs text-muted sm:ml-auto">
          {connected && lastCycleAt ? `Updated ${timeAgo(lastCycleAt)}` : connected ? "Waiting for first probe…" : "Live updates paused"}
        </span>
      </div>

      {branches.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line p-10 text-center">
          <p className="font-medium">No branches yet</p>
          <p className="mt-1 text-sm text-muted">Add your first branch to start monitoring.</p>
          <Link to="/branches?new=1" className="mt-4 inline-block rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg">
            Add a branch
          </Link>
        </div>
      ) : visible.length === 0 ? (
        <p className="text-muted">No branches match your filters.</p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((b) => (
            <li key={b.id}>
              <BranchCard b={b} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <div className={`text-2xl font-bold tabular-nums ${tone}`}>{value}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  );
}

function BranchCard({ b }: { b: Branch }) {
  const border =
    b.status === "DOWN" ? "border-down/60" : b.status === "DEGRADED" ? "border-degraded/60" : "border-line";
  return (
    <Link
      to={`/branches/${b.id}`}
      className={`block rounded-xl border ${border} bg-surface p-4 transition-colors hover:bg-surface-2`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate font-semibold">{b.name}</h3>
          <p className="truncate font-mono text-xs text-muted">{b.host}</p>
        </div>
        <StatusPill status={b.status} />
      </div>

      <dl className="mt-4 grid grid-cols-3 gap-2 text-sm">
        <Metric label="Latency" value={fmtMs(b.latency_ms)} />
        <Metric label="Loss" value={fmtLoss(b.loss_pct)} />
        <Metric label="24h uptime" value={fmtUptime(b.uptime_24h)} />
      </dl>

      <p className="mt-3 truncate text-xs text-muted">
        {b.incident_started_at
          ? `${b.status === "DOWN" ? "Down" : "Degraded"} for ${duration(b.incident_started_at)}${
              b.acknowledged_by ? ` · ack by ${b.acknowledged_by}` : ""
            }`
          : b.location || `Checked ${timeAgo(b.last_check_at)}`}
      </p>
    </Link>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] uppercase tracking-wide text-muted">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}
