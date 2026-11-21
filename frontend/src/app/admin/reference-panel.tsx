"use client";

import { useQuery } from "@tanstack/react-query";

import { formatInt, formatIstDateTime } from "@/lib/format";
import { DATASET_LABEL, DATASETS, fetchReference, type Reference } from "@/lib/reference";

/** Phase 1e NSE archive coverage by dataset. */
export function ReferencePanel() {
  const q = useQuery({
    queryKey: ["reference", 1],
    queryFn: () => fetchReference(1),
    refetchInterval: 30_000,
  });
  if (q.isPending) return <p className="text-muted-foreground">Loading reference data…</p>;
  if (q.error) {
    return (
      <p role="alert" className="text-loss">
        ✕ Couldn&apos;t read the reference-data ledger — state unknown.
      </p>
    );
  }
  const l = q.data.ledger;
  const s = q.data.sources;
  return (
    <div className="flex flex-col gap-2 text-sm">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[44rem] text-left">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 pr-4 font-normal">Dataset</th>
              <th className="py-1 pr-4 text-right font-normal">Days loaded</th>
              <th className="py-1 pr-4 font-normal">Range</th>
              <th className="py-1 pr-4 text-right font-normal">Days missing</th>
              <th className="py-1 pr-4 text-right font-normal">Errors (retried)</th>
              <th className="py-1 text-right font-normal">Notes</th>
            </tr>
          </thead>
          <tbody>
            {DATASETS.map((ds) => {
              const r = l[ds];
              return (
                <tr key={ds} className="border-t">
                  <td className="py-1 pr-4">{DATASET_LABEL[ds]}</td>
                  <td className="py-1 pr-4 text-right tabular-nums">{formatInt(r.ok)}</td>
                  <td className="py-1 pr-4 tabular-nums">
                    {r.oldest ? `${r.oldest} → ${r.newest}` : "—"}
                  </td>
                  <td className={`py-1 pr-4 text-right ${r.missing ? "text-warn" : ""}`}>
                    {formatInt(r.missing)}
                  </td>
                  <td className={`py-1 pr-4 text-right ${r.error ? "text-loss" : ""}`}>
                    {formatInt(r.error)}
                  </td>
                  <td className="py-1 text-right text-muted-foreground">
                    {r.rejected > 0 && `${formatInt(r.rejected)} rows rejected`}
                    {r.roundingDays > 0 &&
                      `${formatInt(r.roundingDays)} days: NSE's own total off by ≤2 contracts`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        NSE archive history starts in 2015. Bulk and block deals are captured from the latest
        available file, so missed sessions appear as gaps. Small discrepancies in NSE&apos;s
        open-interest totals are recorded in Notes.
      </p>
      <dl className="grid gap-3 border-t pt-3 sm:grid-cols-2 xl:grid-cols-3">
        <div>
          <dt>News RSS</dt>
          <dd className="text-muted-foreground">{formatInt(s.newsItems)} stored headlines</dd>
          {s.newsErrors > 0 && <dd className="text-loss">{formatInt(s.newsErrors)} feed errors</dd>}
          {s.newsRefusals > 0 && (
            <dd className="text-warn">{formatInt(s.newsRefusals)} feeds refused access</dd>
          )}
        </div>
        <div>
          <dt>Economic calendar</dt>
          <dd className="text-muted-foreground">
            {formatInt(s.calendarUpcoming)} upcoming imported events
          </dd>
        </div>
        <div>
          <dt>FRED</dt>
          <dd className="text-muted-foreground">
            {formatInt(s.fredSeries)} series with stored data
          </dd>
          {!s.fredKeyConfigured && <dd className="text-warn">API key required to fetch data</dd>}
          {s.fredErrors > 0 && (
            <dd className="text-loss">{formatInt(s.fredErrors)} series failed their last fetch</dd>
          )}
        </div>
        <IndexHistory title="GIFT Nifty (Kite history)" history={s.giftNifty} />
        <IndexHistory title="India VIX (Kite history)" history={s.indiaVix} />
      </dl>
    </div>
  );
}

function IndexHistory({
  title,
  history,
}: {
  title: string;
  history: Reference["sources"]["giftNifty"];
}) {
  return (
    <div>
      <dt>{title}</dt>
      <dd className="text-muted-foreground">
        {formatInt(history.dailyBars)} daily bars
        {history.dailyDate && ` · through ${history.dailyDate}`}
      </dd>
      <dd className="text-muted-foreground">
        {formatInt(history.minuteBars)} minute bars
        {history.minuteAt && ` · through ${formatIstDateTime(history.minuteAt)}`}
      </dd>
    </div>
  );
}
