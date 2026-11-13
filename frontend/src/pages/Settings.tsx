import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { CircleCheck, CirclePause, CircleX, Eye, EyeOff, Info, Send, TriangleAlert } from "lucide-react";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { fmtDateTime } from "../format";
import {
  changedKeys,
  checkPasswordChange,
  humanSeconds,
  monitoringExplanation,
  parseFieldErrors,
  parseSetting,
  retentionNote,
  settingError,
  settingsErrors,
  settingsToSend,
  toDraft,
  type Draft,
} from "../logic";
import { useToast } from "../toast";
import { Credit } from "../components/Credit";
import { Field } from "../components/Field";
import { ErrorState } from "../components/States";
import type { MonitorStatus, SettingValue, SettingsData, TelegramStatus } from "../types";

const SECTIONS = [
  { id: "monitoring", label: "Monitoring" },
  { id: "alerts", label: "Alerts and data" },
  { id: "report", label: "Weekly report" },
  { id: "telegram", label: "Telegram" },
  { id: "health", label: "Monitor health" },
  { id: "account", label: "Account" },
  { id: "about", label: "About" },
];

export function Settings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .settings()
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  return (
    <div>
      <h1 className="mb-1 text-xl font-semibold tracking-tight">Settings</h1>
      <p className="mb-5 text-sm text-muted">Changes to monitoring and alerts take effect immediately. No restart is needed.</p>

      <div className="grid gap-6 lg:grid-cols-[12rem_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="sticky top-20 hidden self-start lg:block">
          <ul className="space-y-0.5">
            {SECTIONS.map((s) => (
              <li key={s.id}>
                <a href={`#${s.id}`} className="block rounded-lg px-3 py-2 text-sm font-medium text-muted transition-colors duration-150 hover:bg-card2 hover:text-ink">
                  {s.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="max-w-3xl space-y-6">
          {error && !data ? (
            <ErrorState message={`Could not load settings: ${error}`} onRetry={load} />
          ) : !data ? (
            <div className="space-y-6" aria-busy="true" aria-label="Loading settings">
              <div className="skeleton h-72" />
              <div className="skeleton h-56" />
            </div>
          ) : (
            <>
              <MonitoringSection data={data} onSaved={setData} />
              <AlertsSection data={data} onSaved={setData} />
              <ReportSection data={data} onSaved={setData} />
            </>
          )}
          <TelegramSection />
          <HealthSection />
          <AccountSection />
          <Card id="about" title="About">
            <div className="flex items-center gap-3">
              <img src="/logo.png" alt="" className="size-12" />
              <div>
                <p className="font-semibold">OTech <span className="text-accent">NetWatch</span></p>
                <p className="text-sm text-muted">Network intelligence. Total visibility.</p>
                <Credit className="mt-1" />
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// shared: a card, and the form state for a group of saved settings
// ---------------------------------------------------------------------------------------------

function Card({ id, title, intro, children }: { id: string; title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-h`} className="card scroll-mt-24 p-5">
      <h2 id={`${id}-h`} className="text-base font-semibold">
        {title}
      </h2>
      {intro && <p className="mt-1 mb-4 text-sm text-muted">{intro}</p>}
      {!intro && <div className="mb-4" />}
      {children}
    </section>
  );
}

/**
 * Draft, validation and saving for one group of settings. `keysFor` says which fields currently matter
 * (e.g. the canary host is ignored while the canary is switched off), so hidden fields never block a save.
 */
function useSettingsForm(data: SettingsData, allKeys: string[], keysFor: (d: Draft) => string[], onSaved: (d: SettingsData) => void) {
  const toast = useToast();
  const formRef = useRef<HTMLFormElement>(null);
  const [draft, setDraft] = useState<Draft>(() => toDraft(data.values, allKeys));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  // After a save (or reload) the form shows what the server now holds.
  useEffect(() => {
    setDraft(toDraft(data.values, allKeys));
    setErrors({});
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const active = keysFor(draft);
  const dirty = changedKeys(draft, data.values, active, data.limits).length > 0;

  const set = (key: string, value: string | boolean) => {
    setDraft((d) => ({ ...d, [key]: value }));
    setErrors((e) => ({ ...e, [key]: "" }));
  };
  const isDefault = (key: string) => {
    const lim = data.limits[key];
    return !settingError(draft[key], lim) && parseSetting(draft[key], lim) === data.defaults[key];
  };
  const toDefault = (key: string) => set(key, toDraft(data.defaults, [key])[key]);
  const discard = () => {
    setDraft(toDraft(data.values, allKeys));
    setErrors({});
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const problems = settingsErrors(draft, active, data.limits);
    setErrors(problems);
    if (Object.keys(problems).length > 0) {
      requestAnimationFrame(() => formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    const payload: Record<string, SettingValue | null> = settingsToSend(draft, data.values, data.defaults, active, data.limits);
    if (Object.keys(payload).length === 0) return;
    setBusy(true);
    try {
      onSaved(await api.updateSettings(payload));
      toast.success("Saved. The monitor is using the new values now.");
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) setErrors(parseFieldErrors(err.message));
      else toast.error(err instanceof ApiError ? err.message : "Could not save. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return { formRef, draft, errors, busy, dirty, set, isDefault, toDefault, discard, save };
}

type SettingsForm = ReturnType<typeof useSettingsForm>;

function SaveBar({ form }: { form: SettingsForm }) {
  return (
    <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
      <button type="submit" className="btn btn-primary" disabled={!form.dirty || form.busy}>
        {form.busy ? "Saving…" : "Save changes"}
      </button>
      {form.dirty && (
        <button type="button" className="btn btn-ghost" onClick={form.discard} disabled={form.busy}>
          Discard
        </button>
      )}
      <span className="text-sm text-muted" role="status">
        {form.dirty ? "You have unsaved changes." : ""}
      </span>
    </div>
  );
}

/** A number field with its range, its default, and a way back to the default. */
function NumberField({
  form,
  data,
  name,
  label,
  help,
  unit,
  step = "1",
}: {
  form: SettingsForm;
  data: SettingsData;
  name: string;
  label: string;
  help: ReactNode;
  unit: string;
  step?: string;
}) {
  const lim = data.limits[name];
  return (
    <Field
      label={label}
      error={form.errors[name]}
      hint={
        <>
          {help} <span className="whitespace-nowrap">Default {String(data.defaults[name])}{unit ? ` ${unit}` : ""}.</span>{" "}
          {!form.isDefault(name) && (
            <button type="button" className="cursor-pointer font-medium text-accent underline underline-offset-2" onClick={() => form.toDefault(name)}>
              Reset to default
            </button>
          )}
        </>
      }
    >
      {(p) => (
        <div className="flex items-center gap-2">
          <input
            {...p}
            type="number"
            inputMode={lim.kind === "int" ? "numeric" : "decimal"}
            min={lim.min}
            max={lim.max}
            step={step}
            value={String(form.draft[name])}
            onChange={(e) => form.set(name, e.target.value)}
            className="input num max-w-40"
          />
          {unit && <span className="text-sm text-muted">{unit}</span>}
        </div>
      )}
    </Field>
  );
}

// ---------------------------------------------------------------------------------------------
// Monitoring
// ---------------------------------------------------------------------------------------------

const MONITORING_KEYS = ["probe_interval_s", "probe_count", "probe_timeout_s", "confirm", "canary_enabled", "canary_type", "canary_host", "canary_port"];

function monitoringKeys(d: Draft): string[] {
  const keys = ["probe_interval_s", "probe_count", "probe_timeout_s", "confirm", "canary_enabled"];
  if (d.canary_enabled) keys.push("canary_type", "canary_host", ...(d.canary_type === "tcp" ? ["canary_port"] : []));
  return keys;
}

function MonitoringSection({ data, onSaved }: { data: SettingsData; onSaved: (d: SettingsData) => void }) {
  const form = useSettingsForm(data, MONITORING_KEYS, monitoringKeys, onSaved);
  const { draft } = form;
  const note = monitoringExplanation(draft);

  return (
    <Card id="monitoring" title="Monitoring" intro="How often branches are checked and how sure the monitor must be before it raises an alert.">
      <form ref={form.formRef} onSubmit={form.save} noValidate className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField form={form} data={data} name="probe_interval_s" label="Check every" unit="seconds" help="How often every branch is checked." />
          <NumberField form={form} data={data} name="confirm" label="Confirm after" unit="checks" help="Matching results needed before a status change is confirmed. Higher means fewer false alarms but slower alerts." />
          <NumberField form={form} data={data} name="probe_count" label="Pings per check" unit="pings" help="More pings give a finer packet-loss reading but take longer." />
          <NumberField form={form} data={data} name="probe_timeout_s" label="Wait for each ping" unit="seconds" step="0.1" help="How long to wait for a reply before counting a ping as lost." />
        </div>

        {note && (
          <div className="rounded-lg border border-line bg-card2 p-3 text-sm">
            <p className="flex items-start gap-2">
              <Info className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
              {note.detect}
            </p>
            {note.warning && (
              <p className="mt-2 flex items-start gap-2 text-degraded" role="status">
                <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
                {note.warning}
              </p>
            )}
          </div>
        )}

        <fieldset className="rounded-lg border border-line p-4">
          <legend className="px-1 text-sm font-semibold">Internet check (canary)</legend>
          <p className="mb-3 text-sm text-muted">
            Tells your branches apart from the monitor's own network. If this and every branch fail at once, the monitor's connection is the likely cause, so it
            sends one warning instead of an alert per branch.
          </p>
          <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm font-medium">
            <input type="checkbox" className="size-5 cursor-pointer accent-[var(--primary)]" checked={Boolean(draft.canary_enabled)} onChange={(e) => form.set("canary_enabled", e.target.checked)} />
            Also check the internet
          </label>
          {draft.canary_enabled ? (
            <div className="mt-3 grid gap-4 sm:grid-cols-3">
              <Field label="Check with" error={form.errors.canary_type} hint="TCP is more reliable: many networks block ping to public addresses.">
                {(p) => (
                  <select {...p} value={String(draft.canary_type)} onChange={(e) => form.set("canary_type", e.target.value)} className="input">
                    <option value="tcp">TCP connect</option>
                    <option value="icmp">ICMP ping</option>
                  </select>
                )}
              </Field>
              <Field label="Address" error={form.errors.canary_host} hint="A public address that is almost always up.">
                {(p) => <input {...p} value={String(draft.canary_host)} onChange={(e) => form.set("canary_host", e.target.value)} className="input mono" autoComplete="off" autoCapitalize="off" spellCheck={false} />}
              </Field>
              {draft.canary_type === "tcp" && (
                <Field label="Port" error={form.errors.canary_port}>
                  {(p) => <input {...p} type="number" inputMode="numeric" min={1} max={65535} value={String(draft.canary_port)} onChange={(e) => form.set("canary_port", e.target.value)} className="input num" />}
                </Field>
              )}
            </div>
          ) : (
            <p className="mt-2 flex items-start gap-2 text-sm text-degraded">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              Without it the monitor cannot tell its own network outage from your branches going down, and you may get one alert per branch.
            </p>
          )}
        </fieldset>

        <SaveBar form={form} />
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------------
// Alerts and data
// ---------------------------------------------------------------------------------------------

const ALERT_KEYS = ["reminder_minutes", "retention_days", "default_latency_warn_ms", "default_loss_warn_pct"];

function AlertsSection({ data, onSaved }: { data: SettingsData; onSaved: (d: SettingsData) => void }) {
  const form = useSettingsForm(data, ALERT_KEYS, () => ALERT_KEYS, onSaved);
  const retention = retentionNote(Number(form.draft.retention_days));

  return (
    <Card id="alerts" title="Alerts and data" intro="Reminders, how long history is kept, and the thresholds suggested for new branches.">
      <form ref={form.formRef} onSubmit={form.save} noValidate className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField form={form} data={data} name="reminder_minutes" label="Remind me every" unit="minutes" help="Re-alert while an incident is open and not acknowledged. 0 turns reminders off." />
          <div>
            <NumberField form={form} data={data} name="retention_days" label="Keep raw checks for" unit="days" help="Older checks are deleted. Incidents are always kept. 0 keeps everything." />
            {retention && (
              <p className="mt-1.5 flex items-start gap-2 text-xs text-degraded" role="status">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                {retention}
              </p>
            )}
          </div>
          <NumberField form={form} data={data} name="default_latency_warn_ms" label="New branches warn above" unit="ms" step="any" help="Pre-filled when you add a branch. Existing branches keep their own value." />
          <NumberField form={form} data={data} name="default_loss_warn_pct" label="New branches warn above loss" unit="%" step="any" help="Same, for packet loss." />
        </div>
        <SaveBar form={form} />
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------------
// Weekly report
// ---------------------------------------------------------------------------------------------

const REPORT_KEYS = ["weekly_report_enabled"];

function ReportSection({ data, onSaved }: { data: SettingsData; onSaved: (d: SettingsData) => void }) {
  const toast = useToast();
  const form = useSettingsForm(data, REPORT_KEYS, () => REPORT_KEYS, onSaved);
  const [sending, setSending] = useState(false);

  const sendNow = async () => {
    setSending(true);
    try {
      const r = await api.sendReport();
      if (r.ok) toast.success("Report sent. Check Telegram.");
      else toast.error(`Telegram did not accept the report: ${r.error ?? "unknown error"}`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not send the report.");
    } finally {
      setSending(false);
    }
  };

  return (
    <Card id="report" title="Weekly report" intro="A summary of the last 7 days sent to Telegram every Monday at 08:00 (the server's time zone): uptime per branch, incidents and downtime, worst first.">
      <form ref={form.formRef} onSubmit={form.save} noValidate className="space-y-4">
        <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm font-medium">
          <input type="checkbox" className="size-5 cursor-pointer accent-[var(--primary)]" checked={Boolean(form.draft.weekly_report_enabled)} onChange={(e) => form.set("weekly_report_enabled", e.target.checked)} />
          Send the weekly report
        </label>
        <div>
          <button type="button" className="btn btn-secondary" onClick={sendNow} disabled={sending}>
            <Send className="size-4" aria-hidden /> {sending ? "Sending…" : "Send a report now"}
          </button>
          <p className="mt-1.5 text-xs text-muted">Sends the current report immediately. It does not replace Monday's.</p>
        </div>
        <SaveBar form={form} />
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------------------------------------
// Telegram
// ---------------------------------------------------------------------------------------------

function ago(iso: string | null): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 60 ? "just now" : `${humanSeconds(s)} ago`;
}

function TelegramSection() {
  const toast = useToast();
  const [tg, setTg] = useState<TelegramStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const load = useCallback(() => {
    api
      .telegram()
      .then((t) => {
        setTg(t);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const test = async () => {
    setSending(true);
    try {
      const r = await api.telegramTest();
      if (r.ok) toast.success("Test message sent. Check Telegram.");
      else toast.error(`Telegram did not accept the message: ${r.error ?? "unknown error"}`);
      load();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not send the test message.");
    } finally {
      setSending(false);
    }
  };

  return (
    <Card id="telegram" title="Telegram" intro="Where alerts are sent. The bot token and chat ID are secrets, so they stay in the server's .env file.">
      {error && !tg ? (
        <ErrorState message={error} onRetry={load} />
      ) : !tg ? (
        <div className="skeleton h-28" aria-busy="true" />
      ) : (
        <>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted">Bot</dt>
              <dd className="mt-0.5 flex items-center gap-2 font-medium">
                {!tg.configured ? (
                  <>
                    <CirclePause className="size-4 text-pending" aria-hidden /> Not configured
                  </>
                ) : tg.bot ? (
                  <>
                    <CircleCheck className="size-4 text-up" aria-hidden /> Connected as @{tg.bot.username}
                  </>
                ) : (
                  <>
                    <CircleX className="size-4 text-down" aria-hidden /> Cannot reach Telegram
                  </>
                )}
              </dd>
              {tg.bot_error && <dd className="mt-0.5 text-xs text-muted">{tg.bot_error}</dd>}
            </div>
            <div>
              <dt className="text-xs text-muted">Authorised chat</dt>
              <dd className="mono mt-0.5 font-medium">{tg.chat ?? "—"}</dd>
              <dd className="mt-0.5 text-xs text-muted">Only this chat can use the bot's commands and buttons.</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Last message delivered</dt>
              <dd className="mt-0.5 font-medium">{ago(tg.delivered_at)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted">Alerts waiting to be re-sent</dt>
              <dd className={`mt-0.5 font-medium ${tg.pending > 0 ? "text-degraded" : ""}`}>{tg.pending === 0 ? "None" : tg.pending}</dd>
            </div>
          </dl>

          {tg.last_error && (
            <p className="mt-4 flex items-start gap-2 text-sm text-degraded" role="status">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              Last failure {ago(tg.last_error_at)}: {tg.last_error}
              {tg.last_error_at ? <span className="text-muted"> ({fmtDateTime(tg.last_error_at)})</span> : null}
            </p>
          )}

          <div className="mt-5 border-t border-line pt-4">
            <button className="btn btn-secondary" onClick={test} disabled={sending || !tg.configured}>
              <Send className="size-4" aria-hidden /> {sending ? "Sending…" : "Send test message"}
            </button>
          </div>
        </>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------------------------
// Monitor health (read-only)
// ---------------------------------------------------------------------------------------------

function HealthRow({ tone, title, detail }: { tone: "ok" | "warn" | "bad" | "idle"; title: string; detail: string }) {
  const icon =
    tone === "ok" ? <CircleCheck className="size-5 text-up" aria-hidden /> :
    tone === "warn" ? <TriangleAlert className="size-5 text-degraded" aria-hidden /> :
    tone === "bad" ? <CircleX className="size-5 text-down" aria-hidden /> :
    <CirclePause className="size-5 text-pending" aria-hidden />;
  return (
    <li className="flex items-start gap-3 py-3">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0">
        <p className="font-medium">{title}</p>
        <p className="text-sm text-muted">{detail}</p>
      </div>
    </li>
  );
}

function HealthSection() {
  const [s, setS] = useState<MonitorStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .status()
      .then((v) => {
        setS(v);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 10_000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <Card id="health" title="Monitor health" intro="Whether the monitor itself is working. This refreshes every 10 seconds.">
      {error && !s ? (
        <ErrorState message={error} onRetry={load} />
      ) : !s ? (
        <div className="skeleton h-40" aria-busy="true" />
      ) : (
        <ul className="divide-y divide-line">
          <HealthRow
            tone={s.probe_loop.ok ? "ok" : "bad"}
            title="Checking branches"
            detail={
              s.probe_loop.ok
                ? `Running every ${s.interval_s}s. Last check ${s.probe_loop.last_cycle_age_s === null ? "pending" : `${Math.round(s.probe_loop.last_cycle_age_s)}s ago`}, ${s.probe_loop.cycles.toLocaleString()} checks since it started.`
                : `No check has completed for a while (last ${s.probe_loop.last_cycle_age_s === null ? "never" : `${Math.round(s.probe_loop.last_cycle_age_s)}s ago`}). Alerts may not be raised.`
            }
          />
          <HealthRow tone={s.database ? "ok" : "bad"} title="Database" detail={s.database ? "Reachable." : "Not reachable: history cannot be saved."} />
          <HealthRow
            tone={s.alerts_pending > 0 ? "warn" : "ok"}
            title="Telegram alerts"
            detail={s.alerts_pending > 0 ? `${s.alerts_pending} could not be sent and will be retried automatically.` : "Nothing waiting. All alerts were delivered."}
          />
          <HealthRow
            tone={!s.heartbeat.configured ? "idle" : s.heartbeat.failing ? "bad" : "ok"}
            title="Dead-man's switch"
            detail={
              !s.heartbeat.configured
                ? "Not set up. Add HEARTBEAT_URL to .env to be told if this monitor stops."
                : s.heartbeat.failing
                  ? "Pings are not getting through, so you would not be warned if the monitor stopped."
                  : `Healthy. Last ping ${s.heartbeat.last_ok_age_s === null ? "pending" : `${Math.round(s.heartbeat.last_ok_age_s)}s ago`}.`
            }
          />
          <HealthRow tone="ok" title="Running for" detail={humanSeconds(s.uptime_s)} />
        </ul>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------------------------
// Account
// ---------------------------------------------------------------------------------------------

function AccountSection() {
  const { user } = useAuth();
  const toast = useToast();
  const formRef = useRef<HTMLFormElement>(null);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [show, setShow] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [general, setGeneral] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const type = show ? "text" : "password";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setGeneral(null);
    const problems = checkPasswordChange(current, next, repeat);
    setErrors(problems);
    if (Object.keys(problems).length > 0) {
      requestAnimationFrame(() => formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    setBusy(true);
    try {
      const r = await api.changePassword(current, next);
      toast.success(
        r.signed_out_others > 0
          ? `Password changed. ${r.signed_out_others} other device${r.signed_out_others === 1 ? " was" : "s were"} signed out.`
          : "Password changed.",
      );
      setCurrent("");
      setNext("");
      setRepeat("");
      setErrors({});
    } catch (err) {
      if (err instanceof ApiError && err.status === 400) setErrors({ current: err.message });
      else if (err instanceof ApiError && err.status === 422) {
        const fields = parseFieldErrors(err.message);
        const message = fields.new_password ?? fields.next;
        if (message) setErrors({ next: message });
        else setGeneral(err.message);
      } else setGeneral(err instanceof ApiError ? err.message : "Could not change the password. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card id="account" title="Account" intro={<>Signed in as <span className="font-medium text-ink">{user?.email}</span>.</>}>
      <form ref={formRef} onSubmit={submit} noValidate className="max-w-md space-y-4">
        <h3 className="text-sm font-semibold">Change password</h3>
        <Field label="Current password" error={errors.current}>
          {(p) => <input {...p} type={type} autoComplete="current-password" value={current} onChange={(e) => { setCurrent(e.target.value); setErrors((x) => ({ ...x, current: "" })); }} className="input" />}
        </Field>
        <Field label="New password" error={errors.next} hint="At least 10 characters. Your other devices are signed out; this one stays signed in.">
          {(p) => <input {...p} type={type} autoComplete="new-password" value={next} onChange={(e) => { setNext(e.target.value); setErrors((x) => ({ ...x, next: "" })); }} className="input" />}
        </Field>
        <Field label="Repeat new password" error={errors.repeat}>
          {(p) => <input {...p} type={type} autoComplete="new-password" value={repeat} onChange={(e) => { setRepeat(e.target.value); setErrors((x) => ({ ...x, repeat: "" })); }} className="input" />}
        </Field>

        <button type="button" className="btn btn-ghost -ml-3" onClick={() => setShow((v) => !v)} aria-pressed={show}>
          {show ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
          {show ? "Hide passwords" : "Show passwords"}
        </button>

        {general && (
          <p role="alert" className="field-error text-sm">
            {general}
          </p>
        )}

        <div>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Changing…" : "Change password"}
          </button>
        </div>
      </form>
    </Card>
  );
}
