import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { CircleCheck, CircleDashed, Radar, SearchX, Square } from "lucide-react";
import { api, ApiError } from "../api";
import { filterScan, type ScanFilter } from "../logic";
import { EmptyState, ErrorState } from "../components/States";
import type { ScanHost, ScanView } from "../types";

const POLL_MS = 1000;
const FILTERS: Array<{ value: ScanFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "used", label: "Used" },
  { value: "free", label: "Free" },
];

/** Used / free, with an icon shape as well as colour so it never relies on colour alone. */
function HostPill({ status }: { status: ScanHost["status"] }) {
  const used = status === "used";
  const Icon = used ? CircleCheck : CircleDashed;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${
        used ? "text-up bg-[color-mix(in_srgb,var(--up)_12%,transparent)]" : "text-pending bg-[color-mix(in_srgb,var(--pending)_12%,transparent)]"
      }`}
      title={used ? "Answered a ping or a connection" : "No answer. It may be free, or a device that is off or blocks pings."}
    >
      <Icon className="size-3.5" aria-hidden />
      {used ? "Used" : "Free"}
    </span>
  );
}

export function Scanner() {
  const [target, setTarget] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scan, setScan] = useState<ScanView | null>(null);
  const [rows, setRows] = useState<ScanHost[]>([]);
  const [starting, setStarting] = useState(false);
  const [filter, setFilter] = useState<ScanFilter>("all");
  const [query, setQuery] = useState("");
  const cursor = useRef(0);

  const running = scan?.state === "running";
  const scanId = scan?.id;

  // While a scan runs, fetch only the rows added since the last poll.
  useEffect(() => {
    if (!running || !scanId) return;
    let stopped = false;
    const tick = async () => {
      try {
        const v = await api.scan(scanId, cursor.current);
        if (stopped) return;
        cursor.current = v.next;
        setRows((r) => [...r, ...v.results]);
        setScan(v);
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
  }, [running, scanId]);

  const start = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      setFieldError(null);
      setError(null);
      setStarting(true);
      try {
        const v = await api.startScan(target);
        cursor.current = v.next;
        setRows(v.results);
        setScan(v);
        setFilter("all");
        setQuery("");
      } catch (err) {
        // A bad target belongs next to the input; anything else is a page-level problem.
        if (err instanceof ApiError && (err.status === 422 || err.status === 409)) setFieldError(err.message.replace(/^target: /, ""));
        else setError((err as Error).message);
      } finally {
        setStarting(false);
      }
    },
    [target],
  );

  const cancel = async () => {
    if (!scan) return;
    try {
      await api.cancelScan(scan.id);
      const v = await api.scan(scan.id, cursor.current); // pick up the final state and any last rows
      cursor.current = v.next;
      setRows((r) => [...r, ...v.results]);
      setScan(v);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const shown = useMemo(() => filterScan(rows, filter, query), [rows, filter, query]);
  const pct = scan && scan.total > 0 ? Math.round((scan.done / scan.total) * 100) : 0;
  const lookup = scan?.total === 1;

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold tracking-tight">IP Scanner</h1>
      <p className="mb-4 text-sm text-muted">
        Look up one address, or sweep a range to see which addresses answer. Only private networks can be scanned.
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
            text="Enter an address or subnet above. The scanner pings each address and tries a few common ports."
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
                ? `Scanning ${scan.target}: ${scan.done} of ${scan.total} checked · `
                : `${scan.target} · ${scan.state === "cancelled" ? `cancelled after ${scan.done} of ${scan.total} · ` : ""}`}
              <span className="font-semibold text-ink">{scan.used} used</span>
              {" · "}
              <span className="font-semibold text-ink">{scan.free} free</span>
              {scan.state === "done" ? ` · finished in ${scan.elapsed_s}s` : ""}
              {scan.state === "failed" ? ` · scan failed: ${scan.error ?? "unknown error"}` : ""}
            </p>
            {scan.state === "done" && !lookup && (
              <p className="mt-1 text-xs text-muted">
                “Free” means no answer. A device that is switched off or blocks pings looks the same, so double-check before reusing an address.
              </p>
            )}
          </div>

          <div className="mb-3 flex flex-col gap-3 md:flex-row md:items-end">
            <div role="group" aria-label="Show" className="flex gap-1">
              {FILTERS.map((f) => (
                <button
                  key={f.value}
                  type="button"
                  aria-pressed={filter === f.value}
                  onClick={() => setFilter(f.value)}
                  className={`btn ${filter === f.value ? "btn-primary" : "btn-secondary"}`}
                >
                  {f.label}
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
                    {h.status === "used" && <HostDetails h={h} />}
                  </li>
                ))}
              </ul>

              <div className="card hidden overflow-x-auto md:block">
                <table className="w-full min-w-[860px] text-sm">
                  <caption className="sr-only">Scanned addresses in IP order</caption>
                  <thead className="border-b border-line">
                    <tr>
                      <th scope="col" className="th">Address</th>
                      <th scope="col" className="th">Status</th>
                      <th scope="col" className="th">Latency</th>
                      <th scope="col" className="th">Open ports</th>
                      <th scope="col" className="th">Name</th>
                      <th scope="col" className="th">MAC</th>
                      <th scope="col" className="th">Maker</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((h) => (
                      <tr key={h.ip} className="border-t border-line">
                        <td className="td num font-semibold">{h.ip}</td>
                        <td className="td"><HostPill status={h.status} /></td>
                        <td className="td num">{h.latency_ms !== null ? `${h.latency_ms} ms` : "–"}</td>
                        <td className="td num">{h.ports.length ? h.ports.join(", ") : "–"}</td>
                        <td className="td">{h.hostname ?? "–"}</td>
                        <td className="td num">{h.mac ?? "–"}</td>
                        <td className="td"><Maker h={h} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {scan.state === "done" && scan.used > 0 && rows.every((r) => !r.mac) && (
            <p className="mt-3 text-xs text-muted">
              No MAC addresses were found. They are only visible for devices on the same subnet as the monitor host.
            </p>
          )}
        </>
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

function HostDetails({ h }: { h: ScanHost }) {
  const parts: Array<[string, string]> = [];
  if (h.hostname) parts.push(["Name", h.hostname]);
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
