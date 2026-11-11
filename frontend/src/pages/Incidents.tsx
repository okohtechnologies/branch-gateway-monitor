import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { duration, fmtDateTime } from "../format";
import { useLive } from "../live";
import { StatusPill } from "../components/StatusPill";
import type { Incident } from "../types";

export function Incidents() {
  const { transitionTick } = useLive();
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [openOnly, setOpenOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .incidents({ openOnly })
      .then((r) => {
        setIncidents(r.incidents);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [openOnly, transitionTick]);

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold tracking-tight">Incidents</h1>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} />
          Open only
        </label>
      </div>

      {error && <p role="alert" className="text-down">Could not load incidents: {error}</p>}
      {!incidents && !error && <p className="text-muted">Loading…</p>}
      {incidents && incidents.length === 0 && (
        <p className="rounded-xl border border-dashed border-line p-10 text-center text-muted">
          {openOnly ? "No open incidents. Everything is running normally." : "No incidents recorded yet."}
        </p>
      )}
      {incidents && incidents.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-line">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-2 text-xs uppercase text-muted">
              <tr>
                <th className="px-3 py-2">Branch</th>
                <th className="px-3 py-2">Severity</th>
                <th className="px-3 py-2">Started</th>
                <th className="px-3 py-2">Duration</th>
                <th className="px-3 py-2">State</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((i) => (
                <tr key={i.id} className="border-t border-line">
                  <td className="px-3 py-2 font-medium">
                    <Link to={`/branches/${i.branch_id}`} className="hover:underline">{i.branch_name}</Link>
                  </td>
                  <td className="px-3 py-2"><StatusPill status={i.severity} /></td>
                  <td className="px-3 py-2">{fmtDateTime(i.started_at)}</td>
                  <td className="px-3 py-2 tabular-nums">{duration(i.started_at, i.ended_at)}</td>
                  <td className="px-3 py-2">
                    {i.ended_at ? "Resolved" : i.acknowledged_by ? `Open · ack by ${i.acknowledged_by}` : "Open"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
