import { useEffect, useMemo } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { Activity, Building2, CircleX, LayoutDashboard, LogOut, Moon, Siren, Sun, TriangleAlert, WifiOff, type LucideIcon } from "lucide-react";
import { useAuth } from "../auth";
import { useFreshness, useLive } from "../live";
import { useTheme } from "../theme";

const NAV: Array<{ to: string; label: string; icon: LucideIcon; end?: boolean }> = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/branches", label: "Branches", icon: Building2, end: true },
  { to: "/incidents", label: "Incidents", icon: Siren },
];

const topLink = ({ isActive }: { isActive: boolean }) =>
  `inline-flex min-h-10 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors duration-150 ${
    isActive ? "bg-card2 text-ink" : "text-muted hover:text-ink"
  }`;

const tabLink = ({ isActive }: { isActive: boolean }) =>
  `flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-xs font-medium transition-colors duration-150 ${
    isActive ? "text-accent" : "text-muted"
  }`;

function LiveIndicator() {
  const { stale, connected } = useFreshness();
  const state = !connected
    ? { label: "Reconnecting", dot: "bg-degraded motion-safe:animate-pulse", tip: "Live updates disconnected, retrying" }
    : stale
      ? { label: "Stale", dot: "bg-degraded", tip: "No recent probe results" }
      : { label: "Live", dot: "bg-up", tip: "Receiving live updates" };
  return (
    <span className="flex items-center gap-1.5 text-xs font-medium text-muted" role="status" title={state.tip}>
      <span className={`size-2 rounded-full ${state.dot}`} aria-hidden />
      {state.label}
    </span>
  );
}

export function Layout() {
  const { user, logout } = useAuth();
  const { snapshots, monitorSuspect, announcement } = useLive();
  const [theme, toggleTheme] = useTheme();
  // /branches/:id is reached from the Overview, so that tab stays highlighted there.
  const onDetail = useLocation().pathname.startsWith("/branches/");

  const { down, degraded } = useMemo(() => {
    let d = 0;
    let g = 0;
    for (const s of Object.values(snapshots)) {
      if (s.status === "DOWN") d++;
      else if (s.status === "DEGRADED") g++;
    }
    return { down: d, degraded: g };
  }, [snapshots]);

  // Problems show in the browser tab, so they are visible from another window too.
  useEffect(() => {
    document.title = down ? `(${down} down) Branch Monitor` : degraded ? `(${degraded} degraded) Branch Monitor` : "Branch Monitor";
  }, [down, degraded]);

  const problem = down > 0 || degraded > 0;

  return (
    <div className="min-h-screen pb-[calc(3.5rem+env(safe-area-inset-bottom))] md:pb-0">
      <header className="sticky top-0 z-40 border-b border-line bg-card/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-2 px-4">
          <Link to="/" className="mr-2 flex min-h-11 items-center gap-2 rounded-lg font-semibold tracking-tight">
            <Activity className="size-5 text-accent" aria-hidden />
            Branch Monitor
          </Link>
          <nav className="hidden flex-1 gap-1 md:flex" aria-label="Main">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={({ isActive }) => topLink({ isActive: isActive || (to === "/" && onDetail) })}>
                <Icon className="size-4" aria-hidden />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="flex-1 md:hidden" />
          <LiveIndicator />
          <button
            onClick={toggleTheme}
            className="icon-btn"
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? <Sun className="size-5" aria-hidden /> : <Moon className="size-5" aria-hidden />}
          </button>
          <button onClick={logout} className="icon-btn" aria-label={`Sign out ${user?.email ?? ""}`} title={`Sign out (${user?.email ?? ""})`}>
            <LogOut className="size-5" aria-hidden />
          </button>
        </div>

        {monitorSuspect && (
          <div role="status" className="banner-degraded border-y">
            <div className="mx-auto flex max-w-[1440px] items-start gap-3 px-4 py-2.5 text-sm">
              <WifiOff className="mt-0.5 size-4 shrink-0 text-degraded" aria-hidden />
              <p>
                <strong className="text-degraded">Monitor may be offline.</strong> Every branch and the external canary are unreachable at
                once, so the monitor's own network is the likely cause. Per-branch alerts are paused until it recovers.
              </p>
            </div>
          </div>
        )}

        {problem && !monitorSuspect && (
          <div className={`${down ? "banner-down" : "banner-degraded"} border-y`}>
            <Link
              to={`/?status=${down ? "down" : "degraded"}`}
              className="mx-auto flex min-h-11 max-w-[1440px] items-center gap-3 px-4 text-sm md:min-h-10"
            >
              {down ? <CircleX className="size-4 shrink-0 text-down" aria-hidden /> : <TriangleAlert className="size-4 shrink-0 text-degraded" aria-hidden />}
              <span className="min-w-0 flex-1 font-medium">
                {[down ? `${down} down` : null, degraded ? `${degraded} degraded` : null].filter(Boolean).join(" · ")}
              </span>
              <span className="shrink-0 font-semibold text-accent underline underline-offset-2">View</span>
            </Link>
          </div>
        )}
      </header>

      {/* Announces status changes (not every refresh) to screen readers. */}
      <div aria-live="polite" role="status" className="sr-only-live">
        {announcement?.text}
      </div>

      <main className="mx-auto max-w-[1440px] px-4 py-5 md:py-6">
        <Outlet />
      </main>

      <nav
        className="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-card pb-[env(safe-area-inset-bottom)] md:hidden"
        aria-label="Main"
      >
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => tabLink({ isActive: isActive || (to === "/" && onDetail) })}>
            <Icon className="size-5" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
