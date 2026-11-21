import { describe, expect, it } from "vitest";

import { initialTheme, storedTheme, THEME_STORAGE_KEY } from "./theme";

const store = (v: string | null) => ({
  getItem: (k: string) => (k === THEME_STORAGE_KEY ? v : null),
});

describe("theme", () => {
  it("uses the saved choice first", () => {
    expect(initialTheme(store("light"), false)).toBe("light");
    expect(initialTheme(store("dark"), true)).toBe("dark");
  });

  it("falls back to the system preference, and ignores junk", () => {
    expect(initialTheme(store(null), true)).toBe("light");
    expect(initialTheme(store("purple"), false)).toBe("dark");
    expect(storedTheme(null)).toBeNull();
  });

  it("survives storage that throws (private mode)", () => {
    const broken = {
      getItem: () => {
        throw new Error("denied");
      },
    };
    expect(initialTheme(broken, true)).toBe("light");
  });
});
