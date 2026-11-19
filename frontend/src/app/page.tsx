import { HealthPanel } from "./health-panel";

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
        Phase 0 scaffold. No market data, scores or trades exist yet — by design.
      </p>
      <section aria-label="Backend health" className="rounded-lg border border-neutral-800 p-4">
        <HealthPanel />
      </section>
    </main>
  );
}
