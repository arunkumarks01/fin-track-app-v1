import type { Metadata } from "next";
import { Suspense } from "react";

import { StockView } from "./stock-view";

export const metadata: Metadata = { title: "Stocks · FinTrack v2" };

/** One stock, everything we hold about it (read-only): names, NSE actions vs Kite, data health. */
export default function StocksPage() {
  return (
    <div className="flex w-full flex-col gap-4 p-4 sm:p-6">
      <div>
        <h1 className="text-2xl font-semibold">Stocks</h1>
        <p className="text-muted-foreground">
          Search a stock in the top bar to see what FinTrack holds for it. Read-only: nothing here
          changes data.
        </p>
      </div>
      <Suspense fallback={<p className="text-muted-foreground">Loading…</p>}>
        <StockView />
      </Suspense>
    </div>
  );
}
