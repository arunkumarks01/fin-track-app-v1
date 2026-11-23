import type { Metadata } from "next";
import { ReviewWorkspace } from "./workspace";

export const metadata: Metadata = { title: "Participant data review · FinTrack" };

export default function Page() {
  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-5 p-4 sm:p-6">
      <header>
        <h1 className="text-2xl font-semibold">NSE participant data review</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Exact source values with their data-quality context.
        </p>
      </header>
      <ReviewWorkspace />
    </main>
  );
}
