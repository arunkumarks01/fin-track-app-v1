import type { Metadata } from "next";

import { CandidateWorkspace } from "./workspace";

export const metadata: Metadata = { title: "Candidates · FinTrack v2" };

export default function CandidatesPage() {
  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-5 p-4 sm:p-6">
      <h1 className="text-2xl font-semibold tracking-tight">Research candidates</h1>
      <CandidateWorkspace />
    </div>
  );
}
