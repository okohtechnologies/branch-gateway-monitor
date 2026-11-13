import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { CircleCheck, CircleDashed, CircleMinus, Pencil, Radar, SearchX, Square } from "lucide-react";
import { api, ApiError } from "../api";
import { timeAgo } from "../format";
import { useNow } from "../hooks";
import { countScan, filterScan, hostName, withLabel, withoutMemory, type ScanFilter } from "../logic";
import { useToast } from "../toast";
import { Field } from "../components/Field";
import { Modal } from "../components/Modal";
import { EmptyState, ErrorState } from "../components/States";
import type { HostState, ScanHost, ScanView } from "../types";

const POLL_MS = 1000;

// Each state has its own icon shape as well as a colour, so it never relies on colour alone.
const STATE: Record<HostState, { label: string; icon: typeof CircleCheck; text: string; bg: string; tip: string }> = {
  online: {
    label: "Online",
    icon: CircleCheck,
    text: "text-up",
    bg: "bg-[color-mix(in_srgb,var(--up)_12%,transparent)]",
    tip: "Answered just now.",
  },
  offline: {
    label: "Offline",
    icon: CircleMinus,
    text: "text-pending",
    bg: "bg-[color-mix(in_srgb,var(--pending)_14%,transparent)]",
    tip: "Did not answer, but this address is known: a device answered here before, or someone marked it as assigned. Treat it as taken.",
  },
  free: {
    label: "Free",
    icon: CircleDashed,
    text: "text-accent",
    bg: "bg-[color-mix(in_srgb,var(--accent)_12%,transparent)]",
    tip: "No answer, and no device has ever been seen here. Likely free, but a device that has always been off would look the same.",
  },
};

function HostPill({ status }: { status: HostState }) {
  const s = STATE[status];
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${s.text} ${s.bg}`} title={s.tip}>
      <Icon className="size-3.5" aria-hidden />
      {s.label}
    </span>
  );
}

export function Scanner() {
  const now = useNow(60_000);
  const [target, setTarget] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scan, setScan] = useState<ScanView | null>(null);
  const [rows, setRows] = useState<ScanHost[]>([]);
  const [starting, setStarting] = useState(false);
  const [filter, setFilter] = useState<ScanFilter>("all");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<ScanHost | null>(null);
  const cursor = useRef({ next: 0, version: 0 });

  const running = scan?.state === "running";
  const scanId = scan?.id;

  /** Apply a poll result: usually new rows to add, but if the server revised earlier rows, replace them all. */
  const absorb = useCallback((v: ScanView) => {
    cursor.current = { next: v.next, version: v.version };
    setRows((r) => (v.reset ? v.results : [...r, ...v.results]));
    setScan(v);
  }, []);

  useEffect(() => {
    if (!running || !scanId) return;
    let stopped = false;
    const tick = async () => {
      try {
        const v = await api.scan(scanId, cursor.current.next, cursor.current.version);
        if (stopped) return;
        absorb(v);
        setError(null);
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      }
    };
    const t = setInterval(tick, POLL_MS);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [running, scanId, absorb]);

  const start = async (e: FormEvent) => {
    e.preventDefault();
    setFieldError(null);
    setError(null);
    setStarting(true);
    try {
      const v = await api.startScan(target);
      cursor.current = { next: 0, version: 0 };
      setRows([]);
      absorb(v);
      setFilter("all");
      setQuery("");
    } catch (err) {
      // A bad target belongs next to the input; anything else is a page-level problem.
      if (err instanceof ApiError && (err.status === 422 || err.status === 409)) setFieldError(err.message.replace(/^target: /, ""));
      else setError((err as Error).message);
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!scan) return;
    try {
      await api.cancelScan(scan.id);
      absorb(await api.scan(scan.id, cursor.current.next, cursor.current.version)); // the final state and any last rows
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const counts = useMemo(() => countScan(rows), [rows]);
  const shown = useMemo(() => filterScan(rows, filter, query), [rows, filter, query]);
  const pct = scan && scan.total > 0 ? Math.round((scan.done / scan.total) * 100) : 0;
  const lookup = scan?.total === 1;
  const filters: Array<{ value: ScanFilter; label: string; n: number }> = [
    { value: "all", label: "All", n: rows.length },
    { value: "online", label: "Online", n: counts.online },
    { value: "offline", label: "Offline", n: counts.offline },
    { value: "free", label: "Free", n: counts.free },
  ];

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold tracking-tight">IP Scanner</h1>
      <p className="mb-4 text-sm text-muted">
        Look up one address, or sweep a range to see what is online, what is assigned but offline, and what is actually free. Devices are remembered between scans.
        Only private networks can be scanned.
      </p>

      <form onSubmit={start} className="mb-4 flex flex-col gap-3 md:flex-row md:items-start" noValidate>
        <div className="md:w-96">
          <label htmlFor="scan-target" className="label">IP address, range or subnet</label>
          <input
            id="scan-target"
            className="input"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="192.168.1.0/24"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={fieldError ? true : undefined}
            aria-describedby={fieldError ? "scan-target-err" : "scan-target-hint"}
          />
          {fieldError ? (
            <p id="scan-target-err" role="alert" className="mt-1 text-sm text-down">{fieldError}</p>
          ) : (
            <p id="scan-target-hint" className="mt-1 text-xs text-muted">
              Examples: 192.168.1.20 · 192.168.1.10-50 · 10.0.0.0/24 (up to 1,022 addresses)
            </p>
          )}
        </div>
        <div className="flex gap-2 md:mt-[1.625rem]">
          <button type="submit" className="btn btn-primary" disabled={starting || running || target.trim() === ""}>
            <Radar className="size-4" aria-hidden /> {running ? "Scanning…" : "Scan"}
          </button>
          {running && (
            <button type="button" className="btn btn-secondary" onClick={cancel}>
              <Square className="size-4" aria-hidden /> Cancel
            </button>
          )}
        </div>
      </form>

      {error && scan === null && <ErrorState message={error} />}
      {error && scan !== null && <p role="alert" className="mb-3 text-sm text-down">{error}</p>}

      {scan === null ? (
        !error && (
          <EmptyState
            icon={Radar}
            title="No scan yet"
            text="Enter an address or subnet above. The scanner pings each address, tries a few common ports and asks the network who is there."
          />
        )
      ) : (
        <>
          <div className="mb-3" role="status" aria-live="polite">
            {running && (
              <div className="mb-2 h-2 overflow-hidden rounded-full bg-card2" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Scan progress">
                <div className="h-full bg-accent transition-[width] duration-300" style={{ width: `${pct}%` }} />
              </div>
            )}
            <p className="text-sm text-muted">
              {running
                ? scan.phase === "naming"
                  ? `Scanning ${scan.target}: all ${scan.total} checked, looking up device names (this can take a few seconds) · `
                  : `Scanning ${scan.target}: ${scan.done} of ${scan.total} checked · `
                : `${scan.target} · ${scan.state === "cancelled" ? `cancelled after ${scan.done} of ${scan.total} · ` : ""}`}
              <span className="font-semibold text-ink">{counts.online} online</span>
              {" · "}
              <span className="font-semibold text-ink">{counts.offline} offline</span>
              {" · "}
              <span className="font-semibold text-ink">{counts.free} free</span>
              {scan.state === "done" ? ` · finished in ${scan.elapsed_s}s` : ""}
              {scan.state === "failed" ? ` · scan failed: ${scan.error ?? "unknown error"}` : ""}
            </p>
            {scan.state === "done" && !lookup && (
              <p className="mt-1 text-xs text-muted">
                <strong className="font-semibold text-ink">Offline</strong> means a device answered here before (or you marked the address as assigned), so treat it as taken.{" "}
                <strong className="font-semibold text-ink">Free</strong> means no device has ever been seen here. A device that has always been off looks the same, so use
                “Mark as assigned” for any you know about.
              </p>
            )}
          </div>

          <div className="mb-3 flex flex-col gap-3 md:flex-row md:items-end">
            <div role="group" aria-label="Show" className="flex flex-wrap gap-1">
              {filters.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  aria-pressed={filter === f.value}
                  onClick={() => setFilter(f.value)}
                  className={`btn ${filter === f.value ? "btn-primary" : "btn-secondary"}`}
                >
                  {f.label} <span className="num opacity-80">{f.n}</span>
                </button>
              ))}
            </div>
            <div className="md:w-64">
              <label htmlFor="scan-search" className="label">Search results</label>
              <input id="scan-search" className="input" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="IP, name, MAC or maker" autoComplete="off" />
            </div>
          </div>

          {shown.length === 0 ? (
            rows.length === 0 && running ? (
              <p className="text-sm text-muted">Waiting for the first results…</p>
            ) : (
              <EmptyState
                icon={SearchX}
                title={rows.length === 0 ? "No addresses were checked" : "Nothing matches"}
                action={
                  rows.length > 0 ? (
                    <button className="btn btn-secondary" onClick={() => { setFilter("all"); setQuery(""); }}>Show everything</button>
                  ) : undefined
                }
              />
            )
          ) : (
            <>
              <ul className="space-y-3 md:hidden">
                {shown.map((h) => (
                  <li key={h.ip} className="card p-4">
                    <div className="flex items-start justify-between gap-3">
                      <span className="num font-semibold">{h.ip}</span>
                      <HostPill status={h.status} />
                    </div>
                    <HostDetails h={h} now={now} />
                    <button type="button" className="btn btn-ghost -mx-3 mt-2" onClick={() => setEditing(h)}>
                      <Pencil className="size-4" aria-hidden /> {h.known ? "Edit name" : h.status === "free" ? "Mark as assigned" : "Name this device"}
                    </button>
                  </li>
                ))}
              </ul>

              <div className="card hidden overflow-x-auto md:block">
                <table className="w-full min-w-[960px] text-sm">
                  <caption className="sr-only">Scanned addresses in IP order</caption>
                  <thead className="border-b border-line">
                    <tr>
                      <th scope="col" className="th">Address</th>
                      <th scope="col" className="th">Status</th>
                      <th scope="col" className="th">Name</th>
                      <th scope="col" className="th">Latency</th>
                      <th scope="col" className="th">Open ports</th>
                      <th scope="col" className="th">MAC</th>
                      <th scope="col" className="th">Maker</th>
                      <th scope="col" className="th"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((h) => (
                      <tr key={h.ip} className="border-t border-line">
                        <td className="td num font-semibold">{h.ip}</td>
                        <td className="td">
                          <HostPill status={h.status} />
                          {h.via === "arp" && <span className="mt-1 block text-xs text-muted" title="It answered the network's ARP request but ignored pings and connections, usually because of a firewall.">ignores ping</span>}
                        </td>
                        <td className="td"><NameCell h={h} now={now} /></td>
                        <td className="td num">{h.latency_ms !== null ? `${h.latency_ms} ms` : "–"}</td>
                        <td className="td num">{h.ports.length ? h.ports.join(", ") : "–"}</td>
                        <td className="td num">{h.mac ?? "–"}</td>
                        <td className="td"><Maker h={h} /></td>
                        <td className="td text-right">
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={() => setEditing(h)}
                            aria-label={`${h.known ? "Edit name of" : h.status === "free" ? "Mark as assigned:" : "Name"} ${h.ip}`}
                            title={h.known ? "Edit name" : h.status === "free" ? "Mark as assigned" : "Name this device"}
                          >
                            <Pencil className="size-4" aria-hidden />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {scan.state === "done" && counts.online > 0 && rows.every((r) => r.status !== "online" || !r.mac) && (
            <p className="mt-3 text-xs text-muted">
              No MAC addresses were found. They are only visible for devices on the same subnet as the monitor host, which also limits how well offline devices can be recognised.
            </p>
          )}
        </>
      )}

      {editing && (
        <NameDialog
          host={editing}
          onClose={() => setEditing(null)}
          onNamed={(ip, label) => setRows((r) => withLabel(r, ip, label))}
          onForgot={(ip) => setRows((r) => withoutMemory(r, ip))}
        />
      )}
    </div>
  );
}

const PRIVATE_LABEL = "Private address (likely a phone or laptop)";

/** The manufacturer, or why there is none: randomized addresses deliberately hide it. */
function Maker({ h }: { h: ScanHost }) {
  if (h.vendor) return <>{h.vendor}</>;
  if (h.mac_private) return <span className="text-muted" title="This device uses a randomized MAC address, which hides its manufacturer. Phones and many laptops do this on Wi-Fi.">{PRIVATE_LABEL}</span>;
  return <>–</>;
}

/** What to call a device, and for offline ones when it was last around. */
function NameCell({ h, now }: { h: ScanHost; now: number }) {
  const name = hostName(h);
  return (
    <>
      {name ? <span className="font-medium">{name}</span> : <span className="text-muted">–</span>}
      {h.label && h.hostname && h.hostname !== h.label && <span className="block text-xs text-muted">{h.hostname}</span>}
      {h.status === "offline" && (
        <span className="block text-xs text-muted">{h.last_seen ? `last seen ${timeAgo(h.last_seen, now)}` : "marked as assigned"}</span>
      )}
    </>
  );
}

function HostDetails({ h, now }: { h: ScanHost; now: number }) {
  const parts: Array<[string, string]> = [];
  const name = hostName(h);
  if (name) parts.push(["Name", name]);
  if (h.status === "offline") parts.push(["Last seen", h.last_seen ? timeAgo(h.last_seen, now) : "never (marked as assigned)"]);
  if (h.via === "arp") parts.push(["Note", "Ignores ping (answers ARP only)"]);
  if (h.latency_ms !== null) parts.push(["Latency", `${h.latency_ms} ms`]);
  if (h.ports.length) parts.push(["Ports", h.ports.join(", ")]);
  if (h.mac) parts.push(["MAC", h.mac]);
  if (h.vendor) parts.push(["Maker", h.vendor]);
  else if (h.mac_private) parts.push(["Maker", PRIVATE_LABEL]);
  if (parts.length === 0) return null;
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
      {parts.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="num min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function NameDialog({
  host,
  onClose,
  onNamed,
  onForgot,
}: {
  host: ScanHost;
  onClose: () => void;
  onNamed: (ip: string, label: string) => void;
  onForgot: (ip: string) => void;
}) {
  const toast = useToast();
  const [label, setLabel] = useState(host.label ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const assigning = host.status === "free";

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const name = label.trim();
    if (!name) return setError("Enter a name.");
    if (name.length > 80) return setError("Use 80 characters or fewer.");
    setBusy(true);
    try {
      await api.nameHost(host.ip, name);
      onNamed(host.ip, name);
      toast.success(assigning ? `${host.ip} is marked as assigned.` : "Name saved.");
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message.replace(/^label: /, "") : "Could not save. Try again.");
      setBusy(false);
    }
  };

  const forget = async () => {
    setBusy(true);
    try {
      await api.forgetHost(host.ip);
      onForgot(host.ip);
      toast.success(host.status === "online" ? "Name and history removed." : `${host.ip} is free again.`);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not remove it. Try again.");
      setBusy(false);
    }
  };

  return (
    <Modal title={assigning ? `Mark ${host.ip} as assigned` : `Name ${host.ip}`} onClose={onClose}>
      <form onSubmit={save} noValidate className="space-y-4">
        <Field
          label="Name"
          error={error ?? undefined}
          hint={
            host.status === "online"
              ? "Shown in the Name column. Saved for future scans."
              : "Use this for a device that is switched off or hides from the scanner, such as a printer or a server with a fixed address. The address will show as offline, not free."
          }
        >
          {(p) => (
            <input
              {...p}
              data-autofocus
              className="input"
              value={label}
              maxLength={80}
              placeholder="e.g. Reception printer"
              autoComplete="off"
              onChange={(e) => {
                setLabel(e.target.value);
                setError(null);
              }}
            />
          )}
        </Field>
        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          {host.known && (
            <button type="button" className="btn btn-ghost ml-auto text-down" onClick={forget} disabled={busy}>
              {host.status === "online" ? "Remove name and history" : "Forget this address"}
            </button>
          )}
        </div>
      </form>
    </Modal>
  );
}
