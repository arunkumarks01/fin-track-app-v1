import type { Metadata } from "next";

import { ChartWorkspace } from "./chart-workspace";

export const metadata: Metadata = { title: "Charts · FinTrack v2" };

/** Exploratory charting over FinTrack's own stored candles (OpenAlgo Charts widget). */
export default function ChartsPage() {
  return (
    <main className="flex flex-col gap-3 px-4 py-4">
      <div className="rounded-md border border-amber-700/60 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
        <strong>Visual exploration only.</strong> Studies and drawings run in your browser and are
        not FinTrack&apos;s analysis engine — nothing scores, signals or trades from them. Candles
        are FinTrack&apos;s stored data. <em>Raw</em> prices are as traded (NSE official daily, the
        measured 1-minute view) and exist for 1m, D and W only.
      </div>
      <ChartWorkspace />
    </main>
  );
}
