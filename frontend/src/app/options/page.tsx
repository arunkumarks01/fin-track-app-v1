import type { Metadata } from "next";

import { OptionsWorkspace } from "./options-workspace";

export const metadata: Metadata = { title: "Options · FinTrack v2" };

export default function OptionsPage() {
  return (
    <div className="flex w-full flex-col gap-4 p-4 sm:p-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Options analytics</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Review saved Kite chains, open interest and model sensitivities by expiry.
        </p>
      </header>
      <OptionsWorkspace />
    </div>
  );
}
