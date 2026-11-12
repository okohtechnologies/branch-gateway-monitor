import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNow } from "./hooks";
import { isStale } from "./logic";
import type { LiveSnapshot, Status } from "./types";

interface Announcement {
  id: number;
  text: string;
}

interface LiveState {
  connected: boolean;
  snapshots: Record<number, LiveSnapshot>;
  monitorSuspect: boolean;
  /** Increments on every confirmed status change; pages refetch when it moves. */
  transitionTick: number;
  /** Increments on every probe cycle. */
  cycleTick: number;
  lastCycleAt: string | null;
  /** Observed seconds between probe cycles (null until two have arrived). */
  cycleIntervalS: number | null;
  /** The latest status change, phrased for a screen-reader live region. */
  announcement: Announcement | null;
}

const Ctx = createContext<LiveState | null>(null);

const SPOKEN: Record<Status, string> = {
  UP: "is back up",
  DEGRADED: "is degraded",
  DOWN: "is down",
  PENDING: "is pending",
  DISABLED: "is disabled",
};

export function LiveProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [snapshots, setSnapshots] = useState<Record<number, LiveSnapshot>>({});
  const [monitorSuspect, setMonitorSuspect] = useState(false);
  const [transitionTick, setTransitionTick] = useState(0);
  const [cycleTick, setCycleTick] = useState(0);
  const [lastCycleAt, setLastCycleAt] = useState<string | null>(null);
  const [cycleIntervalS, setCycleIntervalS] = useState<number | null>(null);
  const [announcement, setAnnouncement] = useState<Announcement | null>(null);
  const retry = useRef(0);
  const prevCycle = useRef<number | null>(null);
  const announceId = useRef(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.onopen = () => {
        retry.current = 0;
        setConnected(true);
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "cycle") {
          const next: Record<number, LiveSnapshot> = {};
          for (const b of msg.branches as LiveSnapshot[]) next[b.branch_id] = b;
          setSnapshots(next);
          setMonitorSuspect(Boolean(msg.monitor_suspect));
          setLastCycleAt(msg.ts);
          const ts = new Date(msg.ts).getTime();
          if (prevCycle.current !== null) {
            const gap = (ts - prevCycle.current) / 1000;
            if (gap >= 1 && gap <= 300) setCycleIntervalS(Math.round(gap));
          }
          prevCycle.current = ts;
          setCycleTick((n) => n + 1);
        } else if (msg.type === "transition") {
          setTransitionTick((n) => n + 1);
          const status = msg.status as Status;
          setAnnouncement({ id: ++announceId.current, text: `${msg.name} ${SPOKEN[status] ?? status}` });
        }
      };
      ws.onclose = () => {
        setConnected(false);
        prevCycle.current = null;
        if (closed) return;
        // Exponential backoff, capped at 15s.
        const delay = Math.min(15000, 1000 * 2 ** retry.current++);
        timer = setTimeout(connect, delay);
      };
    };

    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);

  const value = useMemo(
    () => ({ connected, snapshots, monitorSuspect, transitionTick, cycleTick, lastCycleAt, cycleIntervalS, announcement }),
    [connected, snapshots, monitorSuspect, transitionTick, cycleTick, lastCycleAt, cycleIntervalS, announcement],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLive(): LiveState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLive outside LiveProvider");
  return v;
}

/** How fresh the data on screen is: drives the "Updated 3s ago" / "Stale" label. */
export function useFreshness(): { stale: boolean; ageS: number | null; connected: boolean } {
  const { connected, lastCycleAt, cycleIntervalS } = useLive();
  const now = useNow(1000);
  const last = lastCycleAt ? new Date(lastCycleAt).getTime() : null;
  return {
    stale: isStale(now, last, cycleIntervalS, connected),
    ageS: last === null ? null : Math.max(0, Math.round((now - last) / 1000)),
    connected,
  };
}
