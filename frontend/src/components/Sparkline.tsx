import type { SparkPoint } from "../types";

const W = 96;
const H = 28;

/**
 * Tiny latency trend for the last hour. Decorative (the exact figures are shown next to it),
 * so it is hidden from screen readers. Gaps mean "no reply"; red ticks mark DOWN buckets.
 * The scale starts at zero so a flat line really is flat.
 */
export function Sparkline({ points }: { points?: SparkPoint[] }) {
  const pts = points ?? [];
  const nums = pts.map((p) => p.v).filter((v): v is number => v !== null);

  if (pts.length < 3 || (nums.length === 0 && !pts.some((p) => p.d))) {
    return (
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden className="shrink-0">
        <line x1="0" y1={H - 3} x2={W} y2={H - 3} className="stroke-line" strokeWidth="1.5" strokeDasharray="3 3" />
      </svg>
    );
  }

  const max = Math.max(...nums, 1);
  const step = W / (pts.length - 1);
  let d = "";
  let pen = false;
  pts.forEach((p, i) => {
    if (p.v === null || p.d) {
      pen = false; // break the line where there was no reply
      return;
    }
    const x = (i * step).toFixed(1);
    const y = (H - 4 - (p.v / max) * (H - 8)).toFixed(1);
    d += `${pen ? "L" : "M"}${x} ${y} `;
    pen = true;
  });

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-hidden className="shrink-0 overflow-visible">
      {pts.map((p, i) => {
        if (!p.d) return null;
        const w = Math.min(Math.max(step, 2), 6);
        const x = Math.min(Math.max(0, i * step - w / 2), W - w); // never leave the sparkline box
        return <rect key={i} x={x} y={H - 6} width={w} height={6} rx={1} className="fill-down" />;
      })}
      <path d={d} fill="none" className="stroke-accent" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
