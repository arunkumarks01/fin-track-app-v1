"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchAdjustmentsSummary, fetchRepairs } from "@/lib/adjustments";
import { formatInt } from "@/lib/format";

const CLASS_TEXT: Record<string, { label: string; className: string }> = {
  explained: { label: "explained by an NSE action", className: "text-emerald-400" },
  attributed: { label: "at an action we can't check", className: "text-neutral-300" },
  unexplained_small: { label: "unexplained, small (badged)", className: "text-amber-400" },
  suspect: { label: "unexplained, large (suspect)", className: "text-red-400" },
  official_mismatch: { label: "newest bars ≠ official", className: "text-red-400" },
};

const QUALITY_TEXT: Record<string, string> = {
  measured: "measured against NSE",
  carried: "carried (NSE row missing, flat window)",
  blip: "blip (one day off its window)",
  suspect: "suspect (no as-traded derived)",
  underdetermined: "under-determined window (too few days)",
  unmeasurable: "unmeasurable",
  not_applicable: "indices (never adjusted)",
};

export function AdjustmentsPanel() {
  const summary = useQuery({
    queryKey: ["adjustments-summary"],
    queryFn: () => fetchAdjustmentsSummary(),
    refetchInterval: 30_000,
  });
  const repairs = useQuery({
    queryKey: ["repairs"],
    queryFn: () => fetchRepairs(),
    refetchInterval: 60_000,
  });
  if (summary.isPending) return <p className="text-neutral-400">Loading adjustments…</p>;
  if (summary.error) {
    return (
      <p role="alert" className="text-red-400">
        ✕ Couldn&apos;t read the adjustment report — treat adjusted/as-traded views as unknown.
      </p>
    );
  }
  const s = summary.data;
  const ok = s.archive.ok;
  return (
    <div className="flex flex-col gap-5 text-sm">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
        <dt className="text-neutral-400">NSE archive days</dt>
        <dd>
          {formatInt(ok?.dates ?? 0)} loaded
          {ok && ` (${ok.oldest} → ${ok.newest})`}
          {s.archive.missing && (
            <span className="text-amber-400">
              {" "}
              · {formatInt(s.archive.missing.dates)} missing from the archive
            </span>
          )}
          {s.archive.error && (
            <span className="text-red-400"> · {formatInt(s.archive.error.dates)} errored</span>
          )}
        </dd>
        <dt className="text-neutral-400">Official as-traded rows</dt>
        <dd>{formatInt(s.bhavRows)}</dd>
        <dt className="text-neutral-400">NSE corporate actions</dt>
        <dd>
          {formatInt(s.actions)}{" "}
          <span className="text-neutral-500">
            (
            {Object.entries(s.actionsByKind)
              .map(([k, n]) => `${k} ${formatInt(n)}`)
              .join(" · ")}
            )
          </span>
        </dd>
        <dt className="text-neutral-400">Kite re-adjustments seen</dt>
        <dd>{formatInt(s.driftEvents)}</dd>
        <dt className="text-neutral-400">Drift checks out of date</dt>
        <dd className={s.checksStale > 0 ? "text-amber-400" : "text-emerald-400"}>
          {s.checksStale > 0
            ? `${formatInt(s.checksStale)} symbol-grains — adjusted view may be stale`
            : `✓ all checked through ${s.lastFinalSession}`}
        </dd>
      </dl>

      <div className="grid gap-5 md:grid-cols-2">
        <div>
          <h3 className="mb-2 font-medium">Steps in Kite&apos;s factor</h3>
          {Object.keys(s.eventsByClass).length === 0 ? (
            <p className="text-neutral-400">None measured yet.</p>
          ) : (
            <table className="w-full text-left">
              <tbody>
                {Object.entries(s.eventsByClass).map(([k, n]) => (
                  <tr key={k} className="border-t border-neutral-800">
                    <td className={`py-1 ${CLASS_TEXT[k]?.className ?? ""}`}>
                      {CLASS_TEXT[k]?.label ?? k}
                    </td>
                    <td className="py-1 text-right">{formatInt(n)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div>
          <h3 className="mb-2 font-medium">Symbol-days by quality</h3>
          {Object.keys(s.daysByQuality).length === 0 ? (
            <p className="text-neutral-400">Not measured yet.</p>
          ) : (
            <table className="w-full text-left">
              <tbody>
                {Object.entries(s.daysByQuality).map(([k, n]) => (
                  <tr key={k} className="border-t border-neutral-800">
                    <td className="py-1">{QUALITY_TEXT[k] ?? k}</td>
                    <td className="py-1 text-right">{formatInt(n)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <RepairsView q={repairs} />

      {s.review.length > 0 && (
        <div className="overflow-x-auto">
          <h3 className="mb-2 font-medium">To review</h3>
          <table className="w-full min-w-[36rem] text-left">
            <tbody>
              {s.review.slice(0, 15).map((r) => (
                <tr key={`${r.symbol}-${r.date}`} className="border-t border-neutral-800">
                  <td className="py-1">{r.symbol}</td>
                  <td className="py-1">{r.date}</td>
                  <td className={`py-1 ${CLASS_TEXT[r.cls]?.className ?? ""}`}>{r.cls}</td>
                  <td className="py-1 text-neutral-400">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {s.review.length > 15 && (
            <p className="mt-1 text-neutral-500">…and {formatInt(s.review.length - 15)} more.</p>
          )}
        </div>
      )}
    </div>
  );
}

function RepairsView({
  q,
}: {
  q: ReturnType<typeof useQuery<Awaited<ReturnType<typeof fetchRepairs>>>>;
}) {
  if (q.isPending) return null;
  if (q.error) {
    return <p className="text-red-400">✕ Couldn&apos;t read the repair log.</p>;
  }
  const r = q.data;
  if (r.repairs === 0) return null;
  const done = r.notCheckable === 0 && r.matchOfficial === r.checked;
  return (
    <div>
      <h3 className="mb-2 font-medium">Provisional-bar repairs</h3>
      <p className={done ? "text-emerald-400" : "text-amber-400"}>
        {formatInt(r.repairs)} bars repaired · {formatInt(r.matchOfficial)} of{" "}
        {formatInt(r.checked)} match NSE&apos;s official close
        {r.notCheckable > 0 && ` · ${formatInt(r.notCheckable)} not checkable yet`}
      </p>
      {r.mismatches.length > 0 && (
        <ul className="mt-1 text-red-400">
          {r.mismatches.slice(0, 10).map((m) => (
            <li key={`${m.symbol}-${m.date}`}>
              {m.symbol} {m.date}: repaired {formatInt(m.newClose)} vs official{" "}
              {formatInt(m.officialClose)} paisa
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
