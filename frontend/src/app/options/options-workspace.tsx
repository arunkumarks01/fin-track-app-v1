"use client";

import { useQuery } from "@tanstack/react-query";
import { ChartNoAxesCombined, RefreshCw } from "lucide-react";
import { useId, useState, type FormEvent, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { Input } from "@/components/ui/input";
import {
  basisPoints,
  fetchOptions,
  retryAnalytics,
  type OptionContract,
  type OptionExpiry,
  type PricingInputs,
} from "@/lib/analytics";
import {
  formatDecimal,
  formatInt,
  formatIstDateTime,
  formatPct,
  formatPrice,
  formatRupees,
} from "@/lib/format";
import { fetchTicks } from "@/lib/ticks";

const INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"];

function item(value: number | null, format: (v: number) => string = formatDecimal) {
  return value === null ? "—" : format(value);
}
function pct(value: number) {
  return formatPct(value, 2);
}
function list(strikes: number[] | null) {
  return strikes && strikes.length > 0 ? strikes.map(formatPrice).join(" / ") : "—";
}

function Metric({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="min-w-0 bg-card px-3 py-2.5 sm:px-4">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-lg font-semibold tabular-nums break-words">
        {value}
        {note && (
          <span className="mt-0.5 block text-xs font-normal text-muted-foreground">{note}</span>
        )}
      </dd>
    </div>
  );
}

export function OptionsWorkspace() {
  const listId = useId();
  const [symbolInput, setSymbolInput] = useState("NIFTY");
  const [rate, setRate] = useState("");
  const [dividend, setDividend] = useState("");
  const [selection, setSelection] = useState<{ symbol: string; pricing: PricingInputs }>({
    symbol: "NIFTY",
    pricing: {},
  });
  const [inputError, setInputError] = useState<string | null>(null);

  const catalog = useQuery({
    queryKey: ["ticks-status"],
    queryFn: () => fetchTicks(),
    staleTime: 60_000,
    retry: false,
  });
  const choices = [
    ...new Set([...INDICES, ...(catalog.data?.chain.latest.map((r) => r.underlying) ?? [])]),
  ];
  const report = useQuery({
    queryKey: ["fintrack-options", selection.symbol, selection.pricing],
    queryFn: ({ signal }) => fetchOptions(selection.symbol, selection.pricing, signal),
    retry: retryAnalytics,
    staleTime: 30_000,
    refetchInterval: (q) => (q.state.error ? false : 60_000),
    refetchIntervalInBackground: false,
  });

  const apply = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      const symbol = symbolInput.trim().toUpperCase();
      if (!symbol || symbol.length > 80) throw new Error("Enter an underlying symbol.");
      const rateBps = basisPoints(rate, -2000, 5000);
      const dividendBps = basisPoints(dividend, 0, 5000);
      setInputError(null);
      if (
        symbol === selection.symbol &&
        rateBps === selection.pricing.rateBps &&
        dividendBps === selection.pricing.dividendBps
      ) {
        void report.refetch();
      } else {
        setSelection({ symbol, pricing: { rateBps, dividendBps } });
      }
    } catch (error) {
      setInputError(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <form onSubmit={apply} className="rounded-xl border bg-card p-3 sm:p-4">
        <div className="grid gap-3 sm:grid-cols-[minmax(9rem,1fr)_minmax(8rem,1fr)_minmax(8rem,1fr)_auto] sm:items-end">
          <label className="grid gap-1 text-xs font-medium">
            Underlying
            <Input
              list={listId}
              value={symbolInput}
              onChange={(e) => setSymbolInput(e.target.value)}
              maxLength={80}
              type="text"
            />
            <datalist id={listId}>
              {choices.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>
          <label className="grid gap-1 text-xs font-medium">
            Risk-free rate (%)
            <Input
              value={rate}
              onChange={(e) => setRate(e.target.value)}
              inputMode="decimal"
              placeholder="Backend setting"
              maxLength={8}
            />
          </label>
          <label className="grid gap-1 text-xs font-medium">
            Dividend yield (%)
            <Input
              value={dividend}
              onChange={(e) => setDividend(e.target.value)}
              inputMode="decimal"
              placeholder="Backend setting"
              maxLength={8}
            />
          </label>
          <Button type="submit">
            <RefreshCw data-icon="inline-start" aria-hidden /> Apply
          </Button>
        </div>
        {inputError ? (
          <p className="mt-2 text-sm text-loss" role="alert">
            {inputError}
          </p>
        ) : (
          <p className="mt-2 text-xs text-muted-foreground">
            IV and Greeks need both pricing assumptions.
          </p>
        )}
      </form>
      {report.isPending ? (
        <p className="text-sm text-muted-foreground" role="status">
          Loading the saved chain for {selection.symbol}…
        </p>
      ) : report.error ? (
        <p className="rounded-xl border bg-card p-4 text-sm text-muted-foreground" role="status">
          {report.error.message}
        </p>
      ) : report.data.status === "no_snapshot" ? (
        <div className="rounded-xl border bg-card p-5">
          <h2 className="flex items-center gap-2 font-semibold">
            <ChartNoAxesCombined size={18} className="text-primary" aria-hidden /> No saved option
            chain for {selection.symbol}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Analytics will appear after Kite records a snapshot for this underlying.
          </p>
        </div>
      ) : (
        <Snapshot key={selection.symbol} report={report.data} />
      )}
    </div>
  );
}

type SnapshotReport = Exclude<Awaited<ReturnType<typeof fetchOptions>>, { status: "no_snapshot" }>;

function Snapshot({ report }: { report: SnapshotReport }) {
  const [expiryDate, setExpiry] = useState("");
  const current =
    report.expiries.find((expiry) => expiry.date === expiryDate) ?? report.expiries[0];
  let rankNote = `${formatInt(report.rank.observations)} comparable sessions`;
  if (report.rank.current === null) rankNote = "No comparable current IV";
  else if (report.rank.observations < report.rank.minimum) {
    rankNote = `${report.rank.observations} of ${report.rank.minimum} comparable sessions`;
  } else if (report.rank.pct === null) rankNote = "No variation in observed IV";

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <section aria-label="Snapshot overview" className="rounded-xl border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-2 p-3 sm:px-4">
          <div>
            <h2 className="font-semibold">{report.underlying}</h2>
            <p className="text-xs text-muted-foreground">
              Snapshot · {formatIstDateTime(report.asOf)} · Captured strikes only
            </p>
          </div>
          <div className="flex gap-1.5">
            {report.status !== "ok" && (
              <Badge variant="outline" className="text-warn">
                {report.status === "partial" ? "Partial snapshot" : "Snapshot error"}
              </Badge>
            )}
            {report.stale && (
              <Badge variant="outline" className="text-warn">
                Stale snapshot
              </Badge>
            )}
          </div>
        </div>
        <dl className="grid grid-cols-2 gap-px border-t bg-border lg:grid-cols-4">
          <Metric label="Spot" value={item(report.spot, formatPrice)} />
          <Metric
            label="Near-month future"
            value={item(report.future, formatPrice)}
            note={report.futureExpiry ?? undefined}
          />
          <Metric label={`${report.rank.days}-day IV`} value={item(report.rank.current, pct)} />
          <Metric label="IV rank" value={item(report.rank.pct, pct)} note={rankNote} />
        </dl>
        <div className="flex flex-wrap gap-x-5 gap-y-1 border-t p-3 text-xs text-muted-foreground sm:px-4">
          <span>Risk-free rate {report.rateBps === null ? "—" : pct(report.rateBps / 100)}</span>
          <span>
            Dividend yield {report.dividendBps === null ? "—" : pct(report.dividendBps / 100)}
          </span>
          {report.previousAsOf && (
            <span>OI compared with {formatIstDateTime(report.previousAsOf)}</span>
          )}
        </div>
      </section>
      {current ? (
        <section aria-label="Expiry analytics" className="min-w-0 rounded-xl border bg-card">
          <div className="flex flex-wrap items-center justify-between gap-2 p-3 sm:px-4">
            <div>
              <h2 className="font-semibold">Expiry analytics</h2>
              <p className="text-xs text-muted-foreground">
                {current.days > 0
                  ? `${formatDecimal(current.days, 1)} calendar days to expiry`
                  : "Expired"}
              </p>
            </div>
            <label className="flex items-center gap-2 text-sm">
              Expiry
              <select
                value={current.date}
                onChange={(e) => setExpiry(e.target.value)}
                className="h-8 rounded-lg border border-input bg-background px-2 text-sm focus-visible:outline-ring"
              >
                {report.expiries.map((expiry) => (
                  <option key={expiry.date}>{expiry.date}</option>
                ))}
              </select>
            </label>
          </div>
          <dl className="grid grid-cols-2 gap-px border-y bg-border lg:grid-cols-5">
            <Metric label="Put/call OI" value={item(current.pcrOI)} />
            <Metric label="Put/call volume" value={item(current.pcrVolume)} />
            <Metric label="Max pain" value={list(current.maxPain)} />
            <Metric
              label="Gross gamma wall"
              value={list(current.gammaWall?.strikes ?? null)}
              note={
                current.gammaWall
                  ? `${formatRupees(current.gammaWall.exposure)} per 1% move`
                  : undefined
              }
            />
            <Metric label="ATM IV" value={item(current.atmIV, pct)} />
          </dl>
          {!current.complete && (
            <p className="px-4 pt-3 text-xs text-warn">
              Incomplete call and put pairs. Chain-wide metrics are unavailable.
            </p>
          )}
          <OpenInterest expiry={current} />
          <ChainTable expiry={current} />
        </section>
      ) : (
        <p className="text-sm text-muted-foreground">This snapshot has no stored contracts.</p>
      )}
    </div>
  );
}

function OpenInterest({ expiry }: { expiry: OptionExpiry }) {
  const strikes = new Map<number, { strike: number; CE: number | null; PE: number | null }>();
  for (const row of expiry.contracts) {
    const entry = strikes.get(row.strike) ?? { strike: row.strike, CE: null, PE: null };
    entry[row.type] = row.oi;
    strikes.set(row.strike, entry);
  }
  const data = [...strikes.values()].sort((a, b) => a.strike - b.strike);
  if (!data.some((row) => row.CE !== null || row.PE !== null)) return null;
  return (
    <div className="p-3 sm:p-4">
      <h3 className="mb-2 text-sm font-medium">Open interest by strike</h3>
      <div className="mb-2 flex gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="size-2 rounded-sm bg-[var(--chart-1)]" />
          Calls
        </span>
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="size-2 rounded-sm bg-[var(--chart-2)]" />
          Puts
        </span>
      </div>
      <ChartContainer
        config={{
          CE: { label: "Calls", color: "var(--chart-1)" },
          PE: { label: "Puts", color: "var(--chart-2)" },
        }}
        className="h-52 w-full aspect-auto"
      >
        <BarChart data={data} accessibilityLayer>
          <CartesianGrid vertical={false} />
          <XAxis dataKey="strike" tickFormatter={formatPrice} minTickGap={34} tickLine={false} />
          <YAxis tickFormatter={formatInt} width={82} tickLine={false} />
          <ChartTooltip
            content={<ChartTooltipContent labelFormatter={(v) => formatPrice(Number(v))} />}
          />
          <Bar
            dataKey="CE"
            fill="var(--color-CE)"
            isAnimationActive={false}
            radius={[2, 2, 0, 0]}
          />
          <Bar
            dataKey="PE"
            fill="var(--color-PE)"
            isAnimationActive={false}
            radius={[2, 2, 0, 0]}
          />
        </BarChart>
      </ChartContainer>
    </div>
  );
}

const REASON: Record<string, string> = {
  expired: "Expired",
  missing_or_crossed_quote: "Missing or crossed bid/ask",
  missing_spot: "Spot price unavailable",
  pricing_inputs_required: "Rate and dividend yield required",
  premium_outside_model_bounds: "Premium outside model bounds",
};

function ContractRow({ row }: { row: OptionContract }) {
  const g = row.greeks;
  const change = row.oiChange;
  const pattern = row.pattern?.replaceAll("_", " ").replace(" oi ", " OI ");
  return (
    <tr className="border-t hover:bg-muted/30">
      <th scope="row" className="p-2 text-left font-medium tabular-nums">
        {formatPrice(row.strike)}
      </th>
      <td className="p-2 text-left">{row.type === "CE" ? "Call" : "Put"}</td>
      <td
        className="p-2"
        title={`Bid ${item(row.bid, formatPrice)} · Ask ${item(row.ask, formatPrice)}`}
      >
        {item(row.midpointTwice, (v) => formatDecimal(v / 200, 3))}
      </td>
      <td className="p-2">{item(row.oi, formatInt)}</td>
      <td
        className={`p-2 ${change && change > 0 ? "text-gain" : change && change < 0 ? "text-loss" : ""}`}
        title={pattern}
      >
        {change && change > 0 ? "+" : ""}
        {item(change, formatInt)}
      </td>
      <td className="p-2">{item(row.volume, formatInt)}</td>
      <td className="p-2">{item(row.iv, pct)}</td>
      <td className="p-2">{item(g?.delta ?? null, (v) => formatDecimal(v, 4))}</td>
      <td className="p-2">{item(g?.gamma ?? null, (v) => formatDecimal(v * 100, 6))}</td>
      <td className="p-2">{item(g?.theta ?? null, (v) => formatDecimal(v / 100, 4))}</td>
      <td className="p-2">{item(g?.vega ?? null, (v) => formatDecimal(v / 100, 4))}</td>
      <td className="p-2">{item(g?.rho ?? null, (v) => formatDecimal(v / 100, 4))}</td>
      <td className="p-2 text-left text-muted-foreground">
        {row.reason ? (REASON[row.reason] ?? "Model value unavailable") : (pattern ?? "—")}
      </td>
    </tr>
  );
}

function ChainTable({ expiry }: { expiry: OptionExpiry }) {
  if (expiry.contracts.length === 0)
    return <p className="px-4 py-3 text-sm text-muted-foreground">No contracts for this expiry.</p>;
  return (
    <div className="border-t">
      <div className="flex items-baseline justify-between gap-2 px-3 py-2 sm:px-4">
        <h3 className="text-sm font-medium">Captured contracts</h3>
        <p className="text-xs text-muted-foreground">
          {formatInt(expiry.contracts.length)} contracts
        </p>
      </div>
      <div
        className="overflow-x-auto"
        tabIndex={0}
        role="region"
        aria-label="Options contract table"
      >
        <table className="w-full text-right text-xs whitespace-nowrap tabular-nums">
          <thead className="bg-muted/60 text-muted-foreground">
            <tr>
              <th scope="col" className="p-2 text-left font-medium">
                Strike
              </th>
              <th scope="col" className="p-2 text-left font-medium">
                Type
              </th>
              {[
                "Mid",
                "OI",
                "Δ OI",
                "Volume",
                "IV",
                "Delta",
                "Gamma / ₹",
                "Theta ₹/day",
                "Vega ₹/pt",
                "Rho ₹/pt",
              ].map((heading) => (
                <th key={heading} scope="col" className="p-2 font-medium">
                  {heading}
                </th>
              ))}
              <th scope="col" className="p-2 text-left font-medium">
                Model / OI change
              </th>
            </tr>
          </thead>
          <tbody>
            {expiry.contracts.map((row) => (
              <ContractRow key={`${row.strike}-${row.type}`} row={row} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
