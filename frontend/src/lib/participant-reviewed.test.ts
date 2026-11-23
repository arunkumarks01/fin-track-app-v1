import { describe, expect, it } from "vitest";
import { parseReviewed } from "./participant-reviewed";

const sample = {
  scope: "labelled_research_only",
  gaps: [
    { dataset: "participant_oi", candidate_date: "2021-06-08", reason: "source_date_unverified" },
  ],
  files: [
    {
      dataset: "participant_vol",
      date: "2015-01-05",
      quality: "component_totals_disagree",
      warning: "Published totals disagree",
      source: "NSE",
      source_url: "https://nsearchives.nseindia.com/content/nsccl/test.csv",
      source_sha256: "abc",
      tolerance_contracts: 2,
      source_total_differences: { "Client.long": "-0.3333" },
      participants: [
        {
          participant: "Client",
          components: { fut_idx_long: "290814.3333" },
          derived_totals: { long: "290814.3333", short: "0" },
          reported_totals: { long: "290815", short: "0" },
        },
      ],
    },
  ],
};

describe("reviewed participant values", () => {
  it("preserves exact values, source totals, quality and gaps", () => {
    const result = parseReviewed(sample);
    expect(result.files[0]?.participants[0]?.components.fut_idx_long).toBe("290814.3333");
    expect(result.files[0]?.participants[0]?.reported_totals.long).toBe("290815");
    expect(result.files[0]?.quality).toBe("component_totals_disagree");
    expect(result.files[0]?.source_total_differences["Client.long"]).toBe("-0.3333");
    expect(result.gaps[0]?.reason).toBe("source_date_unverified");
  });
  it("rejects unsafe source links and malformed source numbers", () => {
    expect(() =>
      parseReviewed({
        ...sample,
        files: [{ ...sample.files[0], source_url: "javascript:alert(1)" }],
      }),
    ).toThrow();
    expect(() =>
      parseReviewed({
        ...sample,
        files: [
          {
            ...sample.files[0],
            participants: [
              { ...sample.files[0]?.participants[0], components: { fut_idx_long: "NaN" } },
            ],
          },
        ],
      }),
    ).toThrow();
  });
});
