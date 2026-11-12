import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CircleCheck, SearchX } from "lucide-react";
import { api } from "../api";
import { duration, fmtDateTime, timeAgo } from "../format";
import { useLive } from "../live";
import { filterIncidents, sortIncidents, summarizeIncidents, type IncidentFilters, type IncidentRange } from "../logic";
import { CardsSkeleton, Elapsed, EmptyState, ErrorState } from "../components/States";
import { StatusPill } from "../components/StatusPill";
import { useNow } from "../hooks";
import type { Incident } from "../types";

const PAGE = 50;
const RANGES: Array<{ value: IncidentRange; label: string }> = [
  { value: "24h", label: "Last 24 hours" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "all", label: "All time" },
];

export function Incidents() {
  const { transitionTick } = useLive();
  const now = useNow(60_000);
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState(PAGE);
  const [filters, setFilters] = useState<IncidentFilters>({ openOnly: false, branchId: null, severity: "all", range: "30d" });

  const load = useCallback(() => {
    api
      .incidents()
      .then((r) => {
        setIncidents(r.incidents);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load, transitionTick]);

  const branchOptions = useMemo(() => {
    const m = new Map<number, string>();
    for (const i of incidents ?? []) m.set(i.branch_id, i.branch_name);
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [incidents]);

  const rows = useMemo(() => sortIncidents(filterIncidents(incidents ?? [], filters, now)), [incidents, filters, now]);
  const summary = useMemo(() => summarizeIncidents(rows), [rows]);
  const set = <K extends keyof IncidentFilters>(k: K, v: IncidentFilters[K]) => {
    setFilters((f) => ({ ...f, [k]: v }));
    setShown(PAGE);
  };
  const filtered = filters.openOnly || filters.branchId !== null || filters.severity !== "all" || filters.range !== "30d";

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold tracking-tight">Incidents</h1>

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:flex-wrap md:items-end">
        <div className="md:w-52">
          <label htmlFor="f-branch" className="label">Branch</label>
          <select id="f-branch" className="input" value={filters.branchId ?? ""} onChange={(e) => set("branchId", e.target.value === "" ? null : Number(e.target.value))}>
            <option value="">All branches</option>
            {branchOptions.map(([id, name]) => (
              <option key={id} value={id}>{name}</option>
            ))}
          </select>
        </div>
        <div className="md:w-40">
          <label htmlFor="f-sev" className="label">Severity</label>
          <select id="f-sev" className="input" value={filters.severity} onChange={(e) => set("severity", e.target.value as IncidentFilters["severity"])}>
            <option value="all">All severities</option>
            <option value="DOWN">Down only</option>
            <option value="DEGRADED">Degraded only</option>
          </select>
        </div>
        <div className="md:w-44">
          <label htmlFor="f-range" className="label">Period</label>
          <select id="f-range" className="input" value={filters.range} onChange={(e) => set("range", e.target.value as IncidentRange)}>
            {RANGES.map((r) => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>
        <label className="flex min-h-11 cursor-pointer items-center gap-2.5 text-sm md:min-h-10">
          <input type="checkbox" className="size-5 cursor-pointer accent-[var(--primary)]" checked={filters.openOnly} onChange={(e) => set("openOnly", e.target.checked)} />
          Open only
        </label>
      </div>

      {error && incidents === null ? (
        <ErrorState message={`Could not load incidents: ${error}`} onRetry={load} />
      ) : incidents === null ? (
        <CardsSkeleton count={3} />
      ) : (
        <>
          <p className="mb-3 text-sm text-muted" role="status">
            {summary.total === 0
              ? "No incidents match."
              : `${summary.total} incident${summary.total === 1 ? "" : "s"} · ${summary.open} open${
                  summary.avgResolvedMs !== null ? ` · average resolved in ${duration("1970-01-01T00:00:00Z", new Date(summary.avgResolvedMs).toISOString())}` : ""
                }`}
          </p>

          {rows.length === 0 ? (
            filtered ? (
              <EmptyState
                icon={SearchX}
                title="No incidents match these filters"
                action={
                  <button className="btn btn-secondary" onClick={() => setFilters({ openOnly: false, branchId: null, severity: "all", range: "30d" })}>
                    Reset filters
                  </button>
                }
              />
            ) : (
              <EmptyState icon={CircleCheck} title="No incidents in the last 30 days" text="When a branch goes down or degrades, it will be recorded here." />
            )
          ) : (
            <>
              {/* Phones: stacked cards. Larger screens: a table. */}
              <ul className="space-y-3 md:hidden">
                {rows.slice(0, shown).map((i) => (
                  <li key={i.id} className={`card p-4 ${i.ended_at === null ? (i.severity === "DOWN" ? "tone-down" : "tone-degraded") : ""}`}>
                    <div className="flex items-start justify-between gap-3">
                      <Link to={`/branches/${i.branch_id}`} className="-my-3.5 inline-block py-3.5 font-semibold hover:underline">{i.branch_name}</Link>
                      <StatusPill status={i.severity} />
                    </div>
                    <p className="mt-1 text-sm text-muted">
                      {fmtDateTime(i.started_at)} · <DurationCell i={i} />
                    </p>
                    <p className="mt-1 text-sm">{stateText(i)}</p>
                  </li>
                ))}
              </ul>

              <div className="card hidden overflow-x-auto md:block">
                <table className="w-full min-w-[720px] text-sm">
                  <caption className="sr-only">Incidents, open ones first</caption>
                  <thead className="border-b border-line">
                    <tr>
                      <th scope="col" className="th">Branch</th>
                      <th scope="col" className="th">Severity</th>
                      <th scope="col" className="th">Started</th>
                      <th scope="col" className="th">Duration</th>
                      <th scope="col" className="th">State</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.slice(0, shown).map((i) => (
                      <tr key={i.id} className={`border-t border-line ${i.ended_at === null ? (i.severity === "DOWN" ? "tone-down" : "tone-degraded") : ""}`}>
                        <td className="td font-semibold">
                          <Link to={`/branches/${i.branch_id}`} className="hover:underline">{i.branch_name}</Link>
                        </td>
                        <td className="td"><StatusPill status={i.severity} /></td>
                        <td className="td">
                          <span className="num">{fmtDateTime(i.started_at)}</span>
                          <span className="block text-xs text-muted">{timeAgo(i.started_at, now)}</span>
                        </td>
                        <td className="td num"><DurationCell i={i} /></td>
                        <td className="td">{stateText(i)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {rows.length > shown && (
                <div className="mt-4 text-center">
                  <button className="btn btn-secondary" onClick={() => setShown((n) => n + PAGE)}>
                    Show more ({rows.length - shown} remaining)
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

function DurationCell({ i }: { i: Incident }) {
  return i.ended_at ? <>{duration(i.started_at, i.ended_at)}</> : (
    <>
      <Elapsed since={i.started_at} /> <span className="font-normal text-muted">and counting</span>
    </>
  );
}

function stateText(i: Incident): string {
  if (i.ended_at) return "Resolved";
  return i.acknowledged_by ? `Open · acknowledged by ${i.acknowledged_by}` : "Open · not acknowledged";
}
