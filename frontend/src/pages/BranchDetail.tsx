import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, ApiError } from "../api";
import { duration, fmtDateTime, fmtLoss, fmtMs, fmtUptime } from "../format";
import { useLive } from "../live";
import { StatusPill } from "../components/StatusPill";
import type { BranchDetail as Detail, Incident, Point, Range, Status } from "../types";

const RANGES: Range[] = ["1h", "24h", "7d"];
const tooltipStyle = {
  background: "var(--surface)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  color: "var(--text)",
  fontSize: 12,
};

function tick(range: Range) {
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
  const { cycleTick, snapshots } = useLive();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [points, setPoints] = useState<Point[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [range, setRange] = useState<Range>("1h");
  const [error, setError] = useState<string | null>(null);

  // Refresh on every probe cycle (about every 30s): cheap queries, always current.
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.branch(branchId), api.checks(branchId, range), api.incidents({ branchId })])
      .then(([d, c, i]) => {
        if (cancelled) return;
        setDetail(d);
        setPoints(c.points);
        setIncidents(i.incidents);
        setError(null);
      })
      .catch((e: unknown) => !cancelled && setError(e instanceof ApiError && e.status === 404 ? "Branch not found." : "Could not load this branch."));
    return () => {
      cancelled = true;
    };
  }, [branchId, range, cycleTick]);

  const data = useMemo(
    () => points.map((p) => ({ t: new Date(p.ts).getTime(), latency: p.latency_ms, loss: p.loss_pct })),
    [points],
  );

  if (error) {
    return (
      <div>
        <p role="alert" className="text-down">{error}</p>
        <Link to="/" className="text-sm text-accent">← Back to overview</Link>
      </div>
    );
  }
  if (!detail) return <p className="text-muted">Loading…</p>;

  const live = snapshots[branchId];
  const status: Status = detail.enabled ? (live ? live.status : detail.status) : "DISABLED";
  const latency = live ? live.avg_latency_ms : detail.latency_ms;
  const loss = live ? live.loss_pct : detail.loss_pct;

  return (
    <div>
      <Link to="/" className="text-sm text-accent">← Overview</Link>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{detail.name}</h1>
          <p className="font-mono text-sm text-muted">
            {detail.host}
            {detail.probe_type === "tcp" ? `:${detail.port}` : ""} · {detail.probe_type.toUpperCase()}
            {detail.location ? ` · ${detail.location}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <StatusPill status={status} />
          <Link to={`/branches?edit=${detail.id}`} className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-surface-2">
            Edit
          </Link>
        </div>
      </div>

      {detail.open_incident && (
        <div role="alert" className="mt-4 rounded-lg border border-down/50 bg-down/10 p-3 text-sm">
          <strong className="text-down">{detail.open_incident.severity === "DOWN" ? "Outage" : "Degraded"}</strong> since{" "}
          {fmtDateTime(detail.open_incident.started_at)} ({duration(detail.open_incident.started_at)})
          {detail.open_incident.acknowledged_by && ` · acknowledged by ${detail.open_incident.acknowledged_by}`}
        </div>
      )}

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Tile label="Latency" value={fmtMs(latency)} />
        <Tile label="Packet loss" value={fmtLoss(loss)} />
        <Tile label="Uptime 24h" value={fmtUptime(detail.uptime["24h"])} />
        <Tile label="Uptime 7d" value={fmtUptime(detail.uptime["7d"])} />
        <Tile label="Uptime 30d" value={fmtUptime(detail.uptime["30d"])} />
      </div>

      <div className="mt-6 flex items-center justify-between">
        <h2 className="font-semibold">History</h2>
        <div className="flex gap-1" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button
              key={r}
              onClick={() => setRange(r)}
              aria-pressed={range === r}
              className={`rounded-md border px-3 py-1 text-xs font-medium ${
                range === r ? "border-accent bg-accent/15 text-ink" : "border-line text-muted hover:text-ink"
              }`}
            >
              {r}
            </button>
          ))}
        </div>
      </div>

      {data.length === 0 ? (
        <p className="mt-4 rounded-xl border border-dashed border-line p-8 text-center text-sm text-muted">
          No checks in this range yet. Data appears after the first probe cycle.
        </p>
      ) : (
        <div className="mt-3 grid gap-4 lg:grid-cols-2">
          <ChartCard title="Latency (ms)">
            <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={tick(range)} stroke="var(--muted)" fontSize={11} />
              <YAxis stroke="var(--muted)" fontSize={11} />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(t) => fmtDateTime(new Date(Number(t)).toISOString())} formatter={(v) => [fmtMs(v as number), "Latency"]} />
              <ReferenceLine y={detail.latency_warn_ms} stroke="var(--degraded)" strokeDasharray="4 4" label={{ value: "warn", fill: "var(--degraded)", fontSize: 10, position: "insideTopRight" }} />
              <Area type="monotone" dataKey="latency" stroke="var(--accent)" fill="var(--accent)" fillOpacity={0.18} strokeWidth={2} connectNulls={false} dot={false} isAnimationActive={false} />
            </AreaChart>
          </ChartCard>
          <ChartCard title="Packet loss (%)">
            <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={tick(range)} stroke="var(--muted)" fontSize={11} />
              <YAxis domain={[0, 100]} stroke="var(--muted)" fontSize={11} />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(t) => fmtDateTime(new Date(Number(t)).toISOString())} formatter={(v) => [fmtLoss(v as number), "Loss"]} />
              <Bar dataKey="loss" fill="var(--down)" isAnimationActive={false} />
            </BarChart>
          </ChartCard>
        </div>
      )}

      <h2 className="mb-2 mt-8 font-semibold">Incidents</h2>
      {incidents.length === 0 ? (
        <p className="text-sm text-muted">No incidents recorded for this branch.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-2 text-xs uppercase text-muted">
              <tr>
                <th className="px-3 py-2">Severity</th>
                <th className="px-3 py-2">Started</th>
                <th className="px-3 py-2">Duration</th>
                <th className="px-3 py-2">State</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((i) => (
                <tr key={i.id} className="border-t border-line">
                  <td className="px-3 py-2"><StatusPill status={i.severity} /></td>
                  <td className="px-3 py-2">{fmtDateTime(i.started_at)}</td>
                  <td className="px-3 py-2 tabular-nums">{duration(i.started_at, i.ended_at)}</td>
                  <td className="px-3 py-2">{i.ended_at ? "Resolved" : i.acknowledged_by ? `Open · ack by ${i.acknowledged_by}` : "Open"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <div className="text-xl font-bold tabular-nums">{value}</div>
      <div className="text-xs text-muted">{label}</div>
    </div>
  );
}

function ChartCard({ title, children }: { title: string; children: React.ReactElement }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-4" aria-label={title}>
      <h3 className="mb-2 text-sm font-medium text-muted">{title}</h3>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    </section>
  );
}
