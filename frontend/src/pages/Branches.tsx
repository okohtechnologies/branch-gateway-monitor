import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Building2, Pencil, Plus, Trash2 } from "lucide-react";
import { api, ApiError } from "../api";
import { fmtMs } from "../format";
import { useLive } from "../live";
import { parseFieldErrors, validateBranchInput } from "../logic";
import { useToast } from "../toast";
import { Field } from "../components/Field";
import { Modal } from "../components/Modal";
import { EmptyState, ErrorState } from "../components/States";
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

const toInput = (b: Branch): BranchInput => ({
  name: b.name,
  host: b.host,
  location: b.location,
  probe_type: b.probe_type,
  port: b.port,
  latency_warn_ms: b.latency_warn_ms,
  loss_warn_pct: b.loss_warn_pct,
  enabled: b.enabled,
});

const address = (b: Branch) => `${b.host}${b.probe_type === "tcp" ? `:${b.port}` : ""}`;

export function Branches() {
  const { transitionTick } = useLive();
  const toast = useToast();
  const [branches, setBranches] = useState<Branch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Branch | "new" | null>(null);
  const [deleting, setDeleting] = useState<Branch | null>(null);
  const [highlight, setHighlight] = useState<number | null>(null);
  // Pre-filled thresholds for a new branch come from Settings (falling back to the built-in 200 ms / 20%).
  const [defaults, setDefaults] = useState({ latency: EMPTY.latency_warn_ms, loss: EMPTY.loss_warn_pct });
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
  useEffect(() => {
    api
      .settings()
      .then((d) => setDefaults({ latency: Number(d.values.default_latency_warn_ms), loss: Number(d.values.default_loss_warn_pct) }))
      .catch(() => undefined); // not critical: the built-in defaults are fine
  }, []);

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

  const setEnabled = async (b: Branch, enabled: boolean) => {
    try {
      await api.updateBranch(b.id, { ...toInput(b), enabled });
      toast.success(enabled ? `Monitoring enabled for ${b.name}.` : `${b.name} disabled. Its history is kept.`);
      load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not update the branch.");
    }
  };

  const remove = async (b: Branch) => {
    try {
      await api.deleteBranch(b.id);
      toast.success(`${b.name} deleted.`);
      setDeleting(null);
      load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not delete the branch.");
    }
  };

  if (error && !branches) {
    return (
      <>
        <h1 className="mb-4 text-xl font-semibold tracking-tight">Branches</h1>
        <ErrorState message={`Could not load branches: ${error}`} onRetry={load} />
      </>
    );
  }
  if (!branches) {
    return (
      <>
        <h1 className="mb-4 text-xl font-semibold tracking-tight">Branches</h1>
        <div className="skeleton h-48" aria-busy="true" />
      </>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight">Branches</h1>
        <button onClick={() => setEditing("new")} className="btn btn-primary">
          <Plus className="size-4" aria-hidden /> Add branch
        </button>
      </div>

      {branches.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No branches yet"
          text="Add a branch's gateway IP and monitoring starts within seconds."
          action={
            <button onClick={() => setEditing("new")} className="btn btn-primary">
              <Plus className="size-4" aria-hidden /> Add your first branch
            </button>
          }
        />
      ) : (
        <>
          {/* Phones: one card per branch. Larger screens: a table. */}
          <ul className="space-y-3 md:hidden">
            {branches.map((b) => (
              <li key={b.id} className={`card p-4 ${highlight === b.id ? "ring-2 ring-accent" : ""}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <Link to={`/branches/${b.id}`} className="-my-3.5 inline-block py-3.5 font-semibold hover:underline">
                      {b.name}
                    </Link>
                    <p className="mono truncate text-xs text-muted">{address(b)}</p>
                  </div>
                  <StatusPill status={b.status} />
                </div>
                <p className="mt-2 text-xs text-muted">
                  {b.probe_type.toUpperCase()} · warns above {fmtMs(b.latency_warn_ms)} / {b.loss_warn_pct}% loss
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button className="btn btn-secondary" onClick={() => setEditing(b)}>
                    <Pencil className="size-4" aria-hidden /> Edit
                  </button>
                  {!b.enabled && (
                    <button className="btn btn-secondary" onClick={() => setEnabled(b, true)}>
                      Enable
                    </button>
                  )}
                  <button className="btn btn-ghost text-down" onClick={() => setDeleting(b)} aria-label={`Delete ${b.name}`}>
                    <Trash2 className="size-4" aria-hidden /> Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>

          <div className="card hidden overflow-x-auto md:block">
            <table className="w-full min-w-[760px] text-sm">
              <caption className="sr-only">Monitored branches</caption>
              <thead className="border-b border-line">
                <tr>
                  <th scope="col" className="th">Name</th>
                  <th scope="col" className="th">Address</th>
                  <th scope="col" className="th">Probe</th>
                  <th scope="col" className="th">Warns above</th>
                  <th scope="col" className="th">Status</th>
                  <th scope="col" className="th text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {branches.map((b) => (
                  <tr key={b.id} className={`border-t border-line transition-colors duration-150 ${highlight === b.id ? "bg-[color-mix(in_srgb,var(--accent)_12%,transparent)]" : ""}`}>
                    <td className="td">
                      <Link to={`/branches/${b.id}`} className="font-semibold hover:underline">
                        {b.name}
                      </Link>
                      {b.location && <div className="text-xs text-muted">{b.location}</div>}
                    </td>
                    <td className="td mono text-xs">{address(b)}</td>
                    <td className="td">{b.probe_type === "tcp" ? "TCP" : "ICMP"}</td>
                    <td className="td num">
                      {fmtMs(b.latency_warn_ms)} · {b.loss_warn_pct}%
                    </td>
                    <td className="td"><StatusPill status={b.status} /></td>
                    <td className="td whitespace-nowrap text-right">
                      {!b.enabled && (
                        <button className="btn btn-ghost" onClick={() => setEnabled(b, true)}>
                          Enable
                        </button>
                      )}
                      <button className="btn btn-ghost" onClick={() => setEditing(b)} aria-label={`Edit ${b.name}`}>
                        <Pencil className="size-4" aria-hidden /> Edit
                      </button>
                      <button className="btn btn-ghost text-down" onClick={() => setDeleting(b)} aria-label={`Delete ${b.name}`}>
                        <Trash2 className="size-4" aria-hidden /> Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {editing && (
        <BranchForm
          branch={editing === "new" ? null : editing}
          defaults={defaults}
          onClose={closeEditor}
          onSaved={(saved, created) => {
            closeEditor();
            toast.success(created ? `${saved.name} added. It shows as Pending until the first checks confirm.` : `${saved.name} saved.`);
            setHighlight(saved.id);
            setTimeout(() => setHighlight(null), 3000);
            load();
          }}
        />
      )}

      {deleting && (
        <Modal title={`Delete ${deleting.name}?`} onClose={() => setDeleting(null)}>
          <p className="text-sm">
            Its check history and incidents will be <strong>permanently removed</strong>. This cannot be undone.
          </p>
          {deleting.enabled && (
            <p className="mt-2 text-sm text-muted">Just want it to stop alerting? Disable it instead and keep the history.</p>
          )}
          <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <button className="btn btn-ghost" onClick={() => setDeleting(null)}>
              Cancel
            </button>
            {deleting.enabled && (
              <button
                className="btn btn-secondary"
                data-autofocus
                onClick={async () => {
                  await setEnabled(deleting, false);
                  setDeleting(null);
                }}
              >
                Disable instead
              </button>
            )}
            <button className="btn btn-danger" onClick={() => remove(deleting)} {...(deleting.enabled ? {} : { "data-autofocus": true })}>
              <Trash2 className="size-4" aria-hidden /> Delete branch
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function BranchForm({ branch, defaults, onClose, onSaved }: { branch: Branch | null; defaults: { latency: number; loss: number }; onClose: () => void; onSaved: (saved: Branch, created: boolean) => void }) {
  const [f, setF] = useState<BranchInput>(branch ? toInput(branch) : { ...EMPTY, latency_warn_ms: defaults.latency, loss_warn_pct: defaults.loss });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const set = <K extends keyof BranchInput>(k: K, v: BranchInput[K]) => {
    setF((p) => ({ ...p, [k]: v }));
    if (errors[k]) setErrors((e) => ({ ...e, [k]: "" }));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const body: BranchInput = { ...f, name: f.name.trim(), host: f.host.trim(), location: f.location.trim(), port: f.probe_type === "tcp" ? f.port : null };
    const problems = validateBranchInput(body);
    setErrors(problems);
    setFormError(null);
    if (Object.keys(problems).length > 0) {
      // Send keyboard and screen-reader users to the first field that needs fixing.
      requestAnimationFrame(() => formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    setBusy(true);
    try {
      const saved = branch ? await api.updateBranch(branch.id, body) : await api.createBranch(body);
      onSaved(saved, !branch);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setErrors({ name: "A branch with this name already exists." });
      else if (err instanceof ApiError && err.status === 422) {
        const fields = parseFieldErrors(err.message);
        const { _: general, ...byField } = fields;
        setErrors(byField);
        if (general || Object.keys(byField).length === 0) setFormError(general ?? err.message);
      } else setFormError(err instanceof ApiError ? err.message : "Could not save the branch. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={branch ? `Edit ${branch.name}` : "Add branch"} onClose={onClose}>
      <form ref={formRef} onSubmit={submit} noValidate className="space-y-4">
        <Field label="Name" error={errors.name}>
          {(p) => <input {...p} id={p.id} required value={f.name} onChange={(e) => set("name", e.target.value)} className="input" placeholder="Head office" maxLength={80} autoComplete="off" />}
        </Field>
        <Field label="Gateway IP or hostname" error={errors.host} hint="The address the monitor will ping, for example 192.168.1.1.">
          {(p) => <input {...p} required value={f.host} onChange={(e) => set("host", e.target.value)} className="input mono" placeholder="192.168.1.1" autoComplete="off" autoCapitalize="off" spellCheck={false} />}
        </Field>
        <Field label="Location" optional>
          {(p) => <input {...p} value={f.location} onChange={(e) => set("location", e.target.value)} className="input" maxLength={200} autoComplete="off" />}
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Probe type" hint={f.probe_type === "icmp" ? "Ping. Use TCP if the gateway ignores ping." : undefined}>
            {(p) => (
              <select {...p} value={f.probe_type} onChange={(e) => set("probe_type", e.target.value as "icmp" | "tcp")} className="input">
                <option value="icmp">ICMP ping</option>
                <option value="tcp">TCP connect</option>
              </select>
            )}
          </Field>
          {f.probe_type === "tcp" && (
            <Field label="Port" error={errors.port}>
              {(p) => (
                <input {...p} type="number" inputMode="numeric" min={1} max={65535} value={f.port ?? ""} onChange={(e) => set("port", e.target.value === "" ? null : Number(e.target.value))} className="input num" placeholder="443" />
              )}
            </Field>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Warn above (ms)" error={errors.latency_warn_ms} hint="Status becomes Degraded when latency stays above this.">
            {(p) => <input {...p} type="number" inputMode="decimal" min={1} step="any" value={f.latency_warn_ms} onChange={(e) => set("latency_warn_ms", Number(e.target.value))} className="input num" />}
          </Field>
          <Field label="Warn above loss (%)" error={errors.loss_warn_pct} hint="Same, for packet loss.">
            {(p) => <input {...p} type="number" inputMode="decimal" min={0} max={100} step="any" value={f.loss_warn_pct} onChange={(e) => set("loss_warn_pct", Number(e.target.value))} className="input num" />}
          </Field>
        </div>

        <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm">
          <input type="checkbox" className="size-5 cursor-pointer accent-[var(--primary)]" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          <span>
            Monitoring on <span className="text-muted">(probed every cycle)</span>
          </span>
        </label>

        {formError && (
          <p role="alert" className="field-error text-sm">
            {formError}
          </p>
        )}

        <div className="flex flex-col-reverse gap-2 pt-1 sm:flex-row sm:justify-end">
          <button type="button" onClick={onClose} className="btn btn-ghost">
            Cancel
          </button>
          <button type="submit" disabled={busy} className="btn btn-primary">
            {busy ? "Saving…" : branch ? "Save changes" : "Add branch"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
