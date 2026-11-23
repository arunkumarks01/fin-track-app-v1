import type { Metadata } from "next";

import { ChartWorkspace } from "./chart-workspace";

export const metadata: Metadata = { title: "Charts · FinTrack v2" };

/** Exploratory charting over FinTrack's own stored candles (OpenAlgo Charts widget). */
export default function ChartsPage() {
  return (
    <div className="flex w-full flex-col gap-3 p-4 sm:p-6">
      <p className="text-sm text-muted-foreground">
        FinTrack indicators use finalized adjusted candles from the server. Chart studies and
        drawings are for visual exploration.
      </p>
      <ChartWorkspace />
    </div>
  );
}
