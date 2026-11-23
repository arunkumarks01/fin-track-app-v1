"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  Bar,
  type BarShapeProps,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  XAxis,
  YAxis,
} from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";
import { parseIndicatorReport } from "@/lib/analytics";
import { FinTrackFeed } from "@/lib/charts";
import { formatDecimal, formatIstDate, formatIstTime } from "@/lib/format";
import type { Candidate, CandidateReport } from "@/lib/scoring";

const PRICE_HISTORY = 45;
const EMA = {
  ema_9_paisa: ["EMA 9", "var(--chart-1)"],
  ema_21_paisa: ["EMA 21", "var(--chart-2)"],
  ema_50_paisa: ["EMA 50", "var(--chart-1)"],
  ema_200_paisa: ["EMA 200", "var(--chart-2)"],
} as const;

const LEVELS: Record<string, string> = {
  prior_252_high_paisa: "Prior 52-week high",
  prior_252_low_paisa: "Prior 52-week low",
  prior_20_high_paisa: "Prior 20-bar high",
  prior_20_low_paisa: "Prior 20-bar low",
  opening_range_high_paisa: "Opening-range high",
  opening_range_low_paisa: "Opening-range low",
  bollinger_upper_paisa: "Upper Bollinger band at evaluation",
  bollinger_lower_paisa: "Lower Bollinger band at evaluation",
  cpr_top_paisa: "CPR upper boundary",
  cpr_bottom_paisa: "CPR lower boundary",
  previous_close_paisa: "Prior session close",
  vwap_paisa: "VWAP at evaluation",
  gamma_wall_paisa: "Gross gamma wall at capture",
};

type EmaKey = keyof typeof EMA;
type CandlePoint = {
  t: number;
  open: number;
  close: number;
  range: [number, number];
  evaluated: boolean;
};

function isEma(field: string | null): field is EmaKey {
  return field !== null && Object.hasOwn(EMA, field);
}

function Candle({ x, y, height, width, payload }: BarShapeProps) {
  const point = payload as CandlePoint | undefined;
  if (!point) return null;
  const [low, high] = point.range;
  const priceY = (price: number) =>
    high === low ? y : y + ((high - price) / (high - low)) * height;
  const color = point.close >= point.open ? "var(--gain)" : "var(--loss)";
  const center = x + width / 2;
  const body = Math.min(8, width);
  const open = priceY(point.open);
  const close = priceY(point.close);
  return (
    <g>
      <line x1={center} x2={center} y1={y} y2={y + height} stroke={color} />
      <rect
        x={center - body / 2}
        y={Math.min(open, close)}
        width={body}
        height={Math.max(1, Math.abs(open - close))}
        fill={color}
        stroke={point.evaluated ? "currentColor" : undefined}
      />
    </g>
  );
}

export function SetupChart({ report, item }: { report: CandidateReport; item: Candidate }) {
  const latest = report.latestBar;
  const relevant = item.evidence.filter((e) => !e.previous);
  const emaFields = [
    ...new Set(relevant.flatMap((e) => [e.field, e.thresholdField]).filter(isEma)),
  ];
  const level = relevant.find(
    (e) => e.thresholdField && Object.hasOwn(LEVELS, e.thresholdField) && e.threshold !== null,
  );
  const levelLabel = level?.thresholdField && LEVELS[level.thresholdField];
  const levelPrice = level && level.threshold !== null ? level.threshold / 100 : null;
  const prices = useQuery({
    queryKey: ["candidate-candles", report.symbol, report.interval, latest],
    enabled: latest !== null,
    queryFn: async ({ signal }) => {
      if (latest === null) return [];
      const [exchange, symbol] = report.symbol.split(":");
      return new FinTrackFeed().getBars({
        symbol: symbol ?? "",
        exchange: exchange ?? "",
        interval: report.interval,
        countBack: PRICE_HISTORY,
        to: latest,
        signal,
      });
    },
    retry: false,
  });
  const indicators = useQuery({
    queryKey: ["candidate-chart-indicators", report.symbol, report.interval, report.asOf],
    enabled: latest !== null && emaFields.length > 0,
    queryFn: async ({ signal }) => {
      const query = new URLSearchParams({
        symbol: report.symbol,
        interval: report.interval,
        count: String(PRICE_HISTORY),
        before: report.asOf,
      });
      const res = await fetch(`/api/analytics/indicators?${query}`, { signal, cache: "no-store" });
      if (!res.ok) throw new Error("Moving-average history is unavailable.");
      return parseIndicatorReport(await res.json());
    },
    retry: false,
  });

  if (latest === null || prices.error) {
    return (
      <p className="text-sm text-muted-foreground">
        Chart data is unavailable for this evaluation.
      </p>
    );
  }
  if (prices.isPending)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading chart…
      </p>
    );
  if (!prices.data.some((bar) => bar.time === latest)) {
    return (
      <p className="text-sm text-muted-foreground">
        The evaluated candle could not be found in chart history.
      </p>
    );
  }

  const indicatorRows = new Map(indicators.data?.rows.map((row) => [row.t, row.values]));
  const data = prices.data.map((bar) => ({
    t: bar.time,
    open: bar.open,
    close: bar.close,
    range: [bar.low, bar.high] as [number, number],
    evaluated: bar.time === latest,
    ...Object.fromEntries(
      emaFields.map((key) => {
        const value = indicatorRows.get(bar.time)?.[key];
        return [key, value == null ? null : value / 100];
      }),
    ),
  }));
  const timeLabel = (value: number) =>
    report.mode === "swing"
      ? formatIstDate(value)
      : formatIstTime(new Date(value * 1000).toISOString());

  return (
    <section aria-label="Price chart" className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Price at evaluation</h3>
        <Link
          className="text-sm text-primary underline-offset-4 hover:underline"
          href={`/charts?symbol=${encodeURIComponent(report.symbol)}`}
        >
          Open full chart
        </Link>
      </div>
      <ChartContainer
        config={{ close: { label: "Close" } }}
        className="h-48 w-full aspect-auto sm:h-56"
        aria-label={`Adjusted candles through ${timeLabel(latest)}`}
      >
        <ComposedChart
          data={data}
          accessibilityLayer
          margin={{ left: 0, right: 10, top: 8, bottom: 0 }}
        >
          <CartesianGrid vertical={false} />
          <XAxis dataKey="t" tickFormatter={timeLabel} minTickGap={56} tickLine={false} />
          <YAxis
            domain={["auto", "auto"]}
            tickFormatter={(n: number) => formatDecimal(n)}
            width={58}
            tickLine={false}
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                labelFormatter={(t) => {
                  const timestamp = Number(t);
                  return Number.isFinite(timestamp) ? timeLabel(timestamp) : "";
                }}
                formatter={(_value, _name, entry) => {
                  const point = entry.payload as CandlePoint | undefined;
                  return point ? <span>Close · ₹{formatDecimal(point.close)}</span> : null;
                }}
              />
            }
          />
          <ReferenceLine x={latest} stroke="var(--muted-foreground)" strokeDasharray="3 3" />
          {levelPrice !== null && (
            <ReferenceLine
              y={levelPrice}
              ifOverflow="extendDomain"
              stroke="var(--chart-2)"
              strokeDasharray="6 3"
            />
          )}
          <Bar dataKey="range" shape={Candle} isAnimationActive={false} />
          {emaFields.map((key) => (
            <Line
              key={key}
              dataKey={key}
              stroke={EMA[key][1]}
              dot={false}
              strokeWidth={2}
              tooltipType="none"
              isAnimationActive={false}
            />
          ))}
        </ComposedChart>
      </ChartContainer>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>Evaluated candle · {timeLabel(latest)}</span>
        {levelLabel && levelPrice !== null && (
          <span>
            {levelLabel} · ₹{formatDecimal(levelPrice)}
          </span>
        )}
        {emaFields.map((key) => (
          <span key={key} style={{ color: EMA[key][1] }}>
            {EMA[key][0]}
          </span>
        ))}
        {indicators.error && <span>Moving-average history is unavailable.</span>}
      </p>
    </section>
  );
}
