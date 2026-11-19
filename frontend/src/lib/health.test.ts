import { describe, expect, it } from "vitest";

import { HealthContractError, fetchHealth, parseHealth } from "./health";

const OK = { status: "ok", paper_mode: true, db: "ok", scheduler: "running", version: "0.0.1" };

function respond(status: number, body: unknown): typeof fetch {
  return async () => new Response(JSON.stringify(body), { status });
}

describe("parseHealth", () => {
  it("maps the backend contract", () => {
    expect(parseHealth(OK)).toEqual({
      status: "ok",
      paperMode: true,
      db: "ok",
      scheduler: "running",
      version: "0.0.1",
    });
  });

  it.each([
    null,
    [],
    { ...OK, status: "fine" },
    { ...OK, paper_mode: false },
    { ...OK, db: undefined },
    { ...OK, scheduler: "paused" },
    { ...OK, version: 1 },
  ])("rejects an unexpected shape: %o", (body) => {
    expect(() => parseHealth(body)).toThrow(HealthContractError);
  });
});

describe("fetchHealth", () => {
  it("surfaces a degraded 503 honestly instead of throwing it away", async () => {
    const degraded = { ...OK, status: "degraded", db: "error" };
    await expect(fetchHealth(respond(503, degraded))).resolves.toMatchObject({
      status: "degraded",
      db: "error",
    });
  });

  it("fails on other HTTP errors", async () => {
    await expect(fetchHealth(respond(500, {}))).rejects.toThrow("HTTP 500");
  });
});
