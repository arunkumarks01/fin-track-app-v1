import type { Metadata } from "next";

import { PaperWorkspace } from "./workspace";

export const metadata: Metadata = { title: "Paper Trading · FinTrack v2" };

export default async function PaperPage({
  searchParams,
}: {
  searchParams: Promise<{ symbol?: string }>;
}) {
  const { symbol } = await searchParams;
  return (
    <div className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Paper trading</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Practice on archived NSE equity sessions with simulated fills.
        </p>
      </header>
      <PaperWorkspace symbol={symbol} />
    </div>
  );
}
