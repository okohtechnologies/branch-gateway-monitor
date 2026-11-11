import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { fmtMs } from "../format";
import { useLive } from "../live";
import { Modal } from "../components/Modal";
import { StatusPill } from "../components/StatusPill";
import type { Branch, BranchInput } from "../types";

const EMPTY: BranchInput = {
  name: "",
  host: "",
  location: "",
  probe_type: "icmp",
  port: null,
  latency_warn_ms: 200,
  loss_warn_pct: 20,
  enabled: true,
};

export function Branches() {
  const { transitionTick } = useLive();
  const [branches, setBranches] = useState<Branch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Branch | "new" | null>(null);
  const [deleting, setDeleting] = useState<Branch | null>(null);
  const [params, setParams] = useSearchParams();

  const load = useCallback(() => {
    api
      .branches()
      .then((r) => {
        setBranches(r.branches);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load, transitionTick]);

  // Deep links from other pages: /branches?new=1 and /branches?edit=<id>.
  useEffect(() => {
    if (!branches) return;
    if (params.get("new")) setEditing("new");
    const editId = Number(params.get("edit"));
    if (editId) {
      const b = branches.find((x) => x.id === editId);
      if (b) setEditing(b);
    }
    if (params.get("new") || params.get("edit")) setParams({}, { replace: true });
  }, [branches, params, setParams]);

  const closeEditor = useCallback(() => setEditing(null), []);

  if (error && !branches) return <p role="alert" className="text-down">Could not load branches: {error}</p>;
  if (!branches) return <p className="text-muted">Loading…</p>;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold tracking-tight">Branches</h1>
        <button onClick={() => setEditing("new")} className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg">
          Add branch
        </button>
      </div>

      {branches.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line p-10 text-center text-muted">
          No branches yet. Add one to start monitoring.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-2 text-xs uppercase text-muted">
              <tr>
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2">Address</th>
                <th className="px-3 py-2">Probe</th>
                <th className="px-3 py-2">Warn at</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {branches.map((b) => (
                <tr key={b.id} className="border-t border-line">
                  <td className="px-3 py-2 font-medium">
                    <Link to={`/branches/${b.id}`} className="hover:underline">{b.name}</Link>
                    {b.location && <div className="text-xs font-normal text-muted">{b.location}</div>}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{b.host}{b.probe_type === "tcp" ? `:${b.port}` : ""}</td>
                  <td className="px-3 py-2 uppercase">{b.probe_type}</td>
                  <td className="px-3 py-2 tabular-nums">{fmtMs(b.latency_warn_ms)} / {b.loss_warn_pct}%</td>
                  <td className="px-3 py-2"><StatusPill status={b.status} /></td>
                  <td className="whitespace-nowrap px-3 py-2 text-right">
                    <button onClick={() => setEditing(b)} className="rounded px-2 py-1 text-accent hover:bg-surface-2">Edit</button>
                    <button onClick={() => setDeleting(b)} className="rounded px-2 py-1 text-down hover:bg-surface-2">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <BranchForm
          branch={editing === "new" ? null : editing}
          onClose={closeEditor}
          onSaved={() => {
            closeEditor();
            load();
          }}
        />
      )}
      {deleting && (
        <Modal title="Delete branch" onClose={() => setDeleting(null)}>
          <p className="text-sm">
            Delete <strong>{deleting.name}</strong>? Its check history and incidents will be permanently removed.
            To keep the history, disable the branch instead.
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setDeleting(null)} className="rounded-md border border-line px-4 py-2 text-sm">Cancel</button>
            <button
              onClick={async () => {
                await api.deleteBranch(deleting.id).catch(() => undefined);
                setDeleting(null);
                load();
              }}
              className="rounded-md bg-down px-4 py-2 text-sm font-semibold text-bg"
            >
              Delete branch
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function BranchForm({ branch, onClose, onSaved }: { branch: Branch | null; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<BranchInput>(
    branch
      ? {
          name: branch.name,
          host: branch.host,
          location: branch.location,
          probe_type: branch.probe_type,
          port: branch.port,
          latency_warn_ms: branch.latency_warn_ms,
          loss_warn_pct: branch.loss_warn_pct,
          enabled: branch.enabled,
        }
      : EMPTY,
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof BranchInput>(k: K, v: BranchInput[K]) => setF((p) => ({ ...p, [k]: v }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { ...f, name: f.name.trim(), host: f.host.trim(), port: f.probe_type === "tcp" ? f.port : null };
      if (branch) await api.updateBranch(branch.id, body);
      else await api.createBranch(body);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the branch.");
    } finally {
      setBusy(false);
    }
  };

  const input = "mt-1 w-full rounded-md border border-line bg-bg px-3 py-2 text-sm placeholder:text-muted focus:border-accent";

  return (
    <Modal title={branch ? `Edit ${branch.name}` : "Add branch"} onClose={onClose}>
      <form onSubmit={submit} className="space-y-3">
        <label className="block text-sm font-medium">
          Name
          <input required value={f.name} onChange={(e) => set("name", e.target.value)} className={input} placeholder="Head office" maxLength={80} />
        </label>
        <label className="block text-sm font-medium">
          Gateway IP or hostname
          <input required value={f.host} onChange={(e) => set("host", e.target.value)} className={`${input} font-mono`} placeholder="192.168.1.1" />
        </label>
        <label className="block text-sm font-medium">
          Location <span className="font-normal text-muted">(optional)</span>
          <input value={f.location} onChange={(e) => set("location", e.target.value)} className={input} maxLength={200} />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm font-medium">
            Probe type
            <select value={f.probe_type} onChange={(e) => set("probe_type", e.target.value as "icmp" | "tcp")} className={input}>
              <option value="icmp">ICMP ping</option>
              <option value="tcp">TCP connect</option>
            </select>
          </label>
          {f.probe_type === "tcp" && (
            <label className="block text-sm font-medium">
              Port
              <input
                required
                type="number"
                min={1}
                max={65535}
                value={f.port ?? ""}
                onChange={(e) => set("port", e.target.value === "" ? null : Number(e.target.value))}
                className={input}
                placeholder="443"
              />
            </label>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm font-medium">
            Warn above (ms)
            <input type="number" min={1} step="any" required value={f.latency_warn_ms} onChange={(e) => set("latency_warn_ms", Number(e.target.value))} className={input} />
          </label>
          <label className="block text-sm font-medium">
            Warn above loss (%)
            <input type="number" min={0} max={100} step="any" required value={f.loss_warn_pct} onChange={(e) => set("loss_warn_pct", Number(e.target.value))} className={input} />
          </label>
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          Enabled (probed every cycle)
        </label>

        {error && <p role="alert" className="text-sm text-down">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-md border border-line px-4 py-2 text-sm">Cancel</button>
          <button type="submit" disabled={busy} className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-60">
            {busy ? "Saving…" : branch ? "Save changes" : "Add branch"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
