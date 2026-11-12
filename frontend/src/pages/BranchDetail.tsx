import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ArrowLeft, Pencil, Power } from "lucide-react";
import { api, ApiError } from "../api";
import { duration, fmtDateTime, fmtLoss, fmtMs, fmtUptime } from "../format";
import { useFreshness, useLive } from "../live";
import { chartSummary, outageSegments, toChartPoints } from "../logic";
import { useToast } from "../toast";
import { Elapsed, EmptyState, ErrorState, } from "../components/States";
import { StatusIcon, StatusPill, STATUS_LABEL } from "../components/StatusPill";
import { BarChart3 } from "lucide-react";
import type { BranchDetail as Detail, Incident, Point, Range, Status } from "../types";

const RANGES: Range[] = ["1h", "24h", "7d"];
const RANGE_LABEL: Record<Range, string> = { "1h": "1 hour", "24h": "24 hours", "7d": "7 days" };

const tooltipStyle = {
  background: "var(--card)",
  border: "1px solid var(--line-strong)",
  borderRadius: 8,
  color: "var(--text)",
  fontSize: 12,
};

function tickFormat(range: Range) {
  return (t: number) => {
    const d = new Date(t);
    return range === "7d"
      ? d.toLocaleDateString(undefined, { weekday: "short", day: "numeric" })
      : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  };
}

export function BranchDetail() {
  const { id } = useParams();
  const branchId = Number(id);
  const { cycleTick, cycleIntervalS, snapshots } = useLive();
  const fresh = useFreshness();
  const toast = useToast();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [points, setPoints] = useState<Point[] | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [range, setRange] = useState<Range>("1h");
  const [error, setError] = useState<{ message: string; notFound: boolean } | null>(null);
  const [reload, setReload] = useState(0);

  // The probe cycle can be a few seconds; heavy history queries refresh about every 15-30s instead.
  const every = (s: number) => Math.max(1, Math.round(s / (cycleIntervalS ?? 30)));
  const detailTick = Math.floor(cycleTick / every(15));
  const chartTick = Math.floor(cycleTick / every(30));

  const fail = useCallback((e: unknown) => {
    const notFound = e instanceof ApiError && e.status === 404;
    setError({ message: notFound ? "This branch does not exist, or it was deleted." : "Could not load this branch.", notFound });
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.branch(branchId), api.incidents({ branchId })])
      .then(([d, i]) => {
        if (cancelled) return;
        setDetail(d);
        setIncidents(i.incidents);
        setError(null);
      })
      .catch((e) => !cancelled && fail(e));
    return () => {
      cancelled = true;
    };
  }, [branchId, detailTick, reload, fail]);

  useEffect(() => {
    let cancelled = false;
    api
      .checks(branchId, range)
      .then((c) => !cancelled && setPoints(c.points))
      .catch((e) => !cancelled && fail(e));
    return () => {
      cancelled = true;
    };
  }, [branchId, range, chartTick, reload, fail]);

  const data = useMemo(() => toChartPoints(points ?? []), [points]);
  const outages = useMemo(() => outageSegments(data), [data]);
  const summary = useMemo(() => chartSummary(data), [data]);

  const enable = async () => {
    if (!detail) return;
    try {
      await api.updateBranch(detail.id, {
        name: detail.name,
        host: detail.host,
        location: detail.location,
        probe_type: detail.probe_type,
        port: detail.port,
        latency_warn_ms: detail.latency_warn_ms,
        loss_warn_pct: detail.loss_warn_pct,
        enabled: true,
      });
      toast.success(`Monitoring enabled for ${detail.name}.`);
      setReload((n) => n + 1);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not enable monitoring.");
    }
  };

  if (error) {
    return (
      <div>
        <BackLink />
        {error.notFound ? (
          <EmptyState icon={BarChart3} title="Branch not found" text={error.message} action={<Link to="/" className="btn btn-secondary">Back to overview</Link>} />
        ) : (
          <ErrorState message={error.message} onRetry={() => setReload((n) => n + 1)} />
        )}
      </div>
    );
  }
  if (!detail) return <DetailSkeleton />;

  const live = snapshots[branchId];
  const status: Status = !detail.enabled ? "DISABLED" : live ? live.status : detail.status;
  const latency = live ? live.avg_latency_ms : detail.latency_ms;
  const loss = live ? live.loss_pct : detail.loss_pct;
  const incident = detail.open_incident;

  return (
    <div>
      <BackLink />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{detail.name}</h1>
            <StatusPill status={status} />
          </div>
          <p className="mono mt-1 text-sm text-muted">
            {detail.host}
            {detail.probe_type === "tcp" ? `:${detail.port}` : ""} · {detail.probe_type.toUpperCase()}
            {detail.location ? ` · ${detail.location}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!detail.enabled && (
            <button className="btn btn-primary" onClick={enable}>
              <Power className="size-4" aria-hidden /> Enable monitoring
            </button>
          )}
          <Link to={`/branches?edit=${detail.id}`} className="btn btn-secondary">
            <Pencil className="size-4" aria-hidden /> Edit
          </Link>
        </div>
      </div>

      {!detail.enabled && (
        <p className="card mt-4 p-3 text-sm text-muted">Monitoring is off for this branch. It is not being probed, so no alerts will be sent.</p>
      )}

      {incident && (
        <div className={`${incident.severity === "DOWN" ? "banner-down" : "banner-degraded"} mt-4 flex items-start gap-3 rounded-xl border p-3.5`}>
          <StatusIcon status={incident.severity} className="mt-0.5 size-5 shrink-0" />
          <div className="min-w-0 text-sm">
            <p className="font-semibold">
              {incident.severity === "DOWN" ? "Outage" : "Degraded"} for <Elapsed since={incident.started_at} />
              <span className="font-normal text-muted"> · since {fmtDateTime(incident.started_at)}</span>
            </p>
            <p className="mt-0.5 text-muted">
              {incident.acknowledged_by
                ? `Acknowledged by ${incident.acknowledged_by}.`
                : "Not acknowledged yet. Acknowledge it from the Telegram alert to stop reminders."}
            </p>
          </div>
        </div>
      )}

      <section className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5 [&>*:last-child]:col-span-2 sm:[&>*:last-child]:col-span-1" aria-label="Current figures">
        <Tile label="Latency now" value={status === "DOWN" ? "No reply" : fmtMs(latency)} tone={status === "DOWN" ? "text-down" : ""} />
        <Tile label="Packet loss now" value={fmtLoss(loss)} tone={status === "DOWN" ? "text-down" : (loss ?? 0) > 0 ? "text-degraded" : ""} />
        <Tile label="Uptime 24h" value={fmtUptime(detail.uptime["24h"])} />
        <Tile label="Uptime 7d" value={fmtUptime(detail.uptime["7d"])} />
        <Tile label="Uptime 30d" value={fmtUptime(detail.uptime["30d"])} />
      </section>

      <div className="mt-7 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-base font-semibold">History</h2>
        <div className="flex items-center gap-3">
          <span className={`text-xs ${fresh.stale ? "font-semibold text-degraded" : "text-muted"}`}>{fresh.stale ? "Data may be stale" : ""}</span>
          <div className="inline-flex gap-1" role="group" aria-label="Time range">
            {RANGES.map((r) => (
              <button key={r} className="chip" aria-pressed={range === r} onClick={() => setRange(r)}>
                {r}
              </button>
            ))}
          </div>
        </div>
      </div>

      {points === null ? (
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <div className="skeleton h-64" />
          <div className="skeleton h-64" />
        </div>
      ) : data.length === 0 ? (
        <div className="mt-3">
          <EmptyState icon={BarChart3} title="No checks in this range yet" text="Data appears after the first probe cycle completes." />
        </div>
      ) : (
        <>
          <p className="mt-2 text-sm text-muted" id="chart-summary">
            Last {RANGE_LABEL[range]}: {summary.samples} samples, average latency {fmtMs(summary.avgLatency)}, peak {fmtMs(summary.maxLatency)},{" "}
            {summary.outages === 0 ? "no outages" : `${summary.outages} outage${summary.outages === 1 ? "" : "s"} (shaded)`}.
          </p>
          <div className="mt-3 grid gap-4 lg:grid-cols-2">
            <ChartCard title="Latency (ms)" describedBy="chart-summary" emptyNote={summary.avgLatency === null ? "No replies received in this period." : undefined}>
              <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                <CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={tickFormat(range)} stroke="var(--muted)" fontSize={11} tickLine={false} />
                <YAxis stroke="var(--muted)" fontSize={11} tickLine={false} axisLine={false} width={44} />
                <Tooltip contentStyle={tooltipStyle} labelFormatter={(t) => fmtDateTime(new Date(Number(t)).toISOString())} formatter={(v) => [fmtMs(v as number), "Latency"]} />
                {outages.map(([x1, x2], i) => (
                  <ReferenceArea key={i} x1={x1} x2={x2} ifOverflow="hidden" fill="var(--down)" fillOpacity={0.22} stroke="var(--down)" strokeOpacity={0.6} strokeDasharray="3 3" />
                ))}
                <ReferenceLine y={detail.latency_warn_ms} stroke="var(--degraded)" strokeDasharray="5 4" label={{ value: `warn ${detail.latency_warn_ms} ms`, fill: "var(--degraded)", fontSize: 10, position: "insideTopRight" }} />
                <Area type="monotone" dataKey="latency" stroke="var(--accent)" fill="var(--accent)" fillOpacity={0.15} strokeWidth={2} connectNulls={false} dot={false} isAnimationActive={false} />
              </AreaChart>
            </ChartCard>
            <ChartCard title="Packet loss (%)" describedBy="chart-summary">
              <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
                <CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={tickFormat(range)} stroke="var(--muted)" fontSize={11} tickLine={false} />
                <YAxis domain={[0, 100]} stroke="var(--muted)" fontSize={11} tickLine={false} axisLine={false} width={44} />
                <Tooltip contentStyle={tooltipStyle} labelFormatter={(t) => fmtDateTime(new Date(Number(t)).toISOString())} formatter={(v) => [fmtLoss(v as number), "Loss"]} />
                {outages.map(([x1, x2], i) => (
                  <ReferenceArea key={i} x1={x1} x2={x2} ifOverflow="hidden" fill="var(--down)" fillOpacity={0.22} stroke="var(--down)" strokeOpacity={0.6} strokeDasharray="3 3" />
                ))}
                <Bar dataKey="loss" fill="var(--down)" isAnimationActive={false} />
              </BarChart>
            </ChartCard>
          </div>

          <details className="card mt-3 p-3 text-sm">
            <summary className="cursor-pointer font-medium">View recent checks as a table</summary>
            <div className="mt-3 max-h-64 overflow-auto" tabIndex={0} role="region" aria-label="Recent checks, scrollable">
              <table className="w-full text-left">
                <caption className="sr-only">Most recent checks in the selected range</caption>
                <thead>
                  <tr>
                    <th scope="col" className="th">Time</th>
                    <th scope="col" className="th text-right">Latency</th>
                    <th scope="col" className="th text-right">Loss</th>
                    <th scope="col" className="th">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[...(points ?? [])].reverse().slice(0, 50).map((p) => (
                    <tr key={p.ts} className="border-t border-line">
                      <td className="td num">{fmtDateTime(p.ts)}</td>
                      <td className="td num text-right">{p.latency_ms === null ? "No reply" : fmtMs(p.latency_ms)}</td>
                      <td className="td num text-right">{fmtLoss(p.loss_pct)}</td>
                      <td className="td">{STATUS_LABEL[p.status]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}

      <h2 className="mt-8 mb-2 text-base font-semibold">Incidents</h2>
      {incidents.length === 0 ? (
        <p className="text-sm text-muted">No incidents recorded for this branch.</p>
      ) : (
        <>
          <ul className="space-y-2 md:hidden">
            {incidents.map((i) => (
              <li key={i.id} className="card p-3.5">
                <div className="flex items-center justify-between gap-3">
                  <StatusPill status={i.severity} />
                  <span className="num text-sm font-semibold">{duration(i.started_at, i.ended_at)}</span>
                </div>
                <p className="num mt-2 text-sm text-muted">{fmtDateTime(i.started_at)}</p>
                <p className="mt-0.5 text-sm">{i.ended_at ? "Resolved" : i.acknowledged_by ? `Open · acknowledged by ${i.acknowledged_by}` : "Open · not acknowledged"}</p>
              </li>
            ))}
          </ul>
          <div className="card hidden overflow-x-auto md:block">
            <table className="w-full text-sm">
              <thead className="border-b border-line">
                <tr>
                  <th scope="col" className="th">Severity</th>
                  <th scope="col" className="th">Started</th>
                  <th scope="col" className="th">Duration</th>
                  <th scope="col" className="th">State</th>
                </tr>
              </thead>
              <tbody>
                {incidents.map((i) => (
                  <tr key={i.id} className="border-t border-line">
                    <td className="td"><StatusPill status={i.severity} /></td>
                    <td className="td num">{fmtDateTime(i.started_at)}</td>
                    <td className="td num">{duration(i.started_at, i.ended_at)}</td>
                    <td className="td">{i.ended_at ? "Resolved" : i.acknowledged_by ? `Open · acknowledged by ${i.acknowledged_by}` : "Open"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2 className="mt-8 mb-2 text-base font-semibold">Configuration</h2>
      <dl className="card grid gap-x-6 gap-y-3 p-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <Config label="Probe" value={detail.probe_type === "tcp" ? `TCP connect, port ${detail.port}` : "ICMP ping"} />
        <Config label="Warns above latency" value={fmtMs(detail.latency_warn_ms)} />
        <Config label="Warns above loss" value={`${detail.loss_warn_pct}%`} />
        <Config label="Monitoring" value={detail.enabled ? "On" : "Off"} />
      </dl>
    </div>
  );
}

function BackLink() {
  return (
    <Link to="/" className="mb-3 inline-flex min-h-11 items-center md:min-h-9 gap-1.5 text-sm font-medium text-accent hover:underline">
      <ArrowLeft className="size-4" aria-hidden /> Overview
    </Link>
  );
}

function Tile({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="card p-3.5">
      <div className={`num text-xl leading-tight font-semibold ${tone}`}>{value}</div>
      <div className="mt-0.5 text-xs text-muted">{label}</div>
    </div>
  );
}

function Config({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}

function ChartCard({ title, describedBy, emptyNote, children }: { title: string; describedBy: string; emptyNote?: string; children: React.ReactElement }) {
  return (
    <section className="card p-4" aria-label={title} aria-describedby={describedBy}>
      <h3 className="mb-2 text-sm font-medium text-muted">{title}</h3>
      <div className="relative h-56 sm:h-60">
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
        {emptyNote && (
          <p className="pointer-events-none absolute inset-0 grid place-items-center pb-6 text-sm font-medium text-muted">{emptyNote}</p>
        )}
      </div>
    </section>
  );
}

function DetailSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading branch">
      <div className="skeleton mb-4 h-5 w-24" />
      <div className="skeleton mb-2 h-8 w-64" />
      <div className="skeleton mb-6 h-4 w-48" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="skeleton h-16" />
        ))}
      </div>
      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <div className="skeleton h-64" />
        <div className="skeleton h-64" />
      </div>
    </div>
  );
}
