import { describe, expect, it } from "vitest";
import { duration, fmtLoss, fmtMs, fmtUptime, timeAgo } from "./format";
import { errorMessage } from "./api";

describe("format", () => {
  it("formats latency", () => {
    expect(fmtMs(null)).toBe("—");
    expect(fmtMs(3.04)).toBe("3.0 ms");
    expect(fmtMs(124.6)).toBe("125 ms");
  });

  it("formats loss", () => {
    expect(fmtLoss(undefined)).toBe("—");
    expect(fmtLoss(0)).toBe("0%");
    expect(fmtLoss(12.5)).toBe("12.5%");
  });

  it("never rounds uptime up to 100% while checks failed", () => {
    expect(fmtUptime(100)).toBe("100%");
    expect(fmtUptime(99.999)).toBe("99.99%");
    expect(fmtUptime(50)).toBe("50.00%");
    expect(fmtUptime(null)).toBe("—");
  });

  it("formats relative time", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(timeAgo(null, now)).toBe("never");
    expect(timeAgo("2026-01-01T11:59:58Z", now)).toBe("just now");
    expect(timeAgo("2026-01-01T11:59:20Z", now)).toBe("40s ago");
    expect(timeAgo("2026-01-01T11:30:00Z", now)).toBe("30m ago");
    expect(timeAgo("2025-12-30T12:00:00Z", now)).toBe("2d ago");
  });

  it("formats durations, open or closed", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(duration("2026-01-01T11:59:30Z", null, now)).toBe("30s");
    expect(duration("2026-01-01T10:30:00Z", null, now)).toBe("1h 30m");
    expect(duration("2026-01-01T10:00:00Z", "2026-01-01T10:05:00Z", now)).toBe("5m");
  });
});

describe("errorMessage", () => {
  it("uses a plain string detail", () => {
    expect(errorMessage({ detail: "A branch with this name already exists" }, "x")).toBe(
      "A branch with this name already exists",
    );
  });

  it("flattens FastAPI validation errors", () => {
    const body = {
      detail: [{ loc: ["body", "host"], msg: "Value error, invalid IP address or hostname" }],
    };
    expect(errorMessage(body, "x")).toBe("host: invalid IP address or hostname");
  });

  it("falls back when there is no detail", () => {
    expect(errorMessage(null, "fallback")).toBe("fallback");
    expect(errorMessage({}, "fallback")).toBe("fallback");
  });
});
