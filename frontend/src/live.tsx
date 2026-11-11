import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { LiveSnapshot } from "./types";

interface LiveState {
  connected: boolean;
  snapshots: Record<number, LiveSnapshot>;
  monitorSuspect: boolean;
  /** Increments on every confirmed status change; pages refetch when it moves. */
  transitionTick: number;
  /** Increments on every probe cycle. */
  cycleTick: number;
  lastCycleAt: string | null;
}

const Ctx = createContext<LiveState | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [snapshots, setSnapshots] = useState<Record<number, LiveSnapshot>>({});
  const [monitorSuspect, setMonitorSuspect] = useState(false);
  const [transitionTick, setTransitionTick] = useState(0);
  const [cycleTick, setCycleTick] = useState(0);
  const [lastCycleAt, setLastCycleAt] = useState<string | null>(null);
  const retry = useRef(0);

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
          setCycleTick((n) => n + 1);
        } else if (msg.type === "transition") {
          setTransitionTick((n) => n + 1);
        }
      };
      ws.onclose = () => {
        setConnected(false);
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
    () => ({ connected, snapshots, monitorSuspect, transitionTick, cycleTick, lastCycleAt }),
    [connected, snapshots, monitorSuspect, transitionTick, cycleTick, lastCycleAt],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLive(): LiveState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLive outside LiveProvider");
  return v;
}
