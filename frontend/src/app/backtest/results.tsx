"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { backtestResult, backtestRuns } from "@/lib/backtest";
import { formatPct, formatRupees } from "@/lib/format";

export function BacktestResults() {
  const [selected, setSelected] = useState<number | null>(null);
  const runs = useQuery({
    queryKey: ["backtest-runs"],
    queryFn: backtestRuns,
    refetchInterval: 10000,
  });
  const id = selected ?? runs.data?.[0]?.id;
  const detail = useQuery({
    queryKey: ["backtest-result", id],
    queryFn: () => backtestResult(id!),
    enabled: id !== undefined,
    refetchInterval: 10000,
  });
  const data = detail.data;
  const m = data?.metrics;
  const points = data ? [{ day: data.run.start, value: data.run.capital }, ...data.equity] : [];
  const min = Math.min(...points.map((p) => p.value));
  const max = Math.max(...points.map((p) => p.value));
  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 sm:p-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Historical strategy studies</h1>
        <p className="text-muted-foreground">
          Persisted daily-long runs on verified NSE bars. Select a run to inspect its costs, fills
          and marked equity.
        </p>
      </header>
      <aside className="rounded-xl border border-warn/50 bg-warn/10 p-4 text-sm" role="note">
        Research baselines: single symbol, fixed share quantity, price returns excluding dividends.
        These studies do not establish an edge or live-market acceptance. Compare matching dates,
        capital, quantity and assumptions. Open positions remain marked at the final close.
      </aside>
      {runs.isPending && <p role="status">Loading run ledger…</p>}
      {runs.error && <p role="alert">{runs.error.message}</p>}
      {runs.data?.length === 0 && (
        <p>
          No historical runs have been recorded. Run a verified-history study to populate this
          ledger.
        </p>
      )}
      {!!runs.data?.length && (
        <section className="overflow-x-auto rounded-xl border bg-card p-5" aria-label="Run ledger">
          <table className="w-full text-sm">
            <caption className="mb-3 text-left font-semibold">
              Latest runs · refreshes every 10 seconds
            </caption>
            <thead>
              <tr className="border-b text-left">
                <th className="p-2">Run / strategy</th>
                <th>Symbol / period</th>
                <th>Status</th>
                <th>Net after costs</th>
                <th>Closed trades</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id} className="border-b last:border-0">
                  <td className="p-2">
                    <button
                      className="text-left text-primary underline underline-offset-4"
                      onClick={() => setSelected(r.id)}
                      aria-pressed={id === r.id}
                    >
                      #{r.id} {r.strategy} v{r.revision}
                    </button>
                  </td>
                  <td>
                    {r.symbol}
                    <br />
                    {r.start} → {r.end}
                  </td>
                  <td>{r.status}</td>
                  <td>{r.net === null ? "—" : formatRupees(r.net)}</td>
                  <td>{r.trades ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {detail.error && <p role="alert">{detail.error.message}</p>}
      {data && (
        <section className="space-y-5 rounded-xl border bg-card p-5" aria-label="Selected run">
          <h2 className="text-lg font-semibold">
            Run #{data.run.id} · {data.run.strategy} · {data.run.status}
          </h2>
          {data.run.detail && <p role="status">{data.run.detail}</p>}
          {m && (
            <>
              <dl className="grid gap-4 sm:grid-cols-4">
                <div>
                  <dt className="text-sm text-muted-foreground">Net return after costs</dt>
                  <dd className="text-xl font-semibold">
                    {formatPct((100 * m.net) / data.run.capital)} · {formatRupees(m.net)}
                  </dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">Largest peak-to-trough fall</dt>
                  <dd className="text-xl">{formatPct(100 * m.drawdown, 2)}</dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">Closed outcomes</dt>
                  <dd>
                    {m.trades} · {m.wins} wins / {m.losses} losses
                  </dd>
                </div>
                <div>
                  <dt className="text-sm text-muted-foreground">Profit factor</dt>
                  <dd>
                    {m.grossLoss
                      ? (m.grossProfit / m.grossLoss).toFixed(2)
                      : "Unavailable (no closed loss)"}
                  </dd>
                </div>
              </dl>
              <p className="text-sm">
                Ending marked equity {formatRupees(m.ending)} · Shares still open:{" "}
                {String(data.run.state?.quantity ?? "unknown")}. Closed outcomes combine partial
                exit fills into one completed trade.
              </p>
              {points.length > 1 && (
                <figure>
                  <figcaption className="text-sm">
                    Daily equity · {data.equity[0]?.day} → {data.equity.at(-1)?.day} · range{" "}
                    {formatRupees(min)} – {formatRupees(max)}
                  </figcaption>
                  <svg
                    viewBox="0 0 1000 220"
                    className="mt-3 w-full"
                    role="img"
                    aria-label="Chronological daily marked equity including initial capital"
                  >
                    <polyline
                      fill="none"
                      stroke="var(--color-primary)"
                      strokeWidth="2"
                      points={points
                        .map(
                          (p, i) =>
                            `${10 + (i * 980) / (points.length - 1)},${200 - ((p.value - min) * 180) / (max - min || 1)}`,
                        )
                        .join(" ")}
                    />
                  </svg>
                </figure>
              )}
            </>
          )}
          <details>
            <summary className="cursor-pointer font-medium">
              Recorded assumptions and input fingerprint
            </summary>
            <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-words text-xs">
              {JSON.stringify(data.run.assumptions, null, 2)}
            </pre>
          </details>
          <details>
            <summary className="cursor-pointer font-medium">Fills ({data.fills.length})</summary>
            <div className="mt-3 max-h-96 overflow-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">
                  Persisted execution fills in chronological order
                </caption>
                <thead>
                  <tr className="text-left">
                    <th>Date</th>
                    <th>Side / reason</th>
                    <th>Shares</th>
                    <th>Price</th>
                    <th>Fees</th>
                    <th>Realized</th>
                  </tr>
                </thead>
                <tbody>
                  {data.fills.map((f) => (
                    <tr key={f.id} className="border-b">
                      <td className="py-2">{f.day}</td>
                      <td>
                        {f.side} · {f.reason}
                      </td>
                      <td>{f.quantity}</td>
                      <td>{formatRupees(f.price)}</td>
                      <td>{formatRupees(f.fees)}</td>
                      <td>{f.realized == null ? "—" : formatRupees(f.realized)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </section>
      )}
    </main>
  );
}
