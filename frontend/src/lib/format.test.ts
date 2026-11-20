import { describe, expect, it } from "vitest";

import { formatDuration, formatInt, formatIstDateTime, formatIstTime, formatPct } from "./format";

describe("format", () => {
  it("groups integers the Indian way", () => {
    expect(formatInt(1234567)).toBe("12,34,567");
  });

  it("formats percentages and durations", () => {
    expect(formatPct(95.24)).toBe("95.2%");
    expect(formatDuration(65_000)).toBe("1m 05s");
    expect(formatDuration(9_400)).toBe("9s");
    expect(formatDuration(-5)).toBe("0s");
  });

  it("shows times in IST", () => {
    expect(formatIstTime("2026-11-19T12:48:47+00:00")).toBe("18:18:47 IST");
    expect(formatIstDateTime("2026-11-20T10:45:00+00:00")).toBe("20 Nov, 16:15 IST");
  });
});
