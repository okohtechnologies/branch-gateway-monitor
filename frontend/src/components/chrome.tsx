/** Pieces shared by the desktop sidebar and the phone header/tab bar. */
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Building2, CircleX, CloudOff, LayoutDashboard, Settings, Siren, TriangleAlert, WifiOff, type LucideIcon } from "lucide-react";
import { useAuth } from "../auth";
import { useFreshness, useLive } from "../live";
import { Modal } from "./Modal";

export const NAV: Array<{ to: string; label: string; icon: LucideIcon; end?: boolean }> = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/branches", label: "Branches", icon: Building2, end: true },
  { to: "/incidents", label: "Incidents", icon: Siren },
  { to: "/settings", label: "Settings", icon: Settings },
];

/** A branch's detail page is reached from the Overview, so the Overview item stays highlighted there. */
export const navActive = (to: string, isActive: boolean, onDetail: boolean) => isActive || (to === "/" && onDetail);

export function useProblemCounts(): { down: number; degraded: number } {
  const { snapshots } = useLive();
  return useMemo(() => {
    let down = 0;
    let degraded = 0;
    for (const s of Object.values(snapshots)) {
      if (s.status === "DOWN") down++;
      else if (s.status === "DEGRADED") degraded++;
    }
    return { down, degraded };
  }, [snapshots]);
}

/**
 * A warning about the live connection, or null while everything is fine. There is deliberately no
 * "Live" badge: a healthy dashboard says nothing, and only trouble is shown.
 */
export function useLiveState(): { label: string; tip: string; pulse: boolean } | null {
  const { stale, connected } = useFreshness();
  if (!connected) return { label: "Offline, reconnecting", tip: "Live updates are disconnected. Retrying.", pulse: true };
  if (stale) return { label: "Stale data", tip: "No recent probe results are arriving.", pulse: false };
  return null;
}

/** An amber warning dot. When `pulse` is set a soft ring radiates from it (off for reduced motion). */
export function WarningDot({ pulse, className = "size-3" }: { pulse: boolean; className?: string }) {
  return (
    <span className={`relative flex shrink-0 ${className}`} aria-hidden>
      {pulse && <span className="absolute inline-flex size-full rounded-full bg-degraded opacity-60 motion-safe:animate-ping" />}
      <span className="relative inline-flex size-full rounded-full bg-degraded" />
    </span>
  );
}

/** Things that need attention, stacked: undelivered alerts, monitor offline, or branches in trouble. */
export function Banners({ region = false }: { region?: boolean }) {
  const { monitorSuspect, alertsPending } = useLive();
  const { down, degraded } = useProblemCounts();
  const problem = down > 0 || degraded > 0;

  if (!(alertsPending > 0 || monitorSuspect || problem)) return null; // never an empty landmark

  const strips = (
    <>
      {alertsPending > 0 && (
        <div role="status" className="banner-degraded border-b">
          <div className="mx-auto flex max-w-[1440px] items-start gap-3 px-4 py-2.5 text-sm md:px-6">
            <CloudOff className="mt-0.5 size-4 shrink-0 text-degraded" aria-hidden />
            <p>
              <strong className="text-degraded">
                {alertsPending} alert{alertsPending === 1 ? "" : "s"} could not be sent to Telegram.
              </strong>{" "}
              {alertsPending === 1 ? "It is" : "They are"} saved and will be re-sent automatically. Check that this machine can reach the internet.
            </p>
          </div>
        </div>
      )}

      {monitorSuspect && (
        <div role="status" className="banner-degraded border-b">
          <div className="mx-auto flex max-w-[1440px] items-start gap-3 px-4 py-2.5 text-sm md:px-6">
            <WifiOff className="mt-0.5 size-4 shrink-0 text-degraded" aria-hidden />
            <p>
              <strong className="text-degraded">Monitor may be offline.</strong> Every branch and the external canary are unreachable at once, so the
              monitor's own network is the likely cause. Per-branch alerts are paused until it recovers.
            </p>
          </div>
        </div>
      )}

      {problem && !monitorSuspect && (
        <div className={`${down ? "banner-down" : "banner-degraded"} border-b`}>
          <Link
            to={`/?status=${down ? "down" : "degraded"}`}
            className="mx-auto flex min-h-11 max-w-[1440px] items-center gap-3 px-4 text-sm md:min-h-10 md:px-6"
          >
            {down ? <CircleX className="size-4 shrink-0 text-down" aria-hidden /> : <TriangleAlert className="size-4 shrink-0 text-degraded" aria-hidden />}
            <span className="min-w-0 flex-1 font-medium">
              {[down ? `${down} down` : null, degraded ? `${degraded} degraded` : null].filter(Boolean).join(" · ")}
            </span>
            <span className="shrink-0 font-semibold text-accent underline underline-offset-2">View</span>
          </Link>
        </div>
      )}
    </>
  );

  // On desktop the strips sit above the page, outside <main>: a landmark lets screen-reader users find
  // them. (On phones they are inside the header, which already is one.)
  return region ? (
    <section aria-label="Alerts" className="sticky top-0 z-30 hidden md:block">
      {strips}
    </section>
  ) : (
    strips
  );
}

/** Asks before signing the account out of every device (including this one). */
export function SignOutEverywhereDialog({ onClose }: { onClose: () => void }) {
  const { logoutAll } = useAuth();
  return (
    <Modal title="Sign out of all devices?" onClose={onClose}>
      <p className="text-sm">
        Every browser signed in to this account, including this one, will be signed out and will need the password again. Use this if you
        think someone else may have access.
      </p>
      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button className="btn btn-ghost" data-autofocus onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary" onClick={logoutAll}>
          Sign out everywhere
        </button>
      </div>
    </Modal>
  );
}
