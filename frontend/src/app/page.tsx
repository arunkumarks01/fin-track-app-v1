import { HealthPanel } from "./health-panel";
import { IngestPanel } from "./ingest-panel";

export default function Home() {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 px-4 py-12">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">FinTrack v2</h1>
        <span className="rounded border border-amber-500/60 px-2 py-0.5 text-xs font-medium text-amber-300">
          PAPER
        </span>
      </header>
      <p className="text-neutral-400">
        Phase 1 — data foundation. No scores or trades exist yet: nothing is scored until the
        coverage gate passes.
      </p>
      <section aria-label="Backend health" className="rounded-lg border border-neutral-800 p-4">
        <HealthPanel />
      </section>
      <section aria-label="Data foundation" className="rounded-lg border border-neutral-800 p-4">
        <h2 className="mb-3 text-base font-medium">Data foundation</h2>
        <IngestPanel />
      </section>
    </main>
  );
}
