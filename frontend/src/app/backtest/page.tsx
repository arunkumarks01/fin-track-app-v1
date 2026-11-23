import type { Metadata } from "next";

import { exampleTrades, startingCapital, summarizeExample } from "@/lib/backtest-example";
import { formatPct, formatRupees } from "@/lib/format";

export const metadata: Metadata = { title: "Backtest learning · FinTrack" };

const summary = summarizeExample(startingCapital, exampleTrades);
const chartMin = Math.min(...summary.curve);
const chartMax = Math.max(...summary.curve);
const chartRange = chartMax - chartMin || 1;
const points = summary.curve
  .map(
    (value, i) =>
      `${40 + (i * 520) / (summary.curve.length - 1)},${160 - ((value - chartMin) / chartRange) * 120}`,
  )
  .join(" ");

export default function BacktestPage() {
  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 sm:p-6">
      <header className="max-w-3xl space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Understand a backtest</h1>
        <p className="text-muted-foreground">
          Learn to read a strategy’s results before using them to make decisions.
        </p>
      </header>
      <aside
        className="rounded-xl border border-warn/50 bg-warn/10 p-4 text-sm leading-relaxed"
        role="note"
      >
        <strong>Illustration only.</strong> Every trade and amount below is invented. This is not a
        real backtest, a recommendation, or a forecast. The backtest engine is still being built.
      </aside>
      <section
        aria-labelledby="example-title"
        className="overflow-hidden rounded-xl border bg-card text-card-foreground"
      >
        <div className="flex flex-wrap items-end justify-between gap-4 border-b p-5">
          <div>
            <h2 id="example-title" className="text-lg font-semibold">
              A four-trade example
            </h2>
            <p className="text-sm text-muted-foreground">
              Starting capital {formatRupees(startingCapital)} · imaginary net trade outcomes
            </p>
          </div>
          <span className="rounded-md bg-muted px-3 py-1 text-xs font-medium">Sample data</span>
        </div>
        <div className="grid gap-px bg-border sm:grid-cols-3">
          <div className="bg-card p-5">
            <p className="text-sm text-muted-foreground">Net return</p>
            <p className="mt-1 text-2xl font-semibold">{formatPct(summary.returnPct)}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {formatRupees(summary.net)} after the four outcomes
            </p>
          </div>
          <div className="bg-card p-5">
            <p className="text-sm text-muted-foreground">Win rate</p>
            <p className="mt-1 text-2xl font-semibold">{formatPct(summary.winRate, 0)}</p>
            <p className="mt-1 text-sm text-muted-foreground">2 profitable trades out of 4</p>
          </div>
          <div className="bg-card p-5">
            <p className="text-sm text-muted-foreground">Largest drawdown</p>
            <p className="mt-1 text-2xl font-semibold">{formatPct(summary.maxDrawdownPct, 2)}</p>
            <p className="mt-1 text-sm text-muted-foreground">Largest fall from a previous peak</p>
          </div>
        </div>
        <div className="grid gap-6 border-t p-5 lg:grid-cols-[minmax(0,2fr)_minmax(220px,1fr)]">
          <div>
            <h3 className="font-medium">How the balance changes</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Each point is the balance after one imaginary trade.
            </p>
            <svg
              viewBox="0 0 600 200"
              role="img"
              aria-label="Sample balance rises, falls, rises to a peak, then falls"
              className="mt-3 w-full"
            >
              <line x1="40" y1="160" x2="560" y2="160" stroke="currentColor" opacity=".2" />
              <polyline fill="none" stroke="var(--color-primary)" strokeWidth="3" points={points} />
              {summary.curve.map((v, i) => (
                <circle
                  key={i}
                  cx={40 + (i * 520) / (summary.curve.length - 1)}
                  cy={160 - ((v - chartMin) / chartRange) * 120}
                  r="4"
                  fill="var(--color-primary)"
                />
              ))}
              <text x="40" y="190" fill="currentColor" fontSize="13">
                Start
              </text>
              <text x="510" y="190" fill="currentColor" fontSize="13">
                Trade 4
              </text>
            </svg>
          </div>
          <div>
            <h3 className="font-medium">Sample outcomes</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Assumed net results; no separate cost model is applied.
            </p>
            <table className="mt-3 w-full text-sm">
              <caption className="sr-only">Imaginary trade outcomes</caption>
              <tbody>
                {exampleTrades.map((trade) => (
                  <tr className="border-b last:border-0" key={trade.label}>
                    <th scope="row" className="py-2 text-left font-normal">
                      {trade.label}
                    </th>
                    <td className="py-2 text-right tabular-nums">
                      {trade.result > 0 ? "+" : ""}
                      {formatRupees(trade.result)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
      <section
        aria-labelledby="read-title"
        className="grid gap-5 rounded-xl border bg-card p-5 md:grid-cols-3"
      >
        <div>
          <h2 id="read-title" className="font-semibold">
            Read the whole picture
          </h2>
          <p className="mt-2 text-sm text-muted-foreground">
            A return alone does not show how bumpy the journey was.
          </p>
        </div>
        <div>
          <h3 className="font-medium">Win rate is not profit</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            The size of gains and losses matters as much as how often a trade wins.
          </p>
        </div>
        <div>
          <h3 className="font-medium">Check the test conditions</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            Real results must disclose the dates, costs, data quality and assumptions. Four trades
            cannot establish an edge.
          </p>
        </div>
      </section>
    </main>
  );
}
