import { useEffect, useState } from "react";

const SHOW_MS = 2200;

/** Branded start screen: shown on every load, skippable by click or key, fades into the app. */
export function Splash() {
  const [phase, setPhase] = useState<"show" | "leaving" | "gone">("show");

  useEffect(() => {
    if (phase !== "show") return;
    const leave = () => setPhase("leaving");
    const t = window.setTimeout(leave, SHOW_MS);
    window.addEventListener("keydown", leave);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("keydown", leave);
    };
  }, [phase]);

  useEffect(() => {
    if (phase !== "leaving") return;
    const t = window.setTimeout(() => setPhase("gone"), 450);
    return () => window.clearTimeout(t);
  }, [phase]);

  if (phase === "gone") return null;
  return (
    <div
      role="presentation"
      onClick={() => setPhase("leaving")}
      className={`splash fixed inset-0 z-[100] flex cursor-pointer flex-col items-center justify-center bg-[#0b1220] text-white transition-opacity duration-[450ms] ${phase === "leaving" ? "opacity-0" : "opacity-100"}`}
    >
      <img src="/logo.png" alt="" className="splash-logo size-28 sm:size-36" />
      <svg viewBox="0 0 240 24" className="mt-5 h-6 w-60" aria-hidden fill="none">
        <path
          className="splash-pulse"
          d="M0 12 H80 L92 12 L100 3 L110 21 L119 7 L126 12 H240"
          stroke="#22d3ee"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          pathLength="1"
        />
      </svg>
      <div className="splash-name mt-4 flex flex-col items-center leading-none">
        <span className="text-xs font-extrabold tracking-[0.35em]">OTECH</span>
        <span className="mt-1 text-3xl font-bold tracking-tight">
          Net<span className="text-[#22d3ee]">Watch</span>
        </span>
      </div>
      <p className="splash-credit absolute bottom-8 text-xs tracking-wide text-slate-400">Made by Okoh Technologies</p>
      <span className="sr-only" role="status">Loading OTech NetWatch</span>
    </div>
  );
}
