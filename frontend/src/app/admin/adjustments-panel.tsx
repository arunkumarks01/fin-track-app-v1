"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchAdjustmentsSummary, fetchRepairs } from "@/lib/adjustments";
import { formatInt } from "@/lib/format";
import { QUALITY_TEXT } from "@/lib/labels";

const CLASS_TEXT: Record<string, { label: string; short: string; className: string }> = {
  explained: {
    label: "matches an NSE action",
    short: "matches NSE",
    className: "text-gain",
  },
  attributed: {
    label: "at an NSE action we can't check",
    short: "can't check",
    className: "text-foreground/80",
  },
  unexplained_small: {
    label: "no NSE action, small (kept, marked)",
    short: "small, no reason",
    className: "text-warn",
  },
  suspect: {
    label: "no NSE action, large (not used)",
    short: "suspect",
    className: "text-loss",
  },
  corroborated: {
    label: "not in NSE's list, but NSE prices confirm it (kept, listed below)",
    short: "confirmed by NSE prices",
    className: "text-primary",
  },
  official_mismatch: {
    label: "latest bars differ from NSE official",
    short: "differs from official",
    className: "text-loss",
  },
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
  if (summary.isPending) return <p className="text-muted-foreground">Loading adjustments…</p>;
  if (summary.error) {
    return (
      <p role="alert" className="text-loss">
        ✕ Couldn&apos;t read the adjustment report — treat adjusted/as-traded views as unknown.
      </p>
    );
  }
  const s = summary.data;
  const ok = s.archive.ok;
  return (
    <div className="flex flex-col gap-5 text-sm">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
        <dt className="text-muted-foreground">NSE archive days</dt>
        <dd>
          {formatInt(ok?.dates ?? 0)} loaded
          {ok && ` (${ok.oldest} → ${ok.newest})`}
          {s.archive.missing && (
            <span className="text-warn">
              {" "}
              · {formatInt(s.archive.missing.dates)} missing from the archive
            </span>
          )}
          {s.archive.error && (
            <span className="text-loss"> · {formatInt(s.archive.error.dates)} errored</span>
          )}
        </dd>
        <dt className="text-muted-foreground">Official as-traded rows</dt>
        <dd>{formatInt(s.bhavRows)}</dd>
        <dt className="text-muted-foreground">NSE corporate actions</dt>
        <dd>
          {formatInt(s.actions)}{" "}
          <span className="text-muted-foreground">
            (
            {Object.entries(s.actionsByKind)
              .map(([k, n]) => `${k} ${formatInt(n)}`)
              .join(" · ")}
            )
          </span>
        </dd>
        <dt className="text-muted-foreground">Kite re-adjustments seen</dt>
        <dd>{formatInt(s.driftEvents)}</dd>
        <dt className="text-muted-foreground">Drift checks out of date</dt>
        <dd className={s.checksStale > 0 ? "text-warn" : "text-gain"}>
          {s.checksStale > 0
            ? `${formatInt(s.checksStale)} symbol-grains — adjusted view may be stale until the next catch-up re-checks them`
            : `✓ all checked through ${s.lastFinalSession}`}
        </dd>
      </dl>

      <div className="grid gap-5 md:grid-cols-2">
        <div>
          <h3 className="mb-2 font-medium">Steps in Kite&apos;s factor</h3>
          {Object.keys(s.eventsByClass).length === 0 ? (
            <p className="text-muted-foreground">None measured yet.</p>
          ) : (
            <table className="w-full text-left">
              <tbody>
                {Object.entries(s.eventsByClass).map(([k, n]) => (
                  <tr key={k} className="border-t border-border">
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
            <p className="text-muted-foreground">Not measured yet.</p>
          ) : (
            <table className="w-full text-left">
              <tbody>
                {Object.entries(s.daysByQuality).map(([k, n]) => (
                  <tr key={k} className="border-t border-border">
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

      {(s.review.length > 0 || s.reviewNoise.count > 0) && (
        <div className="overflow-x-auto">
          <h3 className="mb-2 font-medium">To review</h3>
          {s.reviewNoise.count > 0 && (
            <p className="mb-2 text-muted-foreground">
              {formatInt(s.reviewNoise.count)} smaller unexplained steps (under{" "}
              {(s.reviewNoise.below * 100).toFixed(2)}%) are refit noise between windows: counted
              here, kept and badged, and listed on each symbol&apos;s page.
            </p>
          )}
          <table className="w-full min-w-[36rem] text-left">
            <tbody>
              {s.review.slice(0, 15).map((r) => (
                <tr key={`${r.symbol}-${r.date}`} className="border-t border-border">
                  <td className="py-1">{r.symbol}</td>
                  <td className="py-1">{r.date}</td>
                  <td className={`py-1 ${CLASS_TEXT[r.cls]?.className ?? ""}`}>
                    {CLASS_TEXT[r.cls]?.short ?? r.cls}
                  </td>
                  <td className="py-1 text-right tabular-nums">
                    {r.magnitude === null ? "—" : `${(r.magnitude * 100).toFixed(2)}%`}
                  </td>
                  <td className="py-1 text-muted-foreground">{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {s.review.length > 15 && (
            <p className="mt-1 text-muted-foreground">
              …and {formatInt(s.review.length - 15)} more.
            </p>
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
    return <p className="text-loss">✕ Couldn&apos;t read the repair log.</p>;
  }
  const r = q.data;
  if (r.repairs === 0 && r.archiveRowsPurged === 0) return null;
  const done = r.notCheckable === 0 && r.matchOfficial === r.checked;
  return (
    <div>
      <h3 className="mb-2 font-medium">Provisional-bar repairs</h3>
      <p className={done ? "text-gain" : "text-warn"}>
        {formatInt(r.repairs)} bars repaired · {formatInt(r.matchOfficial)} of{" "}
        {formatInt(r.checked)} match NSE&apos;s official close
        {r.notCheckable > 0 && ` · ${formatInt(r.notCheckable)} not checkable yet`}
      </p>
      {r.archiveRowsPurged > 0 && (
        <p className="text-muted-foreground">
          {formatInt(r.archiveRowsPurged)} wrong NSE archive rows removed (each one logged)
        </p>
      )}
      {r.mismatches.length > 0 && (
        <ul className="mt-1 text-loss">
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
