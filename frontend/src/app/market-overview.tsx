"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  type ChartConfig,
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import { type ChartSymbol, parseSymbols } from "@/lib/charts";
import { formatIstDate, formatPrice, paisaToRupees } from "@/lib/format";
import { type DailyCloses, fetchDailyCloses, summarize } from "@/lib/market";
import { cn } from "@/lib/utils";

const SPARK_SESSIONS = 30;
const YEAR_SESSIONS = 250;

async function fetchIndices(): Promise<ChartSymbol[]> {
  const res = await fetch("/api/data/symbols", { cache: "no-store" });
  if (!res.ok) throw new Error(`symbols request failed: HTTP ${res.status}`);
  // NIFTY 50 first, then the other NSE indices, then BSE
  const rank = (s: ChartSymbol) => (s.symbol === "NSE:NIFTY 50" ? 0 : s.exchange === "NSE" ? 1 : 2);
  return parseSymbols(await res.json())
    .filter((s) => s.kind === "index")
    .sort((a, b) => rank(a) - rank(b) || a.tradingsymbol.localeCompare(b.tradingsymbol));
}

/** ▲ / ▼ / – with the colour: direction is never shown by colour alone. */
function Change({ change, bps, className }: { change: number; bps: number; className?: string }) {
  const dir = change > 0 ? "up" : change < 0 ? "down" : "flat";
  const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "–";
  const tone = dir === "up" ? "text-gain" : dir === "down" ? "text-loss" : "text-muted-foreground";
  return (
    <span className={cn("tabular-nums", tone, className)}>
      {arrow} {formatPrice(Math.abs(change))} ({(Math.abs(bps) / 100).toFixed(2)}%)
    </span>
  );
}

function Sparkline({ data, up, id }: { data: DailyCloses; up: boolean; id: string }) {
  const points = data.closes.map((c, i) => ({ t: data.times[i], v: paisaToRupees(c) }));
  const colour = up ? "var(--gain)" : "var(--loss)";
  return (
    <AreaChart width={120} height={40} data={points} aria-hidden>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={colour} stopOpacity={0.35} />
          <stop offset="100%" stopColor={colour} stopOpacity={0} />
        </linearGradient>
      </defs>
      <YAxis hide domain={["dataMin", "dataMax"]} />
      <Area
        type="monotone"
        dataKey="v"
        stroke={colour}
        strokeWidth={1.5}
        fill={`url(#${id})`}
        isAnimationActive={false}
      />
    </AreaChart>
  );
}

function IndexCard({
  index,
  data,
  error,
  selected,
  onSelect,
}: {
  index: ChartSymbol;
  data: DailyCloses | undefined;
  error: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const s = data ? summarize(data) : null;
  const windowUp = data && data.closes.length > 1 ? data.closes.at(-1)! >= data.closes[0]! : true;
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-xl border bg-card p-4 text-left text-card-foreground shadow-xs transition-colors hover:border-ring/60",
        selected && "border-primary ring-2 ring-primary/30",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium">{index.tradingsymbol}</div>
          <div className="text-xs text-muted-foreground">{index.exchange}</div>
        </div>
        {data && data.closes.length > 1 && (
          <Sparkline data={data} up={windowUp} id={`spark-${index.symbol.replace(/\W/g, "")}`} />
        )}
      </div>
      {error ? (
        <p className="text-sm text-loss">✕ couldn&apos;t load</p>
      ) : !data ? (
        <Skeleton className="h-10 w-full" />
      ) : s === null ? (
        <p className="text-sm text-muted-foreground">Not enough data</p>
      ) : (
        <>
          <div className="text-xl font-semibold tabular-nums">{formatPrice(s.last)}</div>
          <div className="flex flex-wrap items-baseline justify-between gap-x-2 text-sm">
            <Change change={s.change} bps={s.changeBps} />
            <span className="text-xs text-muted-foreground">close {formatIstDate(s.lastTime)}</span>
          </div>
        </>
      )}
    </button>
  );
}

const YEAR_CONFIG = { v: { label: "Close", color: "var(--chart-1)" } } satisfies ChartConfig;

function YearChart({ index }: { index: ChartSymbol }) {
  const q = useQuery({
    queryKey: ["closes", index.symbol, YEAR_SESSIONS],
    queryFn: () => fetchDailyCloses(index.symbol, YEAR_SESSIONS),
  });
  const s = q.data ? summarize(q.data) : null;
  const first = q.data?.closes[0];
  const yearChange = q.data && s && first ? s.last - first : null;
  const yearBps = yearChange !== null && first ? Math.round((yearChange * 10_000) / first) : 0;
  const points = (q.data?.closes ?? []).map((c, i) => ({
    t: q.data!.times[i]!,
    v: paisaToRupees(c),
  }));
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>
          {index.tradingsymbol} · last {YEAR_SESSIONS} sessions
        </CardTitle>
        <CardDescription>
          Daily closes from FinTrack&apos;s stored Kite data
          {q.data && ` (${q.data.source})`}
        </CardDescription>
        <CardAction>
          <Link
            href={`/charts?symbol=${encodeURIComponent(index.symbol)}`}
            className="text-sm text-primary hover:underline"
          >
            Open chart →
          </Link>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {q.error ? (
          <p role="alert" className="text-loss">
            ✕ Couldn&apos;t load {index.tradingsymbol}: {q.error.message}
          </p>
        ) : !q.data ? (
          <Skeleton className="h-72 w-full" />
        ) : (
          <>
            {s && yearChange !== null && (
              <div className="flex flex-wrap gap-x-8 gap-y-1 text-sm">
                <div>
                  <div className="text-muted-foreground">Last close</div>
                  <div className="text-lg font-semibold tabular-nums">{formatPrice(s.last)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">Over the period</div>
                  <Change change={yearChange} bps={yearBps} className="text-lg font-semibold" />
                </div>
              </div>
            )}
            <ChartContainer config={YEAR_CONFIG} className="aspect-auto h-72 w-full">
              <AreaChart data={points} margin={{ left: 4, right: 8 }}>
                <defs>
                  <linearGradient id="year-fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--color-v)" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="var(--color-v)" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid vertical={false} />
                <XAxis
                  dataKey="t"
                  tickLine={false}
                  axisLine={false}
                  minTickGap={48}
                  tickFormatter={(t: number) => formatIstDate(t).replace(/^\d+ /, "")}
                />
                <YAxis
                  domain={["auto", "auto"]}
                  tickLine={false}
                  axisLine={false}
                  width={64}
                  tickFormatter={(v: number) =>
                    v.toLocaleString("en-IN", { maximumFractionDigits: 0 })
                  }
                />
                <ChartTooltip
                  content={
                    <ChartTooltipContent
                      labelFormatter={(_, p) => {
                        const t = p?.[0]?.payload?.t;
                        return typeof t === "number" ? formatIstDate(t) : "";
                      }}
                    />
                  }
                />
                <Area
                  type="monotone"
                  dataKey="v"
                  stroke="var(--color-v)"
                  strokeWidth={2}
                  fill="url(#year-fill)"
                  isAnimationActive={false}
                />
              </AreaChart>
            </ChartContainer>
          </>
        )}
      </CardContent>
    </Card>
  );
}

/** Indices at a glance (real stored closes only) and a larger view of the chosen one. */
export function MarketOverview() {
  const indices = useQuery({ queryKey: ["indices"], queryFn: fetchIndices, staleTime: 300_000 });
  const list = indices.data ?? [];
  const sparks = useQueries({
    queries: list.map((ix) => ({
      queryKey: ["closes", ix.symbol, SPARK_SESSIONS],
      queryFn: () => fetchDailyCloses(ix.symbol, SPARK_SESSIONS),
      staleTime: 300_000,
    })),
  });
  const [chosen, setChosen] = useState<string | null>(null);
  const selected = list.find((x) => x.symbol === chosen) ?? list[0];
  if (indices.error) {
    return (
      <p role="alert" className="text-loss">
        ✕ Couldn&apos;t load the index list from the backend.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline gap-3">
        <h2 className="text-lg font-semibold">Indices</h2>
        <Badge variant="outline" className="text-muted-foreground">
          last stored daily close · not live
        </Badge>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        {indices.isPending
          ? Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-32 rounded-xl" />)
          : list.map((ix, i) => (
              <IndexCard
                key={ix.symbol}
                index={ix}
                data={sparks[i]?.data}
                error={!!sparks[i]?.error}
                selected={ix.symbol === selected?.symbol}
                onSelect={() => setChosen(ix.symbol)}
              />
            ))}
      </div>
      {selected && <YearChart index={selected} />}
    </div>
  );
}
