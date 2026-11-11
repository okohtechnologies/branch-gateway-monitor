import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { useLive } from "../live";
import { useTheme } from "../theme";

const link = ({ isActive }: { isActive: boolean }) =>
  `whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
    isActive ? "bg-surface-2 text-ink" : "text-muted hover:text-ink"
  }`;

export function Layout() {
  const { user, logout } = useAuth();
  const { connected } = useLive();
  const [theme, toggleTheme] = useTheme();

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-line bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-2.5">
          <span className="text-sm font-bold tracking-tight">Branch Monitor</span>
          <nav className="flex min-w-0 flex-1 gap-1 overflow-x-auto" aria-label="Main">
            <NavLink to="/" end className={link}>
              Overview
            </NavLink>
            <NavLink to="/branches" className={link}>
              Branches
            </NavLink>
            <NavLink to="/incidents" className={link}>
              Incidents
            </NavLink>
          </nav>
          <span
            className="flex items-center gap-1.5 text-xs text-muted"
            role="status"
            title={connected ? "Receiving live updates" : "Live updates disconnected, retrying"}
          >
            <span className={`h-2 w-2 rounded-full ${connected ? "bg-up" : "bg-degraded animate-pulse"}`} aria-hidden />
            <span className="hidden sm:inline">{connected ? "Live" : "Reconnecting"}</span>
          </span>
          <button
            onClick={toggleTheme}
            className="rounded-md border border-line px-2 py-1 text-xs text-muted hover:text-ink"
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? "Light" : "Dark"}
          </button>
          <button
            onClick={logout}
            className="rounded-md border border-line px-2 py-1 text-xs text-muted hover:text-ink"
            title={user?.email}
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
