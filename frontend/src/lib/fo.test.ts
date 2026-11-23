import { describe, expect, it } from "vitest";

import { FoContractError, parseFo } from "./fo";

const BODY = {
  days_ok: 2900,
  days_missing: 3,
  days_error: 0,
  oldest: "2015-01-01",
  newest: "2026-11-20",
  rows: 36_000_000,
  days_with_rows: 2900,
  underlyings: 230,
  rows_by_kind: { IDF: 40_000, STO: 20_000_000 },
  table_gb: 3.2,
};

describe("parseFo", () => {
  it("maps the coverage summary", () => {
    expect(parseFo(BODY)).toMatchObject({ daysOk: 2900, rows: 36_000_000, tableGb: 3.2 });
  });

  it("accepts an empty history (nothing loaded yet)", () => {
    const empty = { ...BODY, days_ok: 0, oldest: null, newest: null, rows: 0, rows_by_kind: {} };
    expect(parseFo(empty).oldest).toBeNull();
  });

  it("rejects missing or non-integer counts and bad dates", () => {
    expect(() => parseFo({ ...BODY, rows: 1.5 })).toThrow(FoContractError);
    expect(() => parseFo({ ...BODY, oldest: "2015" })).toThrow(FoContractError);
    expect(() => parseFo({ ...BODY, rows_by_kind: { IDF: -1 } })).toThrow(FoContractError);
    expect(() => parseFo(null)).toThrow(FoContractError);
  });
});
