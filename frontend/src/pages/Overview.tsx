import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Building2, CircleCheck, CircleX, LayoutGrid, Plus, Rows3, Search, SearchX, TriangleAlert, type LucideIcon } from "lucide-react";
import { api } from "../api";
import { fmtAge, fmtLoss, fmtMs, fmtUptime, timeAgo } from "../format";
import { useMediaQuery, useStoredChoice } from "../hooks";
import { useFreshness, useLive } from "../live";
import { countByStatus, filterBranches, parseFilter, sortBranches, type StatusFilter } from "../logic";
import { Sparkline } from "../components/Sparkline";
import { CardsSkeleton, Elapsed, EmptyState, ErrorState } from "../components/States";
import { StatusPill } from "../components/StatusPill";
import type { Branch } from "../types";

const VIEWS = ["cards", "table"] as const;
const AUTO_TABLE_ABOVE = 18; // beyond this many branches, a dense table scans better than cards

export function Overview() {
  const { snapshots, transitionTick } = useLive();
  const fresh = useFreshness();
  const [branches, setBranches] = useState<Branch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [params, setParams] = useSearchParams();
  const [viewPref, setViewPref] = useStoredChoice("overview-view", VIEWS);
  const wide = useMediaQuery("(min-width: 768px)");

  const filter = parseFilter(params.get("status"));
  const setFilter = (f: StatusFilter) => setParams(f === "all" ? {} : { status: f }, { replace: true });

  const load = useCallback(() => {
    api
      .branches()
      .then((r) => {
        setBranches(r.branches);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  // Refetch on status changes and every minute (uptime and sparklines move slowly).
  useEffect(load, [load, transitionTick]);
  useEffect(() => {
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, [load]);

  // A branch we have not loaded yet shows up in the live feed (added elsewhere): fetch again.
  const unknown = useMemo(
    () => branches !== null && Object.keys(snapshots).some((id) => !branches.some((b) => b.id === Number(id))),
    [branches, snapshots],
  );
  useEffect(() => {
    if (unknown) load();
  }, [unknown, load]);

  const merged = useMemo(
    () =>
      (branches ?? []).map((b) => {
        const s = snapshots[b.id];
        if (!s || !b.enabled) return b;
        return { ...b, latency_ms: s.avg_latency_ms, loss_pct: s.loss_pct, last_check_at: s.ts, status: s.status };
      }),
    [branches, snapshots],
  );

  const counts = useMemo(() => countByStatus(merged), [merged]);
  const visible = useMemo(() => sortBranches(filterBranches(merged, query, filter)), [merged, query, filter]);

  if (error && branches === null) {
    return (
      <>
        <PageHeader />
        <ErrorState message={`Could not load branches: ${error}`} onRetry={load} />
      </>
    );
  }
  if (branches === null) {
    return (
      <>
        <PageHeader />
        <CardsSkeleton />
      </>
    );
  }

  const view = wide ? (viewPref ?? (branches.length > AUTO_TABLE_ABOVE ? "table" : "cards")) : "cards";
  const allHealthy = branches.length > 0 && counts.DOWN === 0 && counts.DEGRADED === 0;

  return (
    <div>
      <PageHeader>
        <span className={`text-xs ${fresh.stale ? "font-semibold text-degraded" : "text-muted"}`} role="status">
          {fresh.ageS === null
            ? fresh.connected
              ? "Waiting for the first probe…"
              : "Live updates paused"
            : fresh.stale
              ? `Stale · last update ${fmtAge(fresh.ageS)}`
              : `Updated ${fmtAge(fresh.ageS)}`}
        </span>
      </PageHeader>

      {branches.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No branches yet"
          text="Add the gateway of your first branch and monitoring starts within seconds."
          action={
            <Link to="/branches?new=1" className="btn btn-primary">
              <Plus className="size-4" aria-hidden /> Add a branch
            </Link>
          }
        />
      ) : (
        <>
          <section className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Summary">
            <Tile icon={CircleX} label="Down" count={counts.DOWN} tone="down" active={filter === "down"} onClick={() => setFilter(filter === "down" ? "all" : "down")} />
            <Tile icon={TriangleAlert} label="Degraded" count={counts.DEGRADED} tone="degraded" active={filter === "degraded"} onClick={() => setFilter(filter === "degraded" ? "all" : "degraded")} />
            <Tile icon={CircleCheck} label="Up" count={counts.UP} tone="up" active={filter === "up"} onClick={() => setFilter(filter === "up" ? "all" : "up")} />
            <Tile icon={Building2} label="All branches" count={branches.length} tone="none" active={filter === "all"} onClick={() => setFilter("all")} />
          </section>

          <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center">
            <div className="relative md:w-72">
              <label htmlFor="branch-search" className="sr-only">
                Search branches by name, address or location
              </label>
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
              <input
                id="branch-search"
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search name or address"
                className="input pl-9"
                autoComplete="off"
              />
            </div>
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="More filters">
              {counts.PENDING > 0 && (
                <button className="chip" aria-pressed={filter === "pending"} onClick={() => setFilter(filter === "pending" ? "all" : "pending")}>
                  Pending <span className="num">{counts.PENDING}</span>
                </button>
              )}
              {counts.DISABLED > 0 && (
                <button className="chip" aria-pressed={filter === "disabled"} onClick={() => setFilter(filter === "disabled" ? "all" : "disabled")}>
                  Disabled <span className="num">{counts.DISABLED}</span>
                </button>
              )}
            </div>
            <div className="hidden items-center gap-1 md:ml-auto md:flex" role="group" aria-label="View">
              <button className="icon-btn" aria-pressed={view === "cards"} aria-label="Card view" title="Card view" onClick={() => setViewPref("cards")}>
                <LayoutGrid className={`size-5 ${view === "cards" ? "text-ink" : ""}`} aria-hidden />
              </button>
              <button className="icon-btn" aria-pressed={view === "table"} aria-label="Table view" title="Table view" onClick={() => setViewPref("table")}>
                <Rows3 className={`size-5 ${view === "table" ? "text-ink" : ""}`} aria-hidden />
              </button>
            </div>
          </div>

          {allHealthy && filter === "all" && !query && (
            <p className="mb-4 flex items-center gap-2 text-sm text-muted">
              <CircleCheck className="size-4 text-up" aria-hidden />
              All {branches.length} branch{branches.length === 1 ? "" : "es"} {branches.length === 1 ? "is" : "are"} up.
            </p>
          )}

          {visible.length === 0 ? (
            <EmptyState
              icon={SearchX}
              title="No branches match your filters"
              action={
                <button
                  className="btn btn-secondary"
                  onClick={() => {
                    setQuery("");
                    setFilter("all");
                  }}
                >
                  Clear filters
                </button>
              }
            />
          ) : view === "table" ? (
            <BranchTable branches={visible} />
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {visible.map((b) => (
                <li key={b.id}>
                  <BranchCard b={b} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function PageHeader({ children }: { children?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-3">
      <h1 className="text-xl font-semibold tracking-tight">Overview</h1>
      {children}
    </div>
  );
}

const TONE = {
  down: { text: "text-down", wash: "tone-down" },
  degraded: { text: "text-degraded", wash: "tone-degraded" },
  up: { text: "text-up", wash: "" },
  none: { text: "text-ink", wash: "" },
} as const;

function Tile({ icon: Icon, label, count, tone, active, onClick }: { icon: LucideIcon; label: string; count: number; tone: keyof typeof TONE; active: boolean; onClick: () => void }) {
  const t = TONE[tone];
  const alert = (tone === "down" || tone === "degraded") && count > 0;
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`card flex min-h-[4.5rem] cursor-pointer items-center gap-3 p-3.5 text-left transition-colors duration-150 hover:bg-card2 ${alert ? t.wash : ""} ${active ? "ring-2 ring-accent" : ""}`}
    >
      <Icon className={`size-6 shrink-0 ${alert || tone === "up" ? t.text : "text-muted"}`} aria-hidden />
      <span>
        <span className={`num block text-2xl leading-none font-semibold ${alert || tone === "up" ? t.text : "text-ink"}`}>{count}</span>
        <span className="mt-1 block text-xs text-muted">{label}</span>
      </span>
    </button>
  );
}

function latencyText(b: Branch): string {
  if (b.status === "PENDING" || b.status === "DISABLED") return "—";
  return b.status === "DOWN" || b.latency_ms == null ? "No reply" : fmtMs(b.latency_ms);
}

function incidentLine(b: Branch) {
  if (b.incident_started_at && (b.status === "DOWN" || b.status === "DEGRADED")) {
    return (
      <>
        {b.status === "DOWN" ? "Down" : "Degraded"} for <Elapsed since={b.incident_started_at} />
        {" · "}
        {b.acknowledged_by ? `Acknowledged by ${b.acknowledged_by}` : <span className="font-medium text-ink">Not acknowledged</span>}
      </>
    );
  }
  return null;
}

const BranchCard = memo(function BranchCard({ b }: { b: Branch }) {
  const tone = b.status === "DOWN" ? "tone-down" : b.status === "DEGRADED" ? "tone-degraded" : "";
  const bad = b.status === "DOWN";
  const incident = incidentLine(b);
  return (
    <Link to={`/branches/${b.id}`} className={`card block p-4 transition-colors duration-150 hover:bg-card2 ${tone}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate font-semibold">{b.name}</h2>
          <p className="mono truncate text-xs text-muted">
            {b.host}
            {b.probe_type === "tcp" ? `:${b.port}` : ""}
          </p>
        </div>
        <StatusPill status={b.status} />
      </div>

      <div className="mt-3 flex items-end justify-between gap-3">
        <dl className="grid flex-1 grid-cols-3 gap-2">
          <Metric label="Latency" value={latencyText(b)} tone={bad ? "text-down" : ""} />
          <Metric label="Loss" value={b.status === "PENDING" || b.status === "DISABLED" ? "—" : fmtLoss(b.loss_pct)} tone={(b.loss_pct ?? 0) > 0 ? "text-degraded" : ""} />
          <Metric label="24h uptime" value={fmtUptime(b.uptime_24h)} />
        </dl>
        <Sparkline points={b.spark} />
      </div>

      <p className="mt-3 truncate text-xs text-muted">
        {incident ?? (b.status === "DISABLED" ? "Monitoring is off" : b.location ? `${b.location} · checked ${timeAgo(b.last_check_at)}` : `Checked ${timeAgo(b.last_check_at)}`)}
      </p>
    </Link>
  );
});

function Metric({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs tracking-wide text-muted uppercase">{label}</dt>
      <dd className={`num truncate text-sm font-semibold ${tone}`}>{value}</dd>
    </div>
  );
}

function BranchTable({ branches }: { branches: Branch[] }) {
  const navigate = useNavigate();
  return (
    <div className="card overflow-x-auto">
      <table className="w-full min-w-[820px] text-sm">
        <caption className="sr-only">Branches, problems first</caption>
        <thead className="border-b border-line">
          <tr>
            <th scope="col" className="th">Status</th>
            <th scope="col" className="th">Branch</th>
            <th scope="col" className="th text-right">Latency</th>
            <th scope="col" className="th text-right">Loss</th>
            <th scope="col" className="th text-right">24h uptime</th>
            <th scope="col" className="th">Last hour</th>
            <th scope="col" className="th">Incident</th>
          </tr>
        </thead>
        <tbody>
          {branches.map((b) => {
            const incident = incidentLine(b);
            return (
              <tr
                key={b.id}
                onClick={() => navigate(`/branches/${b.id}`)}
                className={`cursor-pointer border-t border-line transition-colors duration-150 hover:bg-card2 ${b.status === "DOWN" ? "tone-down" : b.status === "DEGRADED" ? "tone-degraded" : ""}`}
              >
                <td className="td"><StatusPill status={b.status} /></td>
                <td className="td">
                  <Link to={`/branches/${b.id}`} className="font-semibold hover:underline" onClick={(e) => e.stopPropagation()}>
                    {b.name}
                  </Link>
                  <div className="mono text-xs text-muted">
                    {b.host}
                    {b.probe_type === "tcp" ? `:${b.port}` : ""}
                  </div>
                </td>
                <td className={`td num text-right font-medium ${b.status === "DOWN" ? "text-down" : ""}`}>{latencyText(b)}</td>
                <td className={`td num text-right ${(b.loss_pct ?? 0) > 0 ? "text-degraded" : ""}`}>{b.status === "PENDING" || b.status === "DISABLED" ? "—" : fmtLoss(b.loss_pct)}</td>
                <td className="td num text-right">{fmtUptime(b.uptime_24h)}</td>
                <td className="td"><Sparkline points={b.spark} /></td>
                <td className="td text-xs text-muted">{incident ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
