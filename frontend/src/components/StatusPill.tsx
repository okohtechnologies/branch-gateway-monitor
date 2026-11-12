import { CircleCheck, CirclePause, CircleX, Clock, TriangleAlert, type LucideIcon } from "lucide-react";
import type { Status } from "../types";

// Each status has its own icon SHAPE (check, triangle, cross, clock, pause) as well as a colour
// and a text label, so status never depends on colour alone.
const STYLE: Record<Status, { icon: LucideIcon; text: string; bg: string; label: string }> = {
  UP: { icon: CircleCheck, text: "text-up", bg: "bg-[color-mix(in_srgb,var(--up)_12%,transparent)]", label: "Up" },
  DEGRADED: { icon: TriangleAlert, text: "text-degraded", bg: "bg-[color-mix(in_srgb,var(--degraded)_12%,transparent)]", label: "Degraded" },
  DOWN: { icon: CircleX, text: "text-down", bg: "bg-[color-mix(in_srgb,var(--down)_12%,transparent)]", label: "Down" },
  PENDING: { icon: Clock, text: "text-pending", bg: "bg-[color-mix(in_srgb,var(--pending)_12%,transparent)]", label: "Pending" },
  DISABLED: { icon: CirclePause, text: "text-pending", bg: "bg-[color-mix(in_srgb,var(--pending)_12%,transparent)]", label: "Disabled" },
};

export const STATUS_LABEL: Record<Status, string> = {
  UP: "Up",
  DEGRADED: "Degraded",
  DOWN: "Down",
  PENDING: "Pending",
  DISABLED: "Disabled",
};

export function StatusIcon({ status, className = "size-4" }: { status: Status; className?: string }) {
  const { icon: Icon, text } = STYLE[status];
  return <Icon className={`${className} ${text} ${status === "DOWN" ? "motion-safe:animate-pulse" : ""}`} aria-hidden />;
}

export function StatusPill({ status }: { status: Status }) {
  const s = STYLE[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ${s.text} ${s.bg}`}>
      <StatusIcon status={status} className="size-3.5" />
      {s.label}
    </span>
  );
}
