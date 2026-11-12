import { sparkGeometry } from "../logic";
import type { SparkPoint } from "../types";

const W = 96;
const H = 28;

/**
 * Tiny latency trend for the last hour. Decorative (the exact figures sit next to it), so it is
 * hidden from screen readers. Gaps mean "no reply"; red ticks mark DOWN buckets. The height is
 * relative to the branch's own warning threshold (see sparkGeometry), so cards are comparable.
 */
export function Sparkline({ points, warnMs = null }: { points?: SparkPoint[]; warnMs?: number | null }) {
  const g = sparkGeometry(points, warnMs, W, H);

  if (g === null) {
    return (
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden className="shrink-0">
        <line x1="0" y1={H - 3} x2={W} y2={H - 3} className="stroke-line" strokeWidth="1.5" strokeDasharray="3 3" />
      </svg>
    );
  }

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden className="shrink-0 overflow-visible">
      {g.thresholdY !== null && (
        <line x1="0" y1={g.thresholdY} x2={W} y2={g.thresholdY} className="stroke-degraded" strokeOpacity="0.55" strokeWidth="1" strokeDasharray="3 3" />
      )}
      {g.marks.map((m, i) => (
        <rect key={i} x={m.x} y={H - 6} width={m.w} height={6} rx={1} className="fill-down" />
      ))}
      <path d={g.path} fill="none" className="stroke-accent" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
