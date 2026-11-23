import type { Metadata } from "next";
import type { ReactNode } from "react";

import { HealthPanel } from "../health-panel";
import { IngestPanel } from "../ingest-panel";
import { AdjustmentsPanel } from "./adjustments-panel";
import { IntradayPanel } from "./intraday-panel";
import { FoPanel } from "./fo-panel";
import { JobsPanel, QualityPanel } from "./jobs-panel";
import { KitePanel } from "./kite-panel";
import { ReferencePanel } from "./reference-panel";
import { TicksPanel } from "./ticks-panel";

export const metadata: Metadata = { title: "Admin · FinTrack v2" };

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section
      aria-label={title}
      className="rounded-xl border bg-card p-4 text-card-foreground shadow-xs"
    >
      <h2 className="mb-3 text-base font-medium">{title}</h2>
      {children}
    </section>
  );
}

/** Admin/Health monitors and controls the data pipeline. */
export default function AdminPage() {
  return (
    <div className="flex w-full flex-col gap-6 p-4 sm:p-6">
      <div>
        <h1 className="text-2xl font-semibold">Admin · Health</h1>
        <p className="text-muted-foreground">
          Monitor ingestion, schedules and data quality. Job controls require the Admin API token.
        </p>
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        <Section title="System">
          <HealthPanel />
        </Section>
        <Section title="Kite session">
          <KitePanel />
        </Section>
      </div>
      <Section title="Jobs">
        <JobsPanel />
      </Section>
      <div className="grid gap-6 md:grid-cols-2">
        <Section title="Daily bars">
          <IngestPanel />
        </Section>
        <Section title="1-minute bars">
          <IntradayPanel />
        </Section>
      </div>
      <Section title="Corporate actions & adjustments (measured against NSE official)">
        <AdjustmentsPanel />
      </Section>
      <Section title="Reference data">
        <ReferencePanel />
      </Section>
      <Section title="Futures & options history (slice 1f)">
        <FoPanel />
      </Section>
      <Section title="Live ticks and option chains (slice 1d)">
        <TicksPanel />
      </Section>
      <Section title="Data quality">
        <QualityPanel />
      </Section>
    </div>
  );
}
