import type { Status } from "../types";

const STYLE: Record<Status, { dot: string; text: string; label: string }> = {
  UP: { dot: "bg-up", text: "text-up", label: "Up" },
  DEGRADED: { dot: "bg-degraded", text: "text-degraded", label: "Degraded" },
  DOWN: { dot: "bg-down", text: "text-down", label: "Down" },
  PENDING: { dot: "bg-pending", text: "text-pending", label: "Pending" },
  DISABLED: { dot: "bg-pending/50", text: "text-pending", label: "Disabled" },
};

/** Colour is never the only signal: the label is always shown. */
export function StatusPill({ status }: { status: Status }) {
  const s = STYLE[status];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-semibold ${s.text}`}>
      <span className={`h-2 w-2 rounded-full ${s.dot} ${status === "DOWN" ? "animate-pulse" : ""}`} aria-hidden />
      {s.label}
    </span>
  );
}
