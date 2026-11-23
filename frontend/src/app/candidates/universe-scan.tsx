"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { formatIstDateTime } from "@/lib/format";
import { type CandidateMode } from "@/lib/scoring";

type SavedScan = {
  asOf: string;
  evaluated: number;
  unavailable: string[];
  matches: { symbol: string; strategies: string[] }[];
};

function parseHistory(value: unknown): SavedScan[] {
  if (
    !value ||
    typeof value !== "object" ||
    !("history" in value) ||
    !Array.isArray(value.history)
  ) {
    throw new Error("The backend does not yet support saved scan history.");
  }
  return value.history.map((item: unknown) => {
    if (!item || typeof item !== "object") throw new Error("Invalid scan report.");
    const report = item as Record<string, unknown>;
    if (
      typeof report.as_of !== "string" ||
      !Number.isFinite(Date.parse(report.as_of)) ||
      typeof report.symbols_evaluated !== "number" ||
      !Number.isInteger(report.symbols_evaluated) ||
      report.symbols_evaluated < 0 ||
      !Array.isArray(report.symbols_unavailable) ||
      !report.symbols_unavailable.every((s: unknown) => typeof s === "string") ||
      !Array.isArray(report.matches)
    )
      throw new Error("Invalid scan report.");
    const matches = report.matches.map((value: unknown) => {
      if (!value || typeof value !== "object") throw new Error("Invalid scan match.");
      const match = value as Record<string, unknown>;
      if (typeof match.symbol !== "string" || !Array.isArray(match.evaluations))
        throw new Error("Invalid scan match.");
      const strategies = match.evaluations.map((entry: unknown) => {
        if (
          !entry ||
          typeof entry !== "object" ||
          !("name" in entry) ||
          typeof entry.name !== "string"
        )
          throw new Error("Invalid scan strategy.");
        return entry.name;
      });
      return { symbol: match.symbol, strategies };
    });
    return {
      asOf: report.as_of,
      evaluated: report.symbols_evaluated,
      unavailable: report.symbols_unavailable as string[],
      matches,
    };
  });
}

export function UniverseScan({
  onSelect,
}: {
  onSelect: (symbol: string, mode: CandidateMode) => void;
}) {
  const [mode, setMode] = useState<CandidateMode>("swing");
  const [selected, setSelected] = useState<string | null>(null);
  const history = useQuery({
    queryKey: ["scoring-scans", mode],
    queryFn: async () => {
      const response = await fetch(`/api/scoring/scans?mode=${mode}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Could not load saved scans (${response.status}).`);
      return parseHistory(await response.json());
    },
    refetchInterval: 30_000,
  });
  const reports = history.data ?? [];
  const report = reports.find((item) => item.asOf === selected) ?? reports[0];
  return (
    <section className="space-y-4 rounded-xl border bg-card p-4" aria-label="Stock opportunities">
      <div className="flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <h2 className="font-semibold">Saved stock scans</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Scans run in the backend and completed results are saved.
          </p>
        </div>
        <label className="grid gap-1 text-xs font-medium">
          Timeframe
          <select
            value={mode}
            onChange={(e) => {
              setMode(e.target.value as CandidateMode);
              setSelected(null);
            }}
            className="h-9 rounded-md border bg-background px-3 text-sm"
          >
            <option value="swing">Swing · daily</option>
            <option value="intraday">Intraday · 5 minutes</option>
          </select>
        </label>
        <Link
          href="/admin"
          className="rounded-md border px-3 py-2 text-sm font-medium hover:bg-muted"
        >
          Manage scans in Admin
        </Link>
      </div>
      <p className="text-xs text-muted-foreground">
        In Admin, select Swing strategy scan or Intraday strategy scan to start or stop a run. Only
        completed scans are saved; intraday scans run from 09:15 to 15:00 IST. Selecting a stock
        evaluates it again using the latest available data.
      </p>
      {history.isPending && <p className="text-sm text-muted-foreground">Loading saved scans…</p>}
      {history.error && (
        <p role="alert" className="text-sm text-loss">
          {history.error.message}
        </p>
      )}
      {!history.isPending && !history.error && reports.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No completed scans saved for this timeframe yet.
        </p>
      )}
      {report && (
        <div className="space-y-3 text-sm">
          <label className="grid w-fit gap-1 text-xs font-medium">
            Saved scan
            <select
              value={report.asOf}
              onChange={(e) => setSelected(e.target.value)}
              className="h-9 rounded-md border bg-background px-3 text-sm"
            >
              {reports.map((item) => (
                <option key={item.asOf} value={item.asOf}>
                  {formatIstDateTime(item.asOf)}
                </option>
              ))}
            </select>
          </label>
          <p className="text-muted-foreground">
            {report.evaluated} evaluated · {report.unavailable.length} unavailable ·{" "}
            {report.matches.length} stocks with qualifying setups
          </p>
          {report.unavailable.length > 0 && (
            <details>
              <summary className="cursor-pointer text-muted-foreground">
                Stocks with unavailable data
              </summary>
              <p className="mt-1 break-words text-muted-foreground">
                {report.unavailable.join(", ")}
              </p>
            </details>
          )}
          {report.matches.length === 0 ? (
            <p>No qualifying setups found in this completed scan.</p>
          ) : (
            <ul className="divide-y rounded-lg border">
              {report.matches.map((match) => (
                <li key={match.symbol}>
                  <button
                    type="button"
                    onClick={() => onSelect(match.symbol, mode)}
                    className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left hover:bg-muted/60"
                  >
                    <span className="font-medium">{match.symbol}</span>
                    <span className="text-muted-foreground">{match.strategies.join(", ")}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
