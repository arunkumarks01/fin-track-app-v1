"use client";

import { useQuery } from "@tanstack/react-query";
import { ChartNoAxesCombined, ChevronDown } from "lucide-react";
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { fetchIndicators, retryAnalytics, type IndicatorRow } from "@/lib/analytics";
import {
  formatDecimal,
  formatInt,
  formatIstDate,
  formatIstDateTime,
  formatPct,
  formatPrice,
} from "@/lib/format";

const EMA = [
  { period: 20, key: "ema_20_paisa", color: "var(--chart-1)" },
  { period: 50, key: "ema_50_paisa", color: "var(--chart-2)" },
  { period: 200, key: "ema_200_paisa", color: "var(--chart-3)" },
] as const;

const chartConfig = Object.fromEntries(
  EMA.map(({ period, key, color }) => [key, { label: `EMA ${period}`, color }]),
) satisfies ChartConfig;

const EXTRA = [
  {
    title: "Trend & momentum",
    fields: [
      ["adx", "ADX"],
      ["di_plus", "+DI"],
      ["di_minus", "−DI"],
      ["macd_paisa", "MACD"],
      ["macd_signal_paisa", "MACD signal"],
      ["macd_histogram_paisa", "MACD histogram"],
      ["supertrend_paisa", "Supertrend"],
      ["stochastic_k_pct", "Stochastic %K"],
      ["stochastic_d_pct", "Stochastic %D"],
      ["roc_pct", "Rate of change"],
      ["relative_strength_pct", "Relative return vs benchmark"],
    ],
  },
  {
    title: "Bands & prior ranges",
    fields: [
      ["bollinger_upper_paisa", "Bollinger upper"],
      ["bollinger_middle_paisa", "Bollinger middle"],
      ["bollinger_lower_paisa", "Bollinger lower"],
      ["keltner_upper_paisa", "Keltner upper"],
      ["keltner_middle_paisa", "Keltner middle"],
      ["keltner_lower_paisa", "Keltner lower"],
    ],
  },
  {
    title: "Session & volume",
    fields: [
      ["vwap_paisa", "Estimated session VWAP"],
      ["pivot_paisa", "Central pivot"],
      ["cpr_top_paisa", "CPR top"],
      ["cpr_bottom_paisa", "CPR bottom"],
      ["gap_pct", "Opening gap"],
      ["opening_range_high_paisa", "Opening range high"],
      ["opening_range_low_paisa", "Opening range low"],
      ["obv", "On-balance volume"],
      ["volume_sma", "Average volume"],
      ["relative_volume", "Relative volume"],
    ],
  },
] as const;

function indicatorValue(key: string, value: number | null | undefined) {
  if (value == null) return "—";
  if (key.endsWith("_paisa")) return formatPrice(value);
  if (key.endsWith("_pct")) return formatPct(value, 2);
  return formatDecimal(value);
}

function statusText(status: string) {
  if (status === "warming_up") return "Some readings are still warming up.";
  if (status === "no_usable_bars") return "No usable finalized candles.";
  if (status === "session_calendar_unavailable" || status === "session_open_unavailable") {
    return "The session calendar is unavailable for this candle.";
  }
  if (status.startsWith("intraday_session_") || status === "intraday_candle_gap") {
    return "The latest intraday session has incomplete or unverified data.";
  }
  return status === "ok" ? null : "Indicator availability could not be verified.";
}

function History({ rows, daily }: { rows: IndicatorRow[]; daily: boolean }) {
  if (!rows.some((row) => EMA.some(({ key }) => row.values?.[key] != null))) {
    return <p className="text-sm text-muted-foreground">No moving average history is available.</p>;
  }
  const data = rows.map((row) => ({
    t: row.t,
    ...Object.fromEntries(EMA.map(({ key }) => [key, row.values?.[key] ?? null])),
  }));
  const axisDate = (t: number) =>
    daily ? formatIstDate(t) : formatIstDateTime(new Date(t * 1000).toISOString());
  return (
    <ChartContainer
      config={chartConfig}
      className="h-56 w-full aspect-auto"
      aria-label="History of the 20, 50 and 200 bar exponential moving averages"
    >
      <LineChart data={data} accessibilityLayer margin={{ top: 10, right: 12, left: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="t" tickFormatter={axisDate} minTickGap={70} tickLine={false} />
        <YAxis
          domain={["auto", "auto"]}
          tickFormatter={(v: number) => formatDecimal(v / 100)}
          width={68}
          tickLine={false}
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              labelFormatter={(value) => axisDate(Number(value))}
              formatter={(value, name) => (
                <span className="w-full tabular-nums">
                  {chartConfig[String(name)]?.label} ·{" "}
                  {typeof value === "number" ? formatPrice(value) : "—"}
                </span>
              )}
            />
          }
        />
        {EMA.map(({ key, period }) => (
          <Line
            key={key}
            dataKey={key}
            name={key}
            stroke={`var(--color-${key})`}
            strokeWidth={2}
            strokeDasharray={period === 50 ? "5 3" : period === 200 ? "2 2" : undefined}
            dot={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartContainer>
  );
}

export function IndicatorPanel({
  symbol,
  interval,
  adjustment,
}: {
  symbol: string;
  interval: string;
  adjustment: "adjusted" | "raw";
}) {
  const enabled = adjustment === "adjusted" && interval !== "W";
  const report = useQuery({
    queryKey: ["fintrack-indicators", symbol, interval],
    queryFn: ({ signal }) => fetchIndicators(symbol, interval, signal),
    enabled,
    retry: retryAnalytics,
    staleTime: 30_000,
  });
  const row = report.data?.rows.at(-1);
  const values = row?.values;
  const daily = interval === "D";
  const status = report.data ? statusText(report.data.status) : null;
  const periodUnit = daily ? "trading days" : `${interval} bars`;

  return (
    <section
      aria-label="FinTrack indicators"
      className="rounded-xl border bg-card px-3 py-3 sm:px-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <ChartNoAxesCombined aria-hidden size={17} className="text-primary" /> FinTrack indicators
        </h2>
        <span className="text-xs text-muted-foreground">
          {symbol} · {daily ? "Daily" : interval}
        </span>
      </div>
      {!enabled ? (
        <p className="mt-2 text-sm text-muted-foreground">
          {interval === "W"
            ? "Choose a daily or intraday interval for server indicators."
            : "Switch to Adjusted to see server indicators."}
        </p>
      ) : report.isPending ? (
        <p className="mt-2 text-sm text-muted-foreground" role="status">
          Loading indicators…
        </p>
      ) : report.error ? (
        <div
          role="status"
          className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted-foreground"
        >
          {report.error.message}
          <button
            type="button"
            onClick={() => void report.refetch()}
            className="font-medium text-primary hover:underline"
          >
            Retry
          </button>
        </div>
      ) : (
        <>
          <dl className="mt-3 grid gap-px overflow-hidden rounded-lg border bg-border grid-cols-3 lg:grid-cols-5">
            {[
              ...EMA.map(({ period, key, color }) => ({
                key,
                label: `EMA ${period}`,
                color,
                note: `${period} ${periodUnit}`,
              })),
              { key: "rsi_pct", label: "RSI", color: undefined, note: "Momentum" },
              { key: "atr_paisa", label: "ATR", color: undefined, note: "Average range" },
            ].map(({ key, label, note, color }) => (
              <div key={key} className="min-w-0 bg-card px-3 py-2">
                <dt className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  {color && (
                    <span
                      aria-hidden
                      className="size-2 rounded-full"
                      style={{ backgroundColor: color }}
                    />
                  )}
                  {label}
                </dt>
                <dd className="text-base font-semibold tabular-nums">
                  {indicatorValue(key, values?.[key])}
                  <span className="block text-xs font-normal text-muted-foreground">{note}</span>
                </dd>
              </div>
            ))}
          </dl>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
            {row && (
              <span>
                As of{" "}
                {daily
                  ? formatIstDate(row.t)
                  : formatIstDateTime(new Date(row.t * 1000).toISOString())}{" "}
                · {formatInt(row.usableBars)} usable bars
              </span>
            )}
            {status && <span className="text-warn">{status}</span>}
            {report.data.behindBenchmark && (
              <span className="text-warn">
                Behind benchmark session {report.data.latestBenchmarkSession}.
              </span>
            )}
          </div>
          {values && (
            <details className="mt-2 border-t pt-2">
              <summary className="flex cursor-pointer list-none items-center gap-1 text-sm font-medium text-primary">
                <ChevronDown aria-hidden size={16} /> EMA history and more indicators
              </summary>
              <div className="mt-3">
                <History rows={report.data.rows} daily={daily} />
                <div className="mt-3 grid gap-4 md:grid-cols-3">
                  {EXTRA.map(({ title, fields }) => (
                    <div key={title} className="min-w-0">
                      <h3 className="mb-1 text-xs font-semibold">{title}</h3>
                      <dl className="space-y-1 text-xs">
                        {fields.map(([key, label]) => (
                          <div key={key} className="flex justify-between gap-2">
                            <dt className="text-muted-foreground">{label}</dt>
                            <dd className="shrink-0 tabular-nums">
                              {indicatorValue(key, values?.[key])}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  ))}
                </div>
                <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t pt-2 text-xs">
                  {Object.entries(values ?? {})
                    .filter(
                      ([key]) =>
                        /^(?:sma_|prior_|ema_)/.test(key) &&
                        !EMA.some((entry) => entry.key === key),
                    )
                    .map(([key, value]) => (
                      <div key={key} className="flex gap-1">
                        <dt className="text-muted-foreground">
                          {key
                            .replace(/_paisa$/, "")
                            .replaceAll("_", " ")
                            .replace(/^sma/, "SMA")
                            .replace(/^ema/, "EMA")
                            .replace(/^prior/, "Prior")}
                        </dt>
                        <dd className="tabular-nums">{indicatorValue(key, value)}</dd>
                      </div>
                    ))}
                </dl>
              </div>
            </details>
          )}
        </>
      )}
    </section>
  );
}
