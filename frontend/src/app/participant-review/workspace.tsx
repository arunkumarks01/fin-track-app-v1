"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { fetchReviewed } from "@/lib/participant-reviewed";

const columns = [
  ["fut_idx_long", "Index futures long"],
  ["fut_idx_short", "Index futures short"],
  ["fut_stk_long", "Stock futures long"],
  ["fut_stk_short", "Stock futures short"],
  ["opt_idx_call_long", "Index calls long"],
  ["opt_idx_put_long", "Index puts long"],
  ["opt_idx_call_short", "Index calls short"],
  ["opt_idx_put_short", "Index puts short"],
  ["opt_stk_call_long", "Stock calls long"],
  ["opt_stk_put_long", "Stock puts long"],
  ["opt_stk_call_short", "Stock calls short"],
  ["opt_stk_put_short", "Stock puts short"],
] as const;

export function ReviewWorkspace() {
  const q = useQuery({ queryKey: ["participant-reviewed"], queryFn: fetchReviewed });
  const [selected, setSelected] = useState("");
  if (q.isPending) return <p>Loading reviewed source data…</p>;
  if (q.error) return <p role="alert">Couldn’t load reviewed source data: {q.error.message}</p>;
  const files = q.data.files;
  const file = files.find((x) => `${x.dataset}:${x.date}` === selected) ?? files[0];
  return (
    <div className="space-y-5">
      <aside role="note" className="rounded-xl border border-warn/50 bg-warn/10 p-4 text-sm">
        These are labelled NSE source values for research. They remain separate from verified clean
        analytics. Published values are displayed exactly as stored.
      </aside>
      {file ? (
        <section className="space-y-4 rounded-xl border bg-card p-4 sm:p-5">
          <label className="block text-sm font-medium">
            Choose a file
            <select
              className="mt-2 block w-full rounded-md border bg-background px-3 py-2 sm:max-w-lg"
              value={`${file.dataset}:${file.date}`}
              onChange={(e) => setSelected(e.target.value)}
            >
              {files.map((x) => (
                <option key={`${x.dataset}:${x.date}`} value={`${x.dataset}:${x.date}`}>
                  {x.date} · {x.dataset === "participant_oi" ? "Open interest" : "Trading volume"}
                </option>
              ))}
            </select>
          </label>
          <p className="text-sm">
            <strong>Quality:</strong>{" "}
            {file.quality === "reconciled_within_tolerance"
              ? `Reconciled within ${file.tolerance_contracts} contracts`
              : "Published totals disagree; components reconcile within tolerance"}
          </p>
          <p className="text-sm text-muted-foreground">
            {file.warning} Source: {file.source}.{" "}
            <a href={file.source_url} className="text-primary underline">
              Open source file
            </a>
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[95rem] text-left text-xs">
              <caption className="sr-only">
                Exact published component values and separately derived totals
              </caption>
              <thead>
                <tr>
                  <th className="p-2">Participant</th>
                  {columns.map(([key, label]) => (
                    <th className="p-2 text-right" key={key}>
                      {label}
                    </th>
                  ))}
                  <th className="p-2 text-right">Derived long</th>
                  <th className="p-2 text-right">Derived short</th>
                  <th className="p-2 text-right">NSE reported long</th>
                  <th className="p-2 text-right">NSE reported short</th>
                </tr>
              </thead>
              <tbody>
                {file.participants.map((row) => (
                  <tr className="border-t" key={row.participant}>
                    <th scope="row" className="p-2">
                      {row.participant}
                    </th>
                    {columns.map(([key]) => (
                      <td className="p-2 text-right tabular-nums" key={key}>
                        {row.components[key] ?? "—"}
                      </td>
                    ))}
                    <td className="p-2 text-right tabular-nums">{row.derived_totals.long}</td>
                    <td className="p-2 text-right tabular-nums">{row.derived_totals.short}</td>
                    <td className="p-2 text-right tabular-nums">{row.reported_totals.long}</td>
                    <td className="p-2 text-right tabular-nums">{row.reported_totals.short}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-muted-foreground">
            Derived totals sum the published components. NSE reported totals are the original file
            values
            {file.quality === "component_totals_disagree"
              ? " and should not be treated as correct for this file"
              : ""}
            . Source SHA-256: {file.source_sha256}
          </p>
          <details className="text-sm">
            <summary className="cursor-pointer font-medium">Published total differences</summary>
            <p className="mt-2 text-muted-foreground">
              NSE’s published total minus the corresponding published component sum.
            </p>
            {Object.entries(file.source_total_differences).length ? (
              <ul className="mt-2 space-y-1 tabular-nums">
                {Object.entries(file.source_total_differences).map(([key, value]) => (
                  <li key={key}>
                    {key}: {value}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-muted-foreground">No differences reported.</p>
            )}
          </details>
        </section>
      ) : (
        <p>No reviewed files are available yet.</p>
      )}
      <section className="rounded-xl border bg-card p-4">
        <h2 className="font-semibold">Explicit gaps</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          These requested dates are not included in the research values because their source dates
          or checks remain unverified.
        </p>
        {q.data.gaps.length ? (
          <ul className="mt-3 space-y-1 text-sm">
            {q.data.gaps.map((g) => (
              <li key={`${g.dataset}:${g.candidate_date}`}>
                {g.candidate_date} · {g.dataset} · {g.reason.replaceAll("_", " ")}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm">No review gaps.</p>
        )}
      </section>
    </div>
  );
}
