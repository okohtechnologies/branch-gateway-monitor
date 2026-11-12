import { describe, expect, it } from "vitest";
import {
  chartSummary,
  countByStatus,
  filterBranches,
  filterIncidents,
  isStale,
  outageSegments,
  parseFieldErrors,
  parseFilter,
  sortBranches,
  sortIncidents,
  summarizeIncidents,
  toChartPoints,
  validateBranchInput,
  type ChartPoint,
  type IncidentFilters,
} from "./logic";
import { fmtAge } from "./format";
import type { Branch, BranchInput, Incident, Point, Status } from "./types";

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
