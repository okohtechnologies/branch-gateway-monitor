import { describe, expect, it } from "vitest";
import {
  changedKeys,
  chartSummary,
  checkPasswordChange,
  countByStatus,
  detectionSeconds,
  filterBranches,
  filterIncidents,
  humanSeconds,
  isChanged,
  isStale,
  monitoringExplanation,
  outageSegments,
  parseFieldErrors,
  parseFilter,
  parseSetting,
  retentionNote,
  settingError,
  settingsErrors,
  settingsToSend,
  sortBranches,
  sortIncidents,
  sparkGeometry,
  summarizeIncidents,
  toChartPoints,
  toDraft,
  validateBranchInput,
  worstProbeSeconds,
  type ChartPoint,
  type IncidentFilters,
} from "./logic";
import { fmtAge } from "./format";
import type { Branch, BranchInput, Incident, Point, SettingLimit, Status } from "./types";

const branch = (name: string, status: Status, extra: Partial<Branch> = {}): Branch => ({
  id: Math.abs(name.split("").reduce((a, c) => a * 31 + c.charCodeAt(0), 7)) % 10000,
  name,
  host: "10.0.0.1",
  location: "",
  probe_type: "icmp",
  port: null,
  latency_warn_ms: 200,
  loss_warn_pct: 20,
  enabled: status !== "DISABLED",
  status,
  ...extra,
});

describe("sortBranches", () => {
  it("puts problems first, then pending, up, disabled, then by name", () => {
    const sorted = sortBranches([
      branch("Zulu", "UP"),
      branch("Alpha", "UP"),
      branch("Off", "DISABLED"),
      branch("Wait", "PENDING"),
      branch("Slow", "DEGRADED"),
      branch("Dead", "DOWN"),
    ]);
    expect(sorted.map((b) => b.name)).toEqual(["Dead", "Slow", "Wait", "Alpha", "Zulu", "Off"]);
  });

  it("orders names naturally (Branch 2 before Branch 10) and does not mutate the input", () => {
    const input = [branch("Branch 10", "UP"), branch("Branch 2", "UP")];
    expect(sortBranches(input).map((b) => b.name)).toEqual(["Branch 2", "Branch 10"]);
    expect(input[0].name).toBe("Branch 10");
  });
});

describe("filterBranches", () => {
  const list = [
    branch("Head Office", "UP", { host: "10.20.0.1", location: "Lagos" }),
    branch("North Depot", "DEGRADED", { host: "10.20.1.1" }),
    branch("Riverside", "DOWN", { host: "gw.river.example" }),
  ];
  it("filters by status", () => {
    expect(filterBranches(list, "", "down").map((b) => b.name)).toEqual(["Riverside"]);
    expect(filterBranches(list, "", "all")).toHaveLength(3);
  });
  it("searches name, address and location, case-insensitively", () => {
    expect(filterBranches(list, "head", "all")).toHaveLength(1);
    expect(filterBranches(list, "10.20.1", "all").map((b) => b.name)).toEqual(["North Depot"]);
    expect(filterBranches(list, "LAGOS", "all")).toHaveLength(1);
    expect(filterBranches(list, "  river  ", "all")).toHaveLength(1);
  });
  it("combines status and search", () => {
    expect(filterBranches(list, "depot", "down")).toEqual([]);
  });
});

describe("parseFilter / countByStatus", () => {
  it("accepts only known filters", () => {
    expect(parseFilter("down")).toBe("down");
    expect(parseFilter("bogus")).toBe("all");
    expect(parseFilter(null)).toBe("all");
  });
  it("counts every status", () => {
    const c = countByStatus([branch("a", "UP"), branch("b", "UP"), branch("c", "DOWN")]);
    expect(c).toEqual({ UP: 2, DEGRADED: 0, DOWN: 1, PENDING: 0, DISABLED: 0 });
  });
});

describe("isStale", () => {
  const now = 1_000_000;
  it("is stale when disconnected", () => expect(isStale(now, now, 5, false)).toBe(true));
  it("is fresh while cycles keep arriving", () => expect(isStale(now, now - 10_000, 5, true)).toBe(false));
  it("is stale after about three missed intervals", () => {
    expect(isStale(now, now - 25_000, 5, true)).toBe(true); // limit is 3*5+5 = 20s
    expect(isStale(now, now - 19_000, 5, true)).toBe(false);
  });
  it("assumes a 30s interval until one is observed", () => {
    expect(isStale(now, now - 60_000, null, true)).toBe(false);
    expect(isStale(now, now - 100_000, null, true)).toBe(true);
  });
  it("is not stale before the first cycle has arrived", () => expect(isStale(now, null, null, true)).toBe(false));
});

describe("outageSegments", () => {
  const pt = (t: number, down: boolean): ChartPoint => ({ t, latency: down ? null : 10, loss: down ? 100 : 0, down });
  it("returns nothing for healthy or empty data", () => {
    expect(outageSegments([])).toEqual([]);
    expect(outageSegments([pt(0, false), pt(30_000, false)])).toEqual([]);
  });
  it("pads a single failed sample by half a sample so it is visible", () => {
    const segs = outageSegments([pt(0, false), pt(30_000, true), pt(60_000, false)]);
    expect(segs).toEqual([[15_000, 45_000]]);
  });
  it("merges consecutive down samples into one band", () => {
    const segs = outageSegments([pt(0, false), pt(30_000, true), pt(60_000, true), pt(90_000, true), pt(120_000, false)]);
    expect(segs).toEqual([[15_000, 105_000]]);
  });
  it("finds separate outages and one still ongoing at the end", () => {
    const segs = outageSegments([pt(0, true), pt(30_000, false), pt(60_000, true), pt(90_000, true)]);
    expect(segs).toHaveLength(2);
    expect(segs[1][1]).toBe(105_000);
  });
});

describe("chartSummary / toChartPoints", () => {
  const points: Point[] = [
    { ts: "2026-01-01T00:00:00Z", latency_ms: 10, loss_pct: 0, status: "UP" },
    { ts: "2026-01-01T00:00:30Z", latency_ms: 30, loss_pct: 0, status: "UP" },
    { ts: "2026-01-01T00:01:00Z", latency_ms: null, loss_pct: 100, status: "DOWN" },
  ];
  it("summarises latency ignoring no-reply samples, and counts outages", () => {
    const s = chartSummary(toChartPoints(points));
    expect(s).toEqual({ samples: 3, avgLatency: 20, maxLatency: 30, outages: 1 });
  });
  it("handles a range with no replies at all", () => {
    const s = chartSummary(toChartPoints([points[2]]));
    expect(s.avgLatency).toBeNull();
    expect(s.maxLatency).toBeNull();
    expect(s.outages).toBe(1);
  });
});

describe("parseFieldErrors", () => {
  it("splits server validation messages by field", () => {
    expect(parseFieldErrors("host: invalid IP address or hostname; port: port is required for tcp probes")).toEqual({
      host: "invalid IP address or hostname",
      port: "port is required for tcp probes",
    });
  });
  it("keeps unmatched text as a general error", () => {
    expect(parseFieldErrors("Request failed (500)")).toEqual({ _: "Request failed (500)" });
  });
  it("copes with an empty message", () => expect(parseFieldErrors("")).toEqual({}));
});

describe("validateBranchInput", () => {
  const ok: BranchInput = { name: "HQ", host: "10.0.0.1", location: "", probe_type: "icmp", port: null, latency_warn_ms: 200, loss_warn_pct: 20, enabled: true };
  it("accepts a valid branch, including hostnames and IPv6", () => {
    expect(validateBranchInput(ok)).toEqual({});
    expect(validateBranchInput({ ...ok, host: "gateway.example.com" })).toEqual({});
    expect(validateBranchInput({ ...ok, host: "localhost" })).toEqual({});
    expect(validateBranchInput({ ...ok, host: "2001:db8::1" })).toEqual({});
  });
  it("requires a name and an address", () => {
    expect(Object.keys(validateBranchInput({ ...ok, name: "  ", host: "" }))).toEqual(["name", "host"]);
  });
  it("rejects malformed addresses", () => {
    for (const host of ["999.1.1.1", "10.0.0", "bad host", "-bad.example.com", "a..b"]) {
      expect(validateBranchInput({ ...ok, host }).host, host).toBeTruthy();
    }
  });
  it("needs a valid port only for TCP", () => {
    expect(validateBranchInput({ ...ok, probe_type: "tcp", port: null }).port).toBeTruthy();
    expect(validateBranchInput({ ...ok, probe_type: "tcp", port: 70000 }).port).toBeTruthy();
    expect(validateBranchInput({ ...ok, probe_type: "tcp", port: 443 }).port).toBeUndefined();
    expect(validateBranchInput({ ...ok, probe_type: "icmp", port: null }).port).toBeUndefined();
  });
  it("checks thresholds", () => {
    expect(validateBranchInput({ ...ok, latency_warn_ms: 0 }).latency_warn_ms).toBeTruthy();
    expect(validateBranchInput({ ...ok, loss_warn_pct: 101 }).loss_warn_pct).toBeTruthy();
    expect(validateBranchInput({ ...ok, loss_warn_pct: 0 }).loss_warn_pct).toBeUndefined();
  });
  it("limits the name to 80 characters", () => {
    expect(validateBranchInput({ ...ok, name: "x".repeat(81) }).name).toBeTruthy();
  });
});

describe("incidents", () => {
  const inc = (id: number, started: string, ended: string | null, severity: "DOWN" | "DEGRADED" = "DOWN", branch_id = 1): Incident => ({
    id, branch_id, branch_name: `B${branch_id}`, severity, started_at: started, ended_at: ended, acknowledged_by: null, acknowledged_at: null,
  });
  const now = Date.parse("2026-01-31T12:00:00Z");
  const list = [
    inc(1, "2026-01-30T11:00:00Z", "2026-01-30T11:10:00Z"), // 25h ago
    inc(2, "2026-01-31T11:00:00Z", null, "DEGRADED", 2),
    inc(3, "2025-12-01T00:00:00Z", "2025-12-01T01:00:00Z"),
    inc(4, "2026-01-31T10:00:00Z", null, "DOWN", 3),
  ];
  const base: IncidentFilters = { openOnly: false, branchId: null, severity: "all", range: "all" };

  it("pins open incidents first, newest first within each group", () => {
    expect(sortIncidents(list).map((i) => i.id)).toEqual([2, 4, 1, 3]);
  });
  it("filters by open, branch, severity and period", () => {
    expect(filterIncidents(list, { ...base, openOnly: true }, now).map((i) => i.id)).toEqual([2, 4]);
    expect(filterIncidents(list, { ...base, branchId: 2 }, now).map((i) => i.id)).toEqual([2]);
    expect(filterIncidents(list, { ...base, severity: "DEGRADED" }, now).map((i) => i.id)).toEqual([2]);
    expect(filterIncidents(list, { ...base, range: "7d" }, now).map((i) => i.id)).toEqual([1, 2, 4]);
    expect(filterIncidents(list, { ...base, range: "24h" }, now).map((i) => i.id)).toEqual([2, 4]);
  });
  it("counts an incident exactly on the period boundary as inside it", () => {
    const edge = [inc(9, "2026-01-30T12:00:00Z", "2026-01-30T12:05:00Z")]; // exactly 24h before `now`
    expect(filterIncidents(edge, { ...base, range: "24h" }, now)).toHaveLength(1);
    expect(filterIncidents(edge, { ...base, range: "24h" }, now + 1)).toHaveLength(0);
  });
  it("summarises totals, open count and average resolved time", () => {
    const s = summarizeIncidents(list);
    expect(s.total).toBe(4);
    expect(s.open).toBe(2);
    expect(s.avgResolvedMs).toBe((10 * 60_000 + 60 * 60_000) / 2);
  });
  it("has no average when nothing is resolved", () => {
    expect(summarizeIncidents([list[1]]).avgResolvedMs).toBeNull();
  });
});

describe("fmtAge", () => {
  it("phrases freshness", () => {
    expect(fmtAge(0)).toBe("just now");
    expect(fmtAge(12)).toBe("12s ago");
    expect(fmtAge(125)).toBe("2m ago");
    expect(fmtAge(7300)).toBe("2h ago");
  });
});

describe("sparkGeometry", () => {
  const series = (...vs: Array<number | null>) => vs.map((v) => ({ v, d: false }));
  const ys = (path: string) => [...path.matchAll(/[ML][\d.]+ ([\d.]+)/g)].map((m) => Number(m[1]));

  it("needs at least three points and some data to draw a trend", () => {
    expect(sparkGeometry(undefined, 200)).toBeNull();
    expect(sparkGeometry(series(10, 12), 200)).toBeNull();
    expect(sparkGeometry(series(null, null, null), 200)).toBeNull();
  });

  it("makes a calm branch and a struggling one look different (the whole point)", () => {
    const calm = sparkGeometry(series(4, 5, 4, 5, 4), 200)!;
    const struggling = sparkGeometry(series(180, 190, 185, 195, 190), 200)!;
    const calmY = Math.min(...ys(calm.path));
    const strugglingY = Math.min(...ys(struggling.path));
    expect(strugglingY).toBeLessThan(calmY - 10); // smaller y = higher on screen
  });

  it("scales two branches with the SAME threshold identically, whatever their own maximum", () => {
    const a = sparkGeometry(series(20, 40, 20), 200)!;
    const b = sparkGeometry(series(20, 40, 20, 20, 20), 200)!;
    expect(Math.min(...ys(a.path))).toBe(Math.min(...ys(b.path)));
  });

  it("a branch over its threshold reaches the top of the box and the threshold line drops below it", () => {
    const g = sparkGeometry(series(100, 300, 100), 200)!;
    expect(Math.min(...ys(g.path))).toBe(4); // the 300 ms peak touches the top
    expect(g.thresholdY).not.toBeNull();
    expect(g.thresholdY as number).toBeGreaterThan(4);
  });

  it("only shows the threshold once the data comes within half of it", () => {
    expect(sparkGeometry(series(4, 5, 4), 200)!.thresholdY).toBeNull();
    expect(sparkGeometry(series(100, 120, 110), 200)!.thresholdY).not.toBeNull();
  });

  it("lifts the pen where there was no reply, and marks DOWN buckets inside the box", () => {
    const pts = [{ v: 10, d: false }, { v: 12, d: false }, { v: null, d: true }, { v: null, d: true }, { v: 11, d: false }];
    const g = sparkGeometry(pts, 200, 96, 28)!;
    expect((g.path.match(/M/g) ?? []).length).toBe(2); // two separate strokes
    expect(g.marks).toHaveLength(2);
    for (const m of g.marks) {
      expect(m.x).toBeGreaterThanOrEqual(0);
      expect(m.x + m.w).toBeLessThanOrEqual(96);
    }
  });

  it("copes with an unknown threshold by anchoring to the data", () => {
    const g = sparkGeometry(series(10, 20, 10), null)!;
    expect(Math.min(...ys(g.path))).toBe(4);
    expect(g.thresholdY).toBeNull();
  });
});

// ---- settings screen ----

describe("settings form", () => {
  const limits: Record<string, SettingLimit> = {
    probe_interval_s: { kind: "int", min: 2, max: 3600 },
    probe_timeout_s: { kind: "float", min: 0.5, max: 10 },
    canary_enabled: { kind: "bool" },
    canary_type: { kind: "choice", choices: ["tcp", "icmp"] },
    canary_host: { kind: "host" },
  };
  const saved = { probe_interval_s: 30, probe_timeout_s: 2, canary_enabled: true, canary_type: "tcp", canary_host: "1.1.1.1" };
  const defaults = { ...saved };
  const keys = Object.keys(saved);

  it("turns saved values into editable text, keeping switches as booleans", () => {
    expect(toDraft(saved, keys)).toEqual({ probe_interval_s: "30", probe_timeout_s: "2", canary_enabled: true, canary_type: "tcp", canary_host: "1.1.1.1" });
  });

  it("validates numbers: required, numeric, whole, in range", () => {
    expect(settingError("", limits.probe_interval_s)).toBe("Enter a number.");
    expect(settingError("abc", limits.probe_interval_s)).toBe("Enter a number.");
    expect(settingError("2.5", limits.probe_interval_s)).toBe("Use a whole number.");
    expect(settingError("1", limits.probe_interval_s)).toBe("Use a value between 2 and 3600.");
    expect(settingError("3601", limits.probe_interval_s)).toBe("Use a value between 2 and 3600.");
    expect(settingError("5", limits.probe_interval_s)).toBeNull();
    expect(settingError("0.5", limits.probe_timeout_s)).toBeNull();
    expect(settingError("0.4", limits.probe_timeout_s)).toBe("Use a value between 0.5 and 10.");
  });

  it("validates the canary host and choice like the server does", () => {
    expect(settingError("", limits.canary_host)).toBe("Enter an IP address or hostname.");
    expect(settingError("999.1.1.1", limits.canary_host)).toBe("That is not a valid IP address.");
    expect(settingError("bad host", limits.canary_host)).toBe("Enter a valid IP address or hostname.");
    expect(settingError("example.com", limits.canary_host)).toBeNull();
    expect(settingError("udp", limits.canary_type)).toBe("Choose one of the options.");
    expect(settingError(true, limits.canary_enabled)).toBeNull();
  });

  it("collects every problem at once, keyed by field", () => {
    const d = { ...toDraft(saved, keys), probe_interval_s: "1", canary_host: "" };
    expect(Object.keys(settingsErrors(d, keys, limits))).toEqual(["probe_interval_s", "canary_host"]);
  });

  it("compares by value, so 30.0 is not a change but 31 is", () => {
    expect(isChanged("30.0", 30, limits.probe_interval_s)).toBe(false);
    expect(isChanged("31", 30, limits.probe_interval_s)).toBe(true);
    expect(isChanged(false, true, limits.canary_enabled)).toBe(true);
    expect(isChanged("  1.1.1.1 ", "1.1.1.1", limits.canary_host)).toBe(false);
    expect(isChanged("oops", 30, limits.probe_interval_s)).toBe(true); // an invalid entry still counts as an edit
  });

  it("lists exactly the fields that changed", () => {
    const d = { ...toDraft(saved, keys), probe_interval_s: "5", canary_enabled: false };
    expect(changedKeys(d, saved, keys, limits)).toEqual(["probe_interval_s", "canary_enabled"]);
    expect(changedKeys(toDraft(saved, keys), saved, keys, limits)).toEqual([]);
  });

  it("sends real types, and null (a reset) when a field is put back to its default", () => {
    const current = { ...saved, probe_interval_s: 5 }; // currently overridden to 5
    const d = { ...toDraft(current, keys), probe_interval_s: "30", probe_timeout_s: "1.5" };
    expect(settingsToSend(d, current, defaults, keys, limits)).toEqual({ probe_interval_s: null, probe_timeout_s: 1.5 });
  });

  it("parses each kind into its real type", () => {
    expect(parseSetting("5", limits.probe_interval_s)).toBe(5);
    expect(parseSetting("1.5", limits.probe_timeout_s)).toBe(1.5);
    expect(parseSetting(true, limits.canary_enabled)).toBe(true);
    expect(parseSetting(" icmp ", limits.canary_type)).toBe("icmp");
  });
});

describe("monitoring explanations", () => {
  const draft = { probe_interval_s: "5", confirm: "4", probe_count: "3", probe_timeout_s: "1" };
  it("works out how long until an outage is alerted", () => {
    expect(detectionSeconds(4, 5)).toBe(20);
    expect(monitoringExplanation(draft)!.detect).toContain("about 20 seconds");
    expect(monitoringExplanation({ ...draft, confirm: "3", probe_interval_s: "30" })!.detect).toContain("about 90 seconds");
  });
  it("warns when probing an unreachable branch outlasts the interval", () => {
    expect(worstProbeSeconds(3, 1)).toBeCloseTo(3.6);
    expect(monitoringExplanation(draft)!.warning).toBeNull(); // 3.6s fits in 5s
    const w = monitoringExplanation({ ...draft, probe_count: "4", probe_timeout_s: "2" })!.warning;
    expect(w).toContain("8.8s");
    expect(w).toContain("5s interval");
  });
  it("says nothing while any number is missing or invalid", () => {
    expect(monitoringExplanation({ ...draft, confirm: "" })).toBeNull();
    expect(monitoringExplanation({ ...draft, probe_interval_s: "abc" })).toBeNull();
  });
  it("phrases durations sensibly", () => {
    expect(humanSeconds(20)).toBe("20 seconds");
    expect(humanSeconds(180)).toBe("3 minutes");
    expect(humanSeconds(7200)).toBe("2.0 hours");
  });
  it("explains what a retention period does to long-term uptime", () => {
    expect(retentionNote(0)).toContain("forever");
    expect(retentionNote(14)).toContain("last 14 days");
    expect(retentionNote(1)).toContain("last 1 day,");
    expect(retentionNote(30)).toBeNull();
    expect(retentionNote(90)).toBeNull();
  });
});

describe("password change form", () => {
  it("accepts a good change", () => {
    expect(checkPasswordChange("old-password", "a-much-longer-new-one", "a-much-longer-new-one")).toEqual({});
  });
  it("requires the current password", () => {
    expect(checkPasswordChange("", "a-much-longer-new-one", "a-much-longer-new-one").current).toBeTruthy();
  });
  it("enforces length, difference and a matching repeat", () => {
    expect(checkPasswordChange("old", "short", "short").next).toBe("Use at least 10 characters.");
    expect(checkPasswordChange("same-password-1", "same-password-1", "same-password-1").next).toContain("different");
    expect(checkPasswordChange("old", "a-much-longer-new-one", "a-much-longer-new-on").repeat).toContain("do not match");
    expect(checkPasswordChange("old", "x".repeat(257), "x".repeat(257)).next).toBe("Use 256 characters or fewer.");
  });
});
