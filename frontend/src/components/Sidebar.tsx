import { useEffect, useRef, useState, type FocusEvent, type KeyboardEvent, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { Credit } from "./Credit";
import { LogOut, Moon, PanelLeftClose, PanelLeftOpen, ShieldOff, Sun } from "lucide-react";
import { useAuth } from "../auth";
import { useTheme } from "../theme";
import { NAV, navActive, SignOutEverywhereDialog, useLiveState, WarningDot } from "./chrome";

const ENTER_DELAY_MS = 100; // small delay so a pointer merely passing the edge does not open it
const LEAVE_DELAY_MS = 250; // and a brief slip off it does not slam it shut

// Every row is icon + label. Collapsed, the sidebar is only wide enough for the icon and the
// label is clipped (still present for screen readers); expanded, the whole row shows.
const ROW = "relative flex h-11 w-full items-center gap-3 rounded-lg px-2.5 text-sm font-medium whitespace-nowrap transition-colors duration-150";
const ACTION = `${ROW} cursor-pointer text-muted hover:bg-card2 hover:text-ink`;

function Icon({ children }: { children: ReactNode }) {
  return <span className="flex size-5 shrink-0 items-center justify-center">{children}</span>;
}

/** Fades out when the sidebar is collapsed, so no half-clipped letters show at the edge.
 *  Opacity (unlike display:none) keeps the text available to screen readers. */
function Label({ show, children, className = "" }: { show: boolean; children: ReactNode; className?: string }) {
  return <span className={`transition-opacity duration-150 ${show ? "opacity-100" : "opacity-0"} ${className}`}>{children}</span>;
}

/**
 * Desktop navigation. A slim icon rail that slides open over the page (the content never shifts)
 * on pointer hover or keyboard focus, or stays open when pinned. Touch-only screens start pinned,
 * since they cannot hover.
 */
export function Sidebar({ pinned, onTogglePin }: { pinned: boolean; onTogglePin: () => void }) {
  const { user, logout } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const live = useLiveState();
  const onDetail = useLocation().pathname.startsWith("/branches/");
  const [hover, setHover] = useState(false);
  const [focused, setFocused] = useState(false);
  const [confirmAll, setConfirmAll] = useState(false);
  const enterTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const leaveTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => () => {
    clearTimeout(enterTimer.current);
    clearTimeout(leaveTimer.current);
  }, []);

  const expanded = pinned || hover || focused;

  const onEnter = () => {
    clearTimeout(leaveTimer.current);
    enterTimer.current = setTimeout(() => setHover(true), ENTER_DELAY_MS);
  };
  const onLeave = () => {
    clearTimeout(enterTimer.current);
    leaveTimer.current = setTimeout(() => setHover(false), LEAVE_DELAY_MS);
  };
  // Only KEYBOARD focus opens it: clicking a link with the mouse must not leave it stuck open.
  const onFocus = (e: FocusEvent<HTMLElement>) => {
    if ((e.target as HTMLElement).matches(":focus-visible")) setFocused(true);
  };
  const onBlur = (e: FocusEvent<HTMLElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFocused(false);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Escape" && !pinned && expanded) {
      setHover(false);
      setFocused(false);
      document.getElementById("main")?.focus(); // keep keyboard users oriented instead of dropping focus
    }
  };

  const link = (to: string) => ({ isActive }: { isActive: boolean }) =>
    `${ROW} ${
      navActive(to, isActive, onDetail)
        ? "bg-card2 text-ink before:absolute before:top-2 before:-left-2 before:bottom-2 before:w-[3px] before:rounded-r before:bg-accent"
        : "text-muted hover:bg-card2 hover:text-ink"
    }`;

  return (
    <>
      <aside
        aria-label="Sidebar"
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
        onFocus={onFocus}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        className={`fixed inset-y-0 left-0 z-40 hidden flex-col overflow-x-hidden overflow-y-auto border-r border-line bg-card p-2 transition-[width,box-shadow] duration-200 ease-out md:flex ${
          expanded ? "w-60" : "w-14"
        } ${expanded && !pinned ? "shadow-2xl" : ""}`}
      >
        <div className="mb-2 flex h-11 items-center">
          <span className={`${ROW} w-auto min-w-0 flex-1 font-semibold tracking-tight`}>
            <Icon>
              <img src="/logo.png" alt="" className="size-7" />
            </Icon>
            <Label show={expanded}><span className="flex flex-col leading-none"><span className="text-[0.6rem] font-extrabold tracking-[0.2em]">OTECH</span><span className="font-bold">Net<span className="text-accent">Watch</span></span></span></Label>
          </span>
          <button
            onClick={onTogglePin}
            aria-pressed={pinned}
            aria-label="Keep sidebar open"
            title={pinned ? "Let the sidebar hide" : "Keep the sidebar open"}
            className={`icon-btn shrink-0 transition-opacity duration-150 ${expanded ? "opacity-100" : "pointer-events-none opacity-0"}`}
          >
            {pinned ? <PanelLeftClose className="size-5" aria-hidden /> : <PanelLeftOpen className="size-5" aria-hidden />}
          </button>
        </div>

        <nav aria-label="Main" className="flex flex-col gap-1">
          {NAV.map(({ to, label, icon: I, end }) => (
            <NavLink key={to} to={to} end={end} className={link(to)}>
              <Icon>
                <I className="size-5" aria-hidden />
              </Icon>
              <Label show={expanded}>{label}</Label>
            </NavLink>
          ))}
        </nav>

        <div className="mt-auto flex flex-col gap-1 border-t border-line pt-2">
          {/* Silent while all is well. The container is always present so that, when the connection drops,
              screen readers announce the warning that appears inside it. */}
          <div role="status">
            {live && (
              <div className={`${ROW} text-degraded`} title={live.tip}>
                <Icon>
                  <WarningDot pulse={live.pulse} />
                </Icon>
                <Label show={expanded}>{live.label}</Label>
              </div>
            )}
          </div>

          <div className={`${ROW} text-muted`} title={user?.email}>
            <Icon>
              <span className="flex size-5 items-center justify-center rounded-full bg-card2 text-xs font-semibold text-ink" aria-hidden>
                {(user?.email ?? "?")[0].toUpperCase()}
              </span>
            </Icon>
            <Label show={expanded} className="min-w-0 truncate">{user?.email}</Label>
          </div>

          <button onClick={toggleTheme} className={ACTION}>
            <Icon>{theme === "dark" ? <Sun className="size-5" aria-hidden /> : <Moon className="size-5" aria-hidden />}</Icon>
            <Label show={expanded}>{theme === "dark" ? "Light theme" : "Dark theme"}</Label>
          </button>
          <button onClick={logout} className={ACTION}>
            <Icon>
              <LogOut className="size-5" aria-hidden />
            </Icon>
            <Label show={expanded}>Sign out</Label>
          </button>
          <button onClick={() => setConfirmAll(true)} className={ACTION}>
            <Icon>
              <ShieldOff className="size-5" aria-hidden />
            </Icon>
            <Label show={expanded}>Sign out of all devices</Label>
          </button>
          <Label show={expanded} className="block px-2.5 pt-1 pb-0.5 whitespace-nowrap"><Credit /></Label>
        </div>
      </aside>
      {confirmAll && <SignOutEverywhereDialog onClose={() => setConfirmAll(false)} />}
    </>
  );
}
