"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ListFilter, Search } from "lucide-react";
import Link from "next/link";
import { Tabs } from "radix-ui";
import { type FormEvent, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { fetchSymbols } from "@/lib/charts";
import {
  formatDecimal,
  formatInt,
  formatIstDate,
  formatIstDateTime,
  formatPct,
  formatRupees,
} from "@/lib/format";
import {
  type Candidate,
  type CandidateInstrument,
  type CandidateMode,
  type CandidateReport,
  fetchCandidates,
} from "@/lib/scoring";

import { SetupChart } from "./setup-chart";
import { TradePlanPanel } from "./trade-plan";
import { UniverseScan } from "./universe-scan";

const STATUS = {
  matched: ["Qualified", "text-gain"],
  no_match: ["No match", "text-muted-foreground"],
  unavailable: ["Data unavailable", "text-warn"],
  disabled: ["Disabled", "text-muted-foreground"],
} as const;

const INSTRUMENTS: Record<CandidateInstrument, string> = {
  underlying: "Stock or index",
  future: "Futures",
  option: "Options",
};

const VALUE_LABELS: Record<string, string> = {
  close_paisa: "The close",
  prior_252_high_paisa: "the prior 52-week high",
  prior_252_low_paisa: "the prior 52-week low",
  opening_range_high_paisa: "the opening-range high",
  opening_range_low_paisa: "the opening-range low",
  ema_50_paisa: "EMA 50",
  ema_200_paisa: "EMA 200",
  ema_9_paisa: "EMA 9",
  ema_21_paisa: "EMA 21",
  rsi_pct: "RSI",
  vwap_paisa: "session VWAP",
  spread_bps: "The bid–ask spread",
  iv_pct: "Implied volatility",
  oi: "Open interest",
  futures_oi: "Futures open interest",
  futures_volume: "Futures volume",
  days_to_expiry: "Time to expiry",
};

const COMPARISONS: Record<string, string> = {
  ">": "above",
  "<": "below",
  ">=": "at or above",
  "<=": "at or below",
};

const INITIAL = {
  symbol: "NSE:RELIANCE",
  mode: "swing" as CandidateMode,
  instrument: "underlying" as CandidateInstrument,
};

function display(value: number | null, field: string): string {
  if (value === null) return "Unavailable";
  if (field.endsWith("_paisa") && Number.isInteger(value)) return formatRupees(value);
  if (field.endsWith("_pct")) return formatPct(value, 2);
  if (field.endsWith("_bps")) return `${formatDecimal(value)} bps`;
  return formatDecimal(value);
}

function time(value: number, mode: CandidateMode): string {
  return mode === "swing"
    ? formatIstDate(value)
    : formatIstDateTime(new Date(value * 1000).toISOString());
}

function direction(item: Candidate): string {
  return item.direction === "long" ? "Bullish" : "Bearish";
}

function explanation(item: Candidate): string {
  if (item.status === "disabled") return "Short strategies are disabled.";
  if (item.reason) return `${item.reason.replace(/\.$/, "")}.`;
  const rules = item.evidence;
  const current = rules.filter((rule) => !rule.previous);
  const focus = (kind: Candidate["evidence"][number]["result"]) =>
    current.find((rule) => rule.result === kind) ?? rules.find((rule) => rule.result === kind);
  if (item.status === "unavailable") {
    const missing = focus("unavailable");
    return missing
      ? `There’s insufficient data to evaluate this rule: ${missing.label}.`
      : "Required data is unavailable.";
  }
  const key = item.status === "matched" ? focus("pass") : focus("fail");
  if (!key || key.observed === null || key.threshold === null) return item.name;
  const label = VALUE_LABELS[key.field];
  const value = display(key.observed, key.field);
  const boundary = display(key.threshold, key.thresholdField ?? key.field);
  const thresholdLabel = key.thresholdField && VALUE_LABELS[key.thresholdField];
  const relation =
    key.observed > key.threshold ? "above" : key.observed < key.threshold ? "below" : "equal to";
  const tested = thresholdLabel ? `${thresholdLabel} of ${boundary}` : boundary;
  if (label && !key.previous) {
    return `${label} was ${value}, ${relation} ${tested}. ${item.status === "matched" ? "The setup qualified." : `This rule needs it ${COMPARISONS[key.comparator] ?? key.comparator} ${boundary}.`}`;
  }
  return `${key.label} · ${value} against a threshold of ${boundary}. ${item.status === "matched" ? "The setup qualified." : "This rule did not pass."}`;
}

export function CandidateWorkspace() {
  const [tab, setTab] = useState("scan");
  const [symbol, setSymbol] = useState(INITIAL.symbol);
  const [selected, select] = useState(INITIAL);
  const [picked, setPicked] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const symbols = useQuery({ queryKey: ["symbols"], queryFn: fetchSymbols, staleTime: 300_000 });
  const query = useQuery({
    queryKey: ["candidates", selected],
    queryFn: ({ signal }) =>
      fetchCandidates(selected.symbol, selected.mode, selected.instrument, signal),
    retry: false,
  });

  function change(next: typeof INITIAL) {
    setPicked(null);
    setDiagnostics(false);
    select(next);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = symbol.trim().toUpperCase();
    if (!input) return;
    const normalized = input.includes(":") ? input : `NSE:${input}`;
    if (normalized === selected.symbol) void query.refetch();
    else change({ ...selected, symbol: normalized });
  }

  function inspect(item: Candidate) {
    setPicked(item.id);
    if (window.matchMedia("(max-width: 1023px)").matches) {
      requestAnimationFrame(() => {
        panel.current?.focus({ preventScroll: true });
        panel.current?.scrollIntoView({ block: "start" });
      });
    }
  }

  const result = query.data;
  const matches = result?.evaluations.filter((item) => item.status === "matched") ?? [];
  const checks = result?.evaluations.filter((item) => item.status !== "matched") ?? [];
  const active = result?.evaluations.find((item) => item.id === picked) ?? matches[0];
  const field = "h-9 w-full rounded-md border border-border bg-background px-3 text-sm";
  const row = (item: Candidate) => (
    <li key={item.id}>
      <button
        type="button"
        onClick={() => inspect(item)}
        aria-pressed={active?.id === item.id}
        className="flex w-full items-center gap-2 border-l-2 border-transparent px-3 py-2.5 text-left hover:bg-muted/60 aria-pressed:border-primary aria-pressed:bg-primary/8"
      >
        <span className="min-w-0 flex-1">
          <span className="block font-medium">{item.name}</span>
          <span className="mt-0.5 block text-xs text-muted-foreground">
            {direction(item)} · {item.contract?.type ?? INSTRUMENTS[selected.instrument]}
          </span>
        </span>
        {item.status !== "matched" && (
          <span className={`text-xs ${STATUS[item.status][1]}`}>{STATUS[item.status][0]}</span>
        )}
        <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
      </button>
    </li>
  );

  return (
    <Tabs.Root value={tab} onValueChange={setTab} className="space-y-4">
      <Tabs.List
        aria-label="Candidate research"
        className="flex w-fit gap-1 rounded-lg bg-muted p-1"
      >
        <Tabs.Trigger
          value="scan"
          className="rounded-md px-4 py-2 text-sm font-medium text-muted-foreground data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-xs"
        >
          Scan stocks
        </Tabs.Trigger>
        <Tabs.Trigger
          value="evaluate"
          className="rounded-md px-4 py-2 text-sm font-medium text-muted-foreground data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-xs"
        >
          Evaluate a symbol
        </Tabs.Trigger>
      </Tabs.List>
      <Tabs.Content forceMount value="scan" className="data-[state=inactive]:hidden">
        <UniverseScan
          onSelect={(ticker, mode) => {
            setSymbol(ticker);
            change({ symbol: ticker, mode, instrument: "underlying" });
            setTab("evaluate");
          }}
        />
      </Tabs.Content>
      <Tabs.Content forceMount value="evaluate" className="space-y-4 data-[state=inactive]:hidden">
        <div className="flex flex-wrap items-end justify-between gap-4 border-b pb-4">
          <div className="flex gap-1 rounded-lg bg-muted p-1" role="group" aria-label="Pipeline">
            {(["swing", "intraday"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={selected.mode === mode}
                onClick={() => change({ ...selected, mode })}
                className="rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground aria-pressed:bg-background aria-pressed:text-foreground aria-pressed:shadow-xs"
              >
                {mode === "swing" ? "Swing" : "Intraday"}
              </button>
            ))}
          </div>
          <form
            onSubmit={submit}
            className="flex flex-1 flex-wrap items-end justify-end gap-2 sm:flex-none"
          >
            <label className="min-w-40 flex-1 text-xs font-medium">
              <span className="mb-1 block">Symbol</span>
              <input
                className={field}
                value={symbol}
                list="candidate-symbols"
                onChange={(e) => setSymbol(e.target.value)}
                required
                maxLength={80}
              />
              <datalist id="candidate-symbols">
                {symbols.data?.map((item) => (
                  <option key={item.symbol} value={item.symbol} />
                ))}
              </datalist>
            </label>
            <label className="text-xs font-medium">
              <span className="mb-1 block">Instrument</span>
              <select
                className={field}
                value={selected.instrument}
                onChange={(e) =>
                  change({ ...selected, instrument: e.target.value as CandidateInstrument })
                }
              >
                {Object.entries(INSTRUMENTS).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <Button type="submit">
              <Search aria-hidden className="size-4" /> Evaluate
            </Button>
          </form>
        </div>
        {query.isFetching && (
          <p role="status" className="text-sm text-muted-foreground">
            Evaluating stored data…
          </p>
        )}
        {query.error && (
          <p role="alert" className="text-sm text-loss">
            {query.error.message}
          </p>
        )}
        {result && (
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(16rem,19rem)_minmax(0,1fr)]">
            <section
              aria-label="Candidate results"
              className="overflow-hidden rounded-xl border bg-card"
            >
              <header className="border-b px-4 py-3">
                <h2 className="font-semibold">{result.symbol.replace(/^.*:/, "")}</h2>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {result.interval === "D" ? "Daily" : "5-minute"} ·{" "}
                  {result.latestBar === null
                    ? "No stored candle"
                    : time(result.latestBar, result.mode)}
                </p>
              </header>
              <div className="border-b px-4 py-2.5 text-xs font-medium">
                {matches.length
                  ? `${matches.length} qualified ${matches.length === 1 ? "setup" : "setups"}`
                  : "No setups qualified"}
              </div>
              {matches.length > 0 && <ul className="divide-y">{matches.map(row)}</ul>}
              {checks.length > 0 && (
                <>
                  <button
                    type="button"
                    aria-expanded={diagnostics}
                    aria-controls="candidate-checks"
                    onClick={() => setDiagnostics(!diagnostics)}
                    className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm text-muted-foreground hover:bg-muted/60"
                  >
                    <ListFilter aria-hidden className="size-4" /> Strategy checks{" "}
                    <span className="ml-auto">{checks.length}</span>
                  </button>
                  <ul id="candidate-checks" hidden={!diagnostics} className="divide-y border-t">
                    {checks.map(row)}
                  </ul>
                </>
              )}
            </section>
            <section
              ref={panel}
              tabIndex={-1}
              aria-label="Setup explanation"
              className="min-w-0 scroll-mt-28 rounded-xl border bg-card outline-none"
            >
              {active ? (
                <CandidateDetail key={active.id} item={active} report={result} />
              ) : (
                <div className="px-5 py-6 sm:px-6">
                  <h2 className="text-lg font-semibold">No setups qualified</h2>
                  <p className="mt-2 text-sm text-muted-foreground">
                    Open Strategy checks and select a setup to see the rule values and the evaluated
                    candle.
                  </p>
                  <DataNotes report={result} />
                </div>
              )}
            </section>
          </div>
        )}
      </Tabs.Content>
    </Tabs.Root>
  );
}

function DataNotes({ report }: { report: CandidateReport }) {
  const client = useQueryClient();
  const [token, setToken] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function reviewNews() {
    setPending(true);
    setMessage(null);
    try {
      const query = new URLSearchParams({ symbol: report.symbol });
      const response = await fetch(`/api/scoring/news?${query}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token.trim()}` },
      });
      if (!response.ok) throw new Error(`News review failed (${response.status}).`);
      const result: unknown = await response.json();
      if (!result || typeof result !== "object" || !("status" in result))
        throw new Error("Invalid news review response.");
      setMessage(result.status === "classified" ? "Review complete." : "News remains unknown.");
      setToken("");
      await client.invalidateQueries({ queryKey: ["candidates"] });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "News review failed.");
    } finally {
      setPending(false);
    }
  }
  return (
    <details className="mt-4 border-t pt-3 text-xs text-muted-foreground">
      <summary className="cursor-pointer font-medium text-foreground">Evaluation data</summary>
      <div className="mt-2 space-y-1">
        <p>
          {report.source} · Evaluated {formatIstDateTime(report.asOf)}
        </p>
        <div className="flex flex-wrap items-end gap-2 pt-1">
          <label className="grid gap-1">
            Admin API token
            <input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="h-8 rounded-md border bg-background px-2"
            />
          </label>
          <Button
            size="sm"
            variant="outline"
            disabled={!token.trim() || pending}
            onClick={() => void reviewNews()}
          >
            {pending ? "Reviewing…" : "Review current news"}
          </Button>
        </div>
        {message && <p role="status">{message}</p>}
        {report.dataStatus !== "ok" && (
          <p>
            Data status · {report.dataStatus.replaceAll("_", " ")}
            {report.benchmarkSession ? ` · Benchmark session ${report.benchmarkSession}` : ""}
          </p>
        )}
        <p>
          News · {report.news.status === "unknown" ? "Unknown · " : ""}
          {report.news.message}
          {report.news.material && " · Material event flagged"}
        </p>
        {report.news.sources.length > 0 && (
          <ul className="list-inside list-disc">
            {report.news.sources.map((source) => (
              <li key={source.link}>
                <a
                  href={source.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline"
                >
                  {source.title}
                </a>
              </li>
            ))}
          </ul>
        )}
        {report.fundamentals && (
          <p>
            NSE market cap · {display(report.fundamentals.marketCap, "market_cap_paisa")} ·{" "}
            {report.fundamentals.tradeDate}
          </p>
        )}
      </div>
    </details>
  );
}

function CandidateDetail({ item, report }: { item: Candidate; report: CandidateReport }) {
  const [status, statusColor] = STATUS[item.status];
  const { conviction, contract } = item;
  return (
    <div className="space-y-5 p-4 sm:p-5">
      <header>
        <p className={`text-xs font-semibold ${statusColor}`}>{status}</p>
        <h2 className="mt-1 text-xl font-semibold tracking-tight">{item.name}</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {report.symbol} · {direction(item)}
        </p>
        <p className="mt-3 text-sm leading-relaxed">{explanation(item)}</p>
        {item.invalidation && (
          <p className="mt-2 text-sm text-muted-foreground">
            <strong className="text-foreground">Invalidation</strong> · {item.invalidation}
          </p>
        )}
      </header>
      {item.status === "matched" && <TradePlanPanel plan={item.tradePlan} />}
      {item.status === "matched" && item.tradePlan?.status === "draft" && !item.contract && (
        <Button asChild size="sm" variant="outline">
          <Link href={`/paper?symbol=${encodeURIComponent(report.symbol)}`}>
            Open paper workspace
          </Link>
        </Button>
      )}
      <SetupChart item={item} report={report} />
      {contract && (
        <section aria-label="Contract details" className="border-t pt-3">
          <h3 className="text-sm font-semibold">Contract details</h3>
          <p className="mt-1 text-sm">
            {contract.type} · Expiry {contract.expiry}
            {contract.strike !== null && ` · Strike ${formatRupees(contract.strike)}`}
            {contract.bid !== null &&
              contract.ask !== null &&
              ` · Bid ${formatRupees(contract.bid)} / ask ${formatRupees(contract.ask)}`}
            {contract.close !== null && ` · Close ${formatRupees(contract.close)}`}
            {contract.oi !== null && ` · OI ${formatInt(contract.oi)}`}
            {contract.iv !== null && ` · IV ${formatPct(contract.iv, 2)}`}
          </p>
          {(contract.snapshotAt || contract.tradeDate) && (
            <p className="mt-1 text-xs text-muted-foreground">
              {contract.snapshotAt
                ? `Captured ${formatIstDateTime(contract.snapshotAt)}`
                : `NSE session ${contract.tradeDate}`}
            </p>
          )}
        </section>
      )}
      <details className="border-t pt-3">
        <summary className="cursor-pointer text-sm font-semibold">
          Rule evidence{" "}
          <span className="font-normal text-muted-foreground">· {item.evidence.length}</span>
        </summary>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[25rem] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="p-2">Rule</th>
                <th className="p-2">Observed</th>
                <th className="p-2">Required</th>
                <th className="p-2">Result</th>
              </tr>
            </thead>
            <tbody>
              {item.evidence.map((e, index) => (
                <tr key={`${e.field}-${index}`} className="border-t">
                  <td className="p-2">
                    {e.label}
                    {e.previous && report.previousBar !== null && (
                      <span className="block text-muted-foreground">
                        {time(report.previousBar, report.mode)}
                      </span>
                    )}
                  </td>
                  <td className="p-2 tabular-nums">{display(e.observed, e.field)}</td>
                  <td className="p-2 tabular-nums">
                    {e.comparator} {display(e.threshold, e.thresholdField ?? e.field)}
                  </td>
                  <td
                    className={`p-2 ${e.result === "pass" ? "text-gain" : e.result === "unavailable" ? "text-warn" : "text-muted-foreground"}`}
                  >
                    {e.result === "pass" ? "Pass" : e.result === "fail" ? "Fail" : "Unavailable"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <section className="border-t pt-3" aria-label="Historical outcomes">
        <h3 className="text-sm font-semibold">Historical outcomes</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {conviction.probability === null
            ? "Uncalibrated"
            : `${formatPct(conviction.probability)} historical win rate`}
          {item.status === "matched" &&
            ` · ${formatInt(conviction.observations ?? 0)} evaluated outcomes`}
          {conviction.probability === null &&
            item.status === "matched" &&
            ` · Minimum ${formatInt(conviction.minimum ?? 0)}`}
          {conviction.evaluatedThrough &&
            ` · Evaluated through ${formatIstDateTime(conviction.evaluatedThrough)}`}
        </p>
      </section>
      <DataNotes report={report} />
    </div>
  );
}
