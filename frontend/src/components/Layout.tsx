import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { Activity, LogOut, Moon, ShieldOff, Sun } from "lucide-react";
import { useAuth } from "../auth";
import { useMediaQuery, useStoredChoice } from "../hooks";
import { useLive } from "../live";
import { useTheme } from "../theme";
import { Modal } from "./Modal";
import { Sidebar } from "./Sidebar";
import { Banners, NAV, navActive, useLiveState, useProblemCounts } from "./chrome";

const PIN_CHOICES = ["pinned", "auto"] as const;

const tabLink = (active: boolean) =>
  `flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-xs font-medium transition-colors duration-150 ${active ? "text-accent" : "text-muted"}`;

export function Layout() {
  const { announcement } = useLive();
  const { down, degraded } = useProblemCounts();
  const onDetail = useLocation().pathname.startsWith("/branches/");
  const canHover = useMediaQuery("(hover: hover)");
  const [pinPref, setPinPref] = useStoredChoice("sidebar", PIN_CHOICES);
  // Without a saved choice: hover-capable screens auto-hide, touch-only screens start pinned open.
  const pinned = pinPref ? pinPref === "pinned" : !canHover;

  // Problems show in the browser tab, so they are visible from another window too.
  useEffect(() => {
    document.title = down ? `(${down} down) Branch Monitor` : degraded ? `(${degraded} degraded) Branch Monitor` : "Branch Monitor";
  }, [down, degraded]);

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-[70] focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2.5 focus:text-sm focus:font-semibold focus:text-onprimary"
      >
        Skip to main content
      </a>

      {/* Phones: a compact header with the attention banners, plus a bottom tab bar. */}
      <MobileHeader />

      {/* Desktop: the auto-hiding sidebar. */}
      <Sidebar pinned={pinned} onTogglePin={() => setPinPref(pinned ? "auto" : "pinned")} />

      {/* The page only makes room for the slim rail (or the pinned sidebar): hover never shifts content. */}
      <div className={`pb-[calc(3.5rem+env(safe-area-inset-bottom))] md:pb-0 md:transition-[padding] md:duration-200 ${pinned ? "md:pl-60" : "md:pl-14"}`}>
        <Banners region />

        {/* Announces status changes (not every refresh) to screen readers. */}
        <div aria-live="polite" role="status" className="sr-only-live">
          {announcement?.text}
        </div>

        <main id="main" tabIndex={-1} className="mx-auto max-w-[1440px] px-4 py-5 outline-none md:px-6 md:py-6">
          <Outlet />
        </main>
      </div>

      <nav
        className="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-card pb-[env(safe-area-inset-bottom)] md:hidden"
        aria-label="Main"
      >
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => tabLink(navActive(to, isActive, onDetail))}>
            <Icon className="size-5" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

function MobileHeader() {
  const { user, logout, logoutAll } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const live = useLiveState();
  const [account, setAccount] = useState(false);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-card/95 backdrop-blur md:hidden">
      <div className="flex h-14 items-center gap-2 px-4">
        <Link to="/" className="mr-auto flex min-h-11 items-center gap-2 rounded-lg font-semibold tracking-tight">
          <Activity className="size-5 text-accent" aria-hidden />
          Branch Monitor
        </Link>
        <span className="flex items-center gap-1.5 text-xs font-medium text-muted" role="status" title={live.tip}>
          <span className={`size-2 rounded-full ${live.dot}`} aria-hidden />
          {live.label}
        </span>
        <button onClick={toggleTheme} className="icon-btn" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
          {theme === "dark" ? <Sun className="size-5" aria-hidden /> : <Moon className="size-5" aria-hidden />}
        </button>
        <button onClick={() => setAccount(true)} className="icon-btn" aria-label="Account and sign out">
          <LogOut className="size-5" aria-hidden />
        </button>
      </div>
      <Banners />

      {account && (
        <Modal title="Account" onClose={() => setAccount(false)}>
          <p className="text-sm text-muted">Signed in as</p>
          <p className="mb-5 font-medium break-all">{user?.email}</p>
          <div className="flex flex-col gap-2">
            <button className="btn btn-secondary" data-autofocus onClick={logout}>
              <LogOut className="size-4" aria-hidden /> Sign out
            </button>
            <button className="btn btn-ghost" onClick={logoutAll}>
              <ShieldOff className="size-4" aria-hidden /> Sign out of all devices
            </button>
          </div>
          <p className="mt-3 text-xs text-muted">Signing out of all devices also signs out this browser.</p>
        </Modal>
      )}
    </header>
  );
}
