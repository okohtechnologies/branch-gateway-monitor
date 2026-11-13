import type { Branch, BranchDetail, BranchesResponse, BranchInput, Incident, MonitorStatus, Point, Range, ScanView, SettingValue, SettingsData, TelegramStatus } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    /** Seconds to wait before retrying, from a Retry-After header (login lockout). */
    public retryAfter?: number,
  ) {
    super(message);
  }
}

/** Turns FastAPI error bodies (string or validation list) into one readable line. */
export function errorMessage(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const field = d.loc?.filter((p) => p !== "body").join(".");
        const msg = (d.msg ?? "").replace(/^Value error, /, "");
        return field ? `${field}: ${msg}` : msg;
      })
      .join("; ");
  }
  return fallback;
}

type UnauthorizedHandler = () => void;
let onUnauthorized: UnauthorizedHandler | null = null;
export const setUnauthorizedHandler = (h: UnauthorizedHandler | null) => {
  onUnauthorized = h;
};

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server");
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    // A 401 on login itself just means wrong credentials, not an expired session.
    if (res.status === 401 && url !== "/api/login") onUnauthorized?.();
    const wait = Number(res.headers.get("Retry-After"));
    throw new ApiError(
      res.status,
      errorMessage(data, `Request failed (${res.status})`),
      Number.isFinite(wait) && wait > 0 ? wait : undefined,
    );
  }
  return data as T;
}

export interface User {
  email: string;
  role: string;
}

export const api = {
  me: () => request<User>("GET", "/api/me"),
  login: (email: string, password: string) => request<User>("POST", "/api/login", { email, password }),
  logout: () => request<{ ok: boolean }>("POST", "/api/logout"),
  logoutAll: () => request<{ ok: boolean; signed_out: number }>("POST", "/api/logout-all"),
  branches: () => request<BranchesResponse>("GET", "/api/branches"),
  branch: (id: number) => request<BranchDetail>("GET", `/api/branches/${id}`),
  checks: (id: number, range: Range) =>
    request<{ points: Point[] }>("GET", `/api/branches/${id}/checks?range=${range}`),
  incidents: (opts: { branchId?: number; openOnly?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (opts.branchId !== undefined) q.set("branch_id", String(opts.branchId));
    if (opts.openOnly) q.set("open_only", "true");
    q.set("limit", "500");
    return request<{ incidents: Incident[] }>("GET", `/api/incidents?${q}`);
  },
  createBranch: (b: BranchInput) => request<Branch>("POST", "/api/branches", b),
  updateBranch: (id: number, b: BranchInput) => request<Branch>("PUT", `/api/branches/${id}`, b),
  deleteBranch: (id: number) => request<void>("DELETE", `/api/branches/${id}`),
  startScan: (target: string) => request<ScanView>("POST", "/api/scans", { target }),
  scan: (id: string, since: number, version: number) =>
    request<ScanView>("GET", `/api/scans/${id}?since=${since}&version=${version}`),
  nameHost: (ip: string, label: string) => request<{ ip: string; label: string }>("PUT", `/api/scanner/hosts/${ip}`, { label }),
  forgetHost: (ip: string) => request<void>("DELETE", `/api/scanner/hosts/${ip}`),
  cancelScan: (id: string) => request<{ cancelled: boolean }>("POST", `/api/scans/${id}/cancel`),
  settings: () => request<SettingsData>("GET", "/api/settings"),
  /** A value of null resets that setting to its default. */
  updateSettings: (values: Record<string, SettingValue | null>) => request<SettingsData>("PUT", "/api/settings", { values }),
  status: () => request<MonitorStatus>("GET", "/api/status"),
  telegram: () => request<TelegramStatus>("GET", "/api/telegram"),
  telegramTest: () => request<{ ok: boolean; error: string | null }>("POST", "/api/telegram/test"),
  sendReport: () => request<{ ok: boolean; error: string | null }>("POST", "/api/report/send"),
  changePassword: (current_password: string, new_password: string) =>
    request<{ ok: boolean; signed_out_others: number }>("POST", "/api/account/password", { current_password, new_password }),
};
