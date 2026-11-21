"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type ReactNode, useState } from "react";

import { formatInt, formatPct, formatRupees } from "@/lib/format";
import { FLAG_LABELS, label, QUALITY_TEXT, QUARANTINE_REASONS } from "@/lib/labels";
import { describeAction, fetchStock, type KiteStatus, type Stock } from "@/lib/stock";

// Kite's measured prices vs one NSE action — an icon AND a colour, never colour alone.
const KITE_TEXT: Record<KiteStatus, { text: string; className: string }> = {
  matched: { text: "✓ Kite adjusted to match", className: "text-gain" },
  cant_check: { text: "• Kite changed; amount can't be checked", className: "text-foreground/80" },
  mismatch: { text: "✕ Kite's change doesn't match", className: "text-loss" },
  no_change: { text: "– no change in Kite's prices", className: "text-muted-foreground" },
  not_measured: { text: "? not enough data to check", className: "text-warn" },
};

const STEP_TEXT: Record<string, { text: string; className: string }> = {
  corroborated: { text: "✓ confirmed by NSE prices", className: "text-primary" },
  suspect: { text: "✕ suspect (not used)", className: "text-loss" },
  unexplained_small: { text: "• small, no reason", className: "text-warn" },
  official_mismatch: { text: "✕ latest bars differ from official", className: "text-loss" },
};

type ActionFilter = "all" | "price" | "dividend" | "other";
const FILTERS: { id: ActionFilter; text: string }[] = [
  { id: "all", text: "All" },
  { id: "price", text: "Splits & bonuses" },
  { id: "dividend", text: "Dividends" },
  { id: "other", text: "Other" },
];

const COMING_WITH_1E = [
  "Delivery %",
  "Company announcements",
  "Board meetings and results dates",
  "Bulk and block deals",
  "Market cap",
];

export function StockView() {
  const symbol = useSearchParams().get("symbol");
  return (
    <div className="flex flex-col gap-4">
      {symbol ? (
        <StockDetail symbol={symbol} />
      ) : (
        <p className="text-muted-foreground">
          No stock chosen yet. Use the search bar at the top of the page.
        </p>
      )}
    </div>
  );
}

function Section({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return (
    <section
      aria-label={title}
      className="min-w-0 rounded-xl border bg-card p-4 text-card-foreground shadow-xs"
    >
      <h2 className="mb-1 text-base font-medium">{title}</h2>
      {note && <p className="mb-3 text-xs text-muted-foreground">{note}</p>}
      {children}
    </section>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{k}</dt>
      <dd>{children}</dd>
    </>
  );
}

function StockDetail({ symbol }: { symbol: string }) {
  const q = useQuery({ queryKey: ["stock", symbol], queryFn: () => fetchStock(symbol) });
  if (q.isPending) return <p className="text-muted-foreground">Loading {symbol}…</p>;
  if (q.error) {
    return (
      <p role="alert" className="text-loss">
        ✕ Couldn&apos;t read {symbol}: {q.error.message}. Nothing is shown rather than a guess.
      </p>
    );
  }
  const s = q.data;
  return (
    <div className="flex flex-col gap-4">
      <Header s={s} />
      <div className="grid gap-4 xl:grid-cols-3">
        <Basics s={s} />
        <DailyHealthCard s={s} />
        <MinuteHealthCard s={s} />
      </div>
      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <Actions s={s} />
        <div className="flex flex-col gap-4">
          <Names s={s} />
          <AsTraded s={s} />
          <Section title="Coming with slice 1e" note="Not loaded yet — nothing to show.">
            <ul className="list-inside list-disc text-sm text-muted-foreground">
              {COMING_WITH_1E.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </Section>
        </div>
      </div>
      <UnlistedSteps s={s} />
    </div>
  );
}

function Header({ s }: { s: Stock }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
      <h2 className="text-xl font-semibold">{s.tradingsymbol}</h2>
      <span className="text-muted-foreground">{s.name ?? "name not in Kite's list"}</span>
      <span className="rounded border border-border px-2 py-0.5 text-xs text-foreground/80">
        {s.exchange} · {s.kind === "index" ? "index" : "stock"}
        {!s.active && " · no longer in the universe"}
      </span>
      <Link
        href={`/charts?symbol=${encodeURIComponent(s.symbol)}`}
        className="text-sm text-primary hover:underline"
      >
        Open chart →
      </Link>
    </div>
  );
}

function Basics({ s }: { s: Stock }) {
  return (
    <Section title="Basics">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
        <Row k="Symbol">{s.symbol}</Row>
        <Row k="Tick size">{s.tickSizePaisa === null ? "—" : formatRupees(s.tickSizePaisa)}</Row>
        <Row k="F&O">
          {s.futures
            ? `yes · lot size ${formatInt(s.futures.lotSize)} (contract expiring ${s.futures.expiry})`
            : s.kind === "index"
              ? "— (index)"
              : "no live futures contract found"}
        </Row>
        <Row k="Earlier names">
          {s.names.length === 0 ? "none" : s.names.map((n) => n.old).join(", ")}
        </Row>
      </dl>
    </Section>
  );
}

function Counts({
  data,
  map,
  empty,
}: {
  data: Record<string, number>;
  map: Record<string, string>;
  empty: string;
}) {
  const entries = Object.entries(data);
  if (entries.length === 0) return <span className="text-muted-foreground">{empty}</span>;
  return (
    <ul>
      {entries.map(([k, n]) => (
        <li key={k}>
          {formatInt(n)} · {label(map, k)}
        </li>
      ))}
    </ul>
  );
}

function DailyHealthCard({ s }: { s: Stock }) {
  const c = s.daily.coverage;
  return (
    <Section title="Daily candles (Kite)">
      {c === null ? (
        <p className="text-muted-foreground">No daily data stored.</p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
          <Row k="Range">
            {c.oldest ?? "—"} → {c.newest ?? "—"}
          </Row>
          <Row k="Clean bars">{formatInt(c.cleanBars)}</Row>
          <Row k="Missing weekdays">
            {c.weekdayGapPct === null ? "—" : formatPct(c.weekdayGapPct, 2)}{" "}
            <span className="text-muted-foreground">(holidays alone are about 5%)</span>
          </Row>
          <Row k="Enough for analysis">
            {c.sufficient ? (
              <span className="text-gain">✓ yes</span>
            ) : (
              <span className="text-loss">✕ no — {c.insufficientReason}</span>
            )}
          </Row>
          <Row k="Up to date">
            {c.fresh ? (
              <span className="text-gain">✓ yes</span>
            ) : (
              <span className="text-warn">▼ {c.staleWeekdays ?? "?"} trading days behind</span>
            )}
          </Row>
          {c.suspendedWeekdays > 0 && (
            <Row k="Suspended">
              {formatInt(c.suspendedWeekdays)} weekdays (NSE didn&apos;t trade it)
            </Row>
          )}
          {c.preListingBars > 0 && (
            <Row k="Before listing">{formatInt(c.preListingBars)} bars (not counted)</Row>
          )}
          <Row k="Rejected rows">
            <Counts data={s.daily.quarantined} map={QUARANTINE_REASONS} empty="none" />
          </Row>
          <Row k="Flagged rows">
            <Counts data={s.daily.flagged} map={FLAG_LABELS} empty="none" />
          </Row>
        </dl>
      )}
    </Section>
  );
}

function MinuteHealthCard({ s }: { s: Stock }) {
  const c = s.minute.coverage;
  return (
    <Section title="1-minute candles (Kite)">
      {c === null ? (
        <p className="text-muted-foreground">No 1-minute data stored.</p>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
          <Row k="Range">
            {c.first} → {c.last}
          </Row>
          <Row k="Analysis starts">
            {s.minute.analysisStart}{" "}
            <span className="text-muted-foreground">(older days: stored, opt-in only)</span>
          </Row>
          <Row k="Bars">
            {formatInt(c.bars)} in {formatInt(c.sessions)} sessions
          </Row>
          <Row k="Complete sessions">{formatPct(c.completePct, 2)}</Row>
          <Row k="Short · extra · missing">
            <span className={c.short + c.excess + c.missingSessions > 0 ? "text-warn" : ""}>
              {formatInt(c.short)} · {formatInt(c.excess)} · {formatInt(c.missingSessions)}
            </span>
          </Row>
          <Row k="Market-wide short days">
            {formatInt(c.marketShort)} (not this stock&apos;s fault)
          </Row>
          {c.notTradedSessions > 0 && (
            <Row k="Not traded (NSE)">{formatInt(c.notTradedSessions)} days</Row>
          )}
          <Row k="Rejected rows">
            <Counts data={s.minute.quarantined} map={QUARANTINE_REASONS} empty="none" />
          </Row>
        </dl>
      )}
    </Section>
  );
}

function matchesFilter(kinds: string[], f: ActionFilter): boolean {
  if (f === "all") return true;
  if (f === "price") return kinds.includes("split") || kinds.includes("bonus");
  if (f === "dividend") return kinds.includes("dividend");
  return !kinds.some((k) => k === "split" || k === "bonus" || k === "dividend");
}

function Actions({ s }: { s: Stock }) {
  const [filter, setFilter] = useState<ActionFilter>("all");
  const rows = s.actions.filter((a) => matchesFilter(a.kinds, filter));
  return (
    <Section
      title={`Corporate actions (${formatInt(s.actions.length)})`}
      note="From NSE's archive, newest first, for the shares only. The right column is what Kite's prices measurably did on that date."
    >
      {s.kind === "index" ? (
        <p className="text-muted-foreground">Indices have no corporate actions.</p>
      ) : (
        <>
          <div
            role="radiogroup"
            aria-label="Filter actions"
            className="mb-3 flex flex-wrap gap-2 text-xs"
          >
            {FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                role="radio"
                aria-checked={filter === f.id}
                onClick={() => setFilter(f.id)}
                className={`rounded border px-2 py-1 ${filter === f.id ? "border-ring bg-accent text-foreground" : "border-border text-foreground/80 hover:bg-muted"}`}
              >
                {f.text}
              </button>
            ))}
          </div>
          {rows.length === 0 ? (
            <p className="text-muted-foreground">None.</p>
          ) : (
            <div className="max-h-[32rem] overflow-auto">
              <table className="w-full min-w-[40rem] text-left text-sm">
                <thead className="sticky top-0 bg-card text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-3 font-normal">Ex-date</th>
                    <th className="py-1 pr-3 font-normal">What</th>
                    <th className="py-1 pr-3 font-normal">Kite&apos;s prices</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((a) => (
                    <tr
                      key={`${a.exDate}-${a.purpose}`}
                      className="border-t border-border align-top"
                    >
                      <td className="py-1 pr-3 whitespace-nowrap tabular-nums">{a.exDate}</td>
                      <td className="py-1 pr-3">
                        {describeAction(a)}
                        <div className="text-xs text-muted-foreground">NSE: {a.purpose}</div>
                      </td>
                      <td className={`py-1 pr-3 ${KITE_TEXT[a.kite].className}`}>
                        {KITE_TEXT[a.kite].text}
                        {a.detail && (
                          <details className="text-xs text-muted-foreground">
                            <summary className="cursor-pointer">how it was measured</summary>
                            {a.detail}
                          </details>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {s.otherSecurityActions > 0 && (
            <p className="mt-2 text-xs text-muted-foreground">
              {formatInt(s.otherSecurityActions)} more NSE entries are for this company&apos;s other
              securities (bonds, preference shares): not shown, and never used for share prices.
            </p>
          )}
        </>
      )}
    </Section>
  );
}

function Names({ s }: { s: Stock }) {
  return (
    <Section title="Name changes" note="From NSE's symbol-change list, newest first.">
      {s.names.length === 0 ? (
        <p className="text-sm text-muted-foreground">No earlier names.</p>
      ) : (
        <ul className="text-sm">
          {s.names.map((n) => (
            <li key={`${n.old}-${n.date}`}>
              <span className="tabular-nums text-muted-foreground">{n.date}</span> · {n.old} →{" "}
              {n.new}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function AsTraded({ s }: { s: Stock }) {
  const entries = Object.entries(s.asTradedDays);
  return (
    <Section
      title="Real traded prices"
      note="Days where we could work out the real traded price from Kite's adjusted one."
    >
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">Not measured yet.</p>
      ) : (
        <ul className="text-sm">
          {entries.map(([k, n]) => (
            <li key={k}>
              {formatInt(n)} · {label(QUALITY_TEXT, k)}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function UnlistedSteps({ s }: { s: Stock }) {
  const [showSmall, setShowSmall] = useState(false);
  const small = s.unlistedSteps.filter((x) => x.small).length;
  const rows = s.unlistedSteps.filter((x) => showSmall || !x.small);
  if (s.kind === "index") return null;
  return (
    <Section
      title="Price changes NSE didn't list"
      note="Dates where Kite's prices stepped but NSE's action list has nothing."
    >
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">None{small > 0 ? " that matter" : ""}.</p>
      ) : (
        <div className="max-h-[24rem] overflow-auto">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <tbody>
              {rows.map((x) => {
                const t = STEP_TEXT[x.cls] ?? { text: x.cls, className: "" };
                return (
                  <tr key={x.date} className="border-t border-border align-top">
                    <td className="py-1 pr-3 whitespace-nowrap tabular-nums">{x.date}</td>
                    <td className={`py-1 pr-3 whitespace-nowrap ${t.className}`}>{t.text}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">
                      {x.magnitude === null ? "—" : formatPct(x.magnitude * 100, 2)}
                    </td>
                    <td className="py-1 text-xs text-muted-foreground">{x.detail}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {small > 0 && (
        <button
          type="button"
          onClick={() => setShowSmall((v) => !v)}
          className="mt-2 text-xs text-primary hover:underline"
        >
          {showSmall ? "Hide" : "Show"} {formatInt(small)} tiny ones (measuring noise, under the
          review threshold)
        </button>
      )}
    </Section>
  );
}
