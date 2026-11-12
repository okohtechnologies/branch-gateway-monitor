import { useEffect, useState, type FormEvent } from "react";
import { Activity, Eye, EyeOff, RefreshCw } from "lucide-react";
import { useAuth } from "../auth";
import { ApiError } from "../api";
import { useNow } from "../hooks";

function clock(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<{ text: string; offline: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const now = useNow(1000);

  const remaining = lockedUntil === null ? 0 : Math.max(0, Math.ceil((lockedUntil - now) / 1000));
  const locked = remaining > 0;

  // When the lockout ends, clear the message so the form is usable again.
  useEffect(() => {
    if (lockedUntil !== null && remaining === 0) {
      setLockedUntil(null);
      setError(null);
    }
  }, [lockedUntil, remaining]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (locked) return;
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429 && err.retryAfter) {
        setLockedUntil(Date.now() + err.retryAfter * 1000);
      } else if (err instanceof ApiError && err.status === 0) {
        setError({ text: "Cannot reach the server. Check your connection and try again.", offline: true });
      } else {
        setError({ text: err instanceof ApiError ? err.message : "Something went wrong. Try again.", offline: false });
      }
      setPassword("");
      document.getElementById("login-password")?.focus();
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-8">
      <form onSubmit={submit} className="card w-full max-w-sm p-6 shadow-xl sm:p-7">
        <div className="mb-5 flex items-center gap-2.5">
          <Activity className="size-6 text-accent" aria-hidden />
          <h1 className="text-xl font-semibold tracking-tight">Branch Monitor</h1>
        </div>
        <p className="mb-5 text-sm text-muted">Sign in to see the status of your branches.</p>

        <div className="space-y-4">
          <div>
            <label htmlFor="login-email" className="label">Email</label>
            <input id="login-email" type="email" autoComplete="username" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} className="input" disabled={busy} />
          </div>
          <div>
            <label htmlFor="login-password" className="label">Password</label>
            <div className="relative">
              <input
                id="login-password"
                type={show ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input pr-12"
                disabled={busy}
                aria-invalid={Boolean(error) && !error?.offline}
                aria-describedby={error || locked ? "login-error" : undefined}
              />
              <button
                type="button"
                onClick={() => setShow((v) => !v)}
                className="icon-btn absolute top-0 right-0"
                aria-label={show ? "Hide password" : "Show password"}
                aria-pressed={show}
              >
                {show ? <EyeOff className="size-5" aria-hidden /> : <Eye className="size-5" aria-hidden />}
              </button>
            </div>
          </div>
        </div>

        {(locked || error) && (
          <p id="login-error" role="alert" className="mt-4 text-sm font-medium text-down">
            {locked ? `Too many failed attempts. Try again in ${clock(remaining)}.` : error?.text}
          </p>
        )}

        <button type="submit" disabled={busy || locked} className="btn btn-primary mt-5 w-full">
          {busy ? "Signing in…" : locked ? `Locked · ${clock(remaining)}` : "Sign in"}
        </button>

        {error?.offline && (
          <button type="button" onClick={() => setError(null)} className="btn btn-ghost mt-2 w-full">
            <RefreshCw className="size-4" aria-hidden /> Try again
          </button>
        )}
      </form>
    </main>
  );
}
