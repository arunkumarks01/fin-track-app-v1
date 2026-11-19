import { describe, expect, it } from "vitest";

import { requireEnv, requireHttpUrl } from "./env";

describe("requireEnv", () => {
  it("returns a set value", () => {
    expect(requireEnv("A", { A: " x " })).toBe("x");
  });

  it.each([{}, { A: "" }, { A: "   " }])("fails closed when missing: %o", (env) => {
    expect(() => requireEnv("A", env)).toThrow("Missing required environment variable A");
  });
});

describe("requireHttpUrl", () => {
  it("accepts http(s) and strips trailing slashes", () => {
    expect(requireHttpUrl("U", { U: "http://127.0.0.1:8000/" })).toBe("http://127.0.0.1:8000");
    expect(requireHttpUrl("U", { U: "https://api.example.test" })).toBe("https://api.example.test");
  });

  it.each(["not a url", "ftp://x.test", "javascript:alert(1)"])("rejects %s", (value) => {
    expect(() => requireHttpUrl("U", { U: value })).toThrow(/U /);
  });
});
