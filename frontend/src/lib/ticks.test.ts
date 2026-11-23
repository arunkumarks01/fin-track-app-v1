import { describe, expect, it } from "vitest";

import { parseTicks, TicksContractError } from "./ticks";

const BODY = {
  capture: {
    status: "running",
    connected: true,
    ticks: 120000,
    rejected: 2,
    dropped: 0,
    unknown_tokens: 1,
    reconnects: 1,
    buffered: 40,
    instruments: 226,
    last_tick_at: "2026-11-23T10:00:00+05:30",
    detail: null,
  },
  runs: [
    {
      session_date: "2026-11-20",
      status: "ok",
      instruments: 226,
      ticks: 5000000,
      rejected: 3,
      reconnects: 2,
      detail: null,
    },
  ],
  hot: { rows_estimate: 25000000, oldest: "2026-11-16T03:30:00+00:00", newest: null },
  exports: {
    verified: 4,
    dropped: 4,
    mismatch: 0,
    error: 0,
    rows: 20000000,
    bytes: 3200000000,
    newest: null,
  },
  chain: {
    snapshots_today: 12,
    latest: [
      {
        underlying: "NIFTY",
        snapshot_ts: "2026-11-23T10:05:00+05:30",
        status: "ok",
        contracts: 124,
        spot_paisa: 2551230,
      },
    ],
  },
};

describe("parseTicks", () => {
  it("maps capture, exports and chain status", () => {
    const t = parseTicks(BODY);
    expect(t.capture).toMatchObject({ status: "running", connected: true, ticks: 120000 });
    expect(t.exports).toMatchObject({ verified: 4, bytes: 3200000000 });
    expect(t.chain.latest[0]).toMatchObject({ underlying: "NIFTY", spotPaisa: 2551230 });
  });

  it("accepts no capture configured (Kite not set up)", () => {
    expect(parseTicks({ ...BODY, capture: null }).capture).toBeNull();
  });

  it("rejects negative or fractional counts and a missing section", () => {
    expect(() => parseTicks({ ...BODY, exports: { ...BODY.exports, mismatch: -1 } })).toThrow(
      TicksContractError,
    );
    expect(() => parseTicks({ ...BODY, runs: [{ ...BODY.runs[0], ticks: 1.5 }] })).toThrow(
      TicksContractError,
    );
    const without: Record<string, unknown> = { ...BODY };
    delete without.chain;
    expect(() => parseTicks(without)).toThrow(TicksContractError);
  });
});
