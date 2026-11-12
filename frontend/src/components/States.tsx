import type { ReactNode } from "react";
import { CircleAlert, RefreshCw, type LucideIcon } from "lucide-react";
import { duration } from "../format";
import { useNow } from "../hooks";

export function EmptyState({
  icon: Icon,
  title,
  text,
  action,
}: {
  icon: LucideIcon;
  title: string;
  text?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-strong px-6 py-12 text-center">
      <Icon className="mb-3 size-8 text-muted" aria-hidden />
      <p className="font-semibold">{title}</p>
      {text && <p className="mt-1 max-w-sm text-sm text-muted">{text}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Errors are announced to screen readers and always offer a way forward. */
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="card flex flex-col items-center px-6 py-10 text-center">
      <CircleAlert className="mb-3 size-8 text-down" aria-hidden />
      <p className="font-semibold">Something went wrong</p>
      <p className="mt-1 max-w-sm text-sm text-muted">{message}</p>
      {onRetry && (
        <button className="btn btn-secondary mt-4" onClick={onRetry}>
          <RefreshCw className="size-4" aria-hidden /> Try again
        </button>
      )}
    </div>
  );
}

export function CardsSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label="Loading branches">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="card space-y-3 p-4">
          <div className="flex justify-between">
            <div className="skeleton h-5 w-32" />
            <div className="skeleton h-5 w-16" />
          </div>
          <div className="skeleton h-4 w-24" />
          <div className="grid grid-cols-3 gap-3">
            <div className="skeleton h-9" />
            <div className="skeleton h-9" />
            <div className="skeleton h-9" />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Live-ticking "14m" that keeps counting between data refreshes. */
export function Elapsed({ since, until }: { since: string; until?: string | null }) {
  const now = useNow(until ? 3_600_000 : 15_000);
  return <>{duration(since, until, now)}</>;
}
