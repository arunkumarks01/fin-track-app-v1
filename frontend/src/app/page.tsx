import { HealthPanel } from "./health-panel";
import { IngestPanel } from "./ingest-panel";
import { MarketOverview } from "./market-overview";
import { ParticipantsCard } from "./participants-card";

export default function Home() {
  return (
    <div className="flex w-full flex-col gap-6 p-4 sm:p-6">
      <div>
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <p className="text-muted-foreground">
          Research and paper trading workspace. Data coverage and trading features are still being
          verified.
        </p>
      </div>
      <MarketOverview />
      <ParticipantsCard />
      <div className="grid gap-4 lg:grid-cols-2">
        <section
          aria-label="Backend health"
          className="rounded-xl border bg-card p-4 text-card-foreground shadow-xs"
        >
          <h2 className="mb-3 text-base font-medium">System</h2>
          <HealthPanel />
        </section>
        <section
          aria-label="Data foundation"
          className="rounded-xl border bg-card p-4 text-card-foreground shadow-xs"
        >
          <h2 className="mb-3 text-base font-medium">Data foundation</h2>
          <IngestPanel />
        </section>
      </div>
    </div>
  );
}
