import { useCallback, useEffect, useState } from "react";

/** Current time in ms, refreshed every `everyMs`, so "3s ago" labels stay honest. */
export function useNow(everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const onChange = () => setMatches(m.matches);
    m.addEventListener("change", onChange);
    onChange();
    return () => m.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}

/** A remembered choice (e.g. cards vs table). Storage can be blocked, so it never throws. */
export function useStoredChoice<T extends string>(key: string, allowed: readonly T[]): [T | null, (v: T) => void] {
  const [value, setValue] = useState<T | null>(() => {
    try {
      const v = localStorage.getItem(key);
      return allowed.includes(v as T) ? (v as T) : null;
    } catch {
      return null;
    }
  });
  const set = useCallback(
    (v: T) => {
      setValue(v);
      try {
        localStorage.setItem(key, v);
      } catch {
        /* not persisted */
      }
    },
    [key],
  );
  return [value, set];
}
