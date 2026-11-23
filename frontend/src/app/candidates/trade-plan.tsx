import type { ReactNode } from "react";

import { formatDecimal, formatInt, formatIstDateTime, formatRupees } from "@/lib/format";
import type { TradePlan } from "@/lib/risk";

function Metric({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="rounded-lg border bg-background/70 p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm font-semibold tabular-nums">{value}</dd>
      {note && <dd className="mt-1 text-xs leading-relaxed text-muted-foreground">{note}</dd>}
    </div>
  );
}

export function TradePlanPanel({ plan }: { plan: TradePlan | null }) {
  const absent = plan === null || plan.status === "unavailable";
  return (
    <section aria-label="Trade plan" className="space-y-3 rounded-lg border bg-muted/25 p-3 sm:p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Trade plan</h3>
        {!absent && (
          <span className="text-xs text-muted-foreground">
            {plan.status === "expired" ? "Entry window expired" : "Paper preview"} ·{" "}
            {plan.paperMode === "learning" ? "Learning" : "Realistic"}
          </span>
        )}
      </div>
      {absent ? (
        <p className="text-sm text-muted-foreground">
          {plan?.reason ?? "Trade plan data is unavailable from this backend."}
        </p>
      ) : (
        <>
          <dl className="grid grid-cols-1 gap-2 min-[400px]:grid-cols-2 xl:grid-cols-3">
            <Metric
              label={plan.side === "long" ? "Buy at" : "Sell short at"}
              value={formatRupees(plan.trigger)}
              note="Entry trigger"
            />
            <Metric
              label={plan.side === "long" ? "Don’t buy above" : "Don’t short below"}
              value={formatRupees(plan.limit)}
              note="Entry price limit"
            />
            <Metric
              label={plan.side === "long" ? "Don’t buy after" : "Don’t short after"}
              value={plan.expiresAt ? formatIstDateTime(plan.expiresAt) : plan.entryWindow}
            />
            <Metric
              label="Initial stop-loss"
              value={formatRupees(plan.stop)}
              note={`Structure anchor ${formatRupees(plan.support)} plus an ATR buffer`}
            />
            <Metric
              label={plan.side === "long" ? "Sell target" : "Buy-to-cover target"}
              value={formatRupees(plan.target)}
              note={
                plan.targetBasis === "structure"
                  ? "From a prior price level"
                  : "ATR extension from the signal candle"
              }
            />
            <Metric
              label="Reward to risk"
              value={`${formatDecimal(plan.rewardRiskBps / 10000)} : 1`}
              note={`At the entry price limit, before costs. Risk per unit ${formatRupees(plan.riskPerUnit)}.`}
            />
          </dl>
          <div className="grid gap-3 border-t pt-3 sm:grid-cols-2">
            <div className="space-y-1 text-sm">
              <p className="font-medium">Trailing stop rule</p>
              <p className="text-muted-foreground">
                {plan.trailingActivation > 0
                  ? `At ${formatRupees(plan.trailingActivation)}, trail ${formatRupees(plan.trailingDistance)} ${plan.side === "long" ? "below the highest favorable price" : "above the lowest favorable price"}.`
                  : "The activation level is outside the valid price range."}
              </p>
              <p className="text-xs text-muted-foreground">
                Preview based on the entry price limit.
              </p>
            </div>
            <div className="space-y-1 text-sm">
              <p className="font-medium">Paper position size</p>
              <p>
                {plan.units === null
                  ? "Sizing unavailable"
                  : `${formatInt(plan.units)} units · ${formatInt(plan.lots ?? 0)} ${plan.lots === 1 ? "lot" : "lots"}`}
              </p>
              {plan.risk !== null && plan.notional !== null && (
                <p className="text-muted-foreground">
                  Planned stop risk {formatRupees(plan.risk)} · Notional{" "}
                  {formatRupees(plan.notional)}
                </p>
              )}
              {plan.sizingNote && (
                <p className="text-xs text-muted-foreground">{plan.sizingNote}</p>
              )}
            </div>
          </div>
          <details className="border-t pt-3 text-sm">
            <summary className="cursor-pointer font-medium">Risk checks and assumptions</summary>
            <div className="mt-2 space-y-1 text-xs text-muted-foreground">
              <p>
                Paper capital {formatRupees(plan.capital)} · Risk budget{" "}
                {formatRupees(plan.riskBudget)} · Notional cap {formatRupees(plan.notionalCap)}
              </p>
              <p>
                Lot size {formatInt(plan.lotSize)} · ATR {formatRupees(plan.atr)} ·{" "}
                {plan.priceBasis}
              </p>
              {plan.blocks && <p className="text-warn">Realistic-mode checks block this plan.</p>}
              {[...plan.warnings, ...plan.unknown].map((message) => (
                <p key={message} className="text-warn">
                  {message}
                </p>
              ))}
            </div>
          </details>
        </>
      )}
    </section>
  );
}
