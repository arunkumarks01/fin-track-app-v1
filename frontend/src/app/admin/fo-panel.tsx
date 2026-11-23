"use client";

import { useQuery } from "@tanstack/react-query";

import { FO_KIND_LABEL, fetchFo } from "@/lib/fo";
import { formatInt } from "@/lib/format";

/** Slice 1f: how much of the F&O history (futures and options, from NSE's archive) is loaded. */
export function FoPanel() {
  const q = useQuery({
    queryKey: ["fo-summary"],
    queryFn: () => fetchFo(),
    refetchInterval: 60_000,
  });
  if (q.isPending) return <p className="text-muted-foreground">Loading F&amp;O history…</p>;
  if (q.error) {
    return (
      <p role="alert" className="text-loss">
        ✕ Couldn&apos;t read the F&amp;O history status — state unknown.
      </p>
    );
  }
  const s = q.data;
  return (
    <div className="flex flex-col gap-2 text-sm">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
        <dt className="text-muted-foreground">Days loaded</dt>
        <dd>
          {formatInt(s.daysOk)}
          {s.oldest && ` (${s.oldest} → ${s.newest})`}
          {s.daysMissing > 0 && (
            <span className="text-warn">
              {" "}
              · {formatInt(s.daysMissing)} missing in NSE&apos;s archive
            </span>
          )}
          {s.daysError > 0 && (
            <span className="text-loss"> · {formatInt(s.daysError)} errors (retried next run)</span>
          )}
        </dd>
        <dt className="text-muted-foreground">Contract-days stored</dt>
        <dd>
          {formatInt(s.rows)} across {formatInt(s.underlyings)} underlyings ({s.tableGb.toFixed(2)}{" "}
          GB)
        </dd>
        <dt className="text-muted-foreground">By type</dt>
        <dd>
          {Object.entries(s.rowsByKind)
            .map(([k, n]) => `${FO_KIND_LABEL[k] ?? k} ${formatInt(n)}`)
            .join(" · ") || "—"}
        </dd>
      </dl>
      <p className="text-xs text-muted-foreground">
        Daily open, high, low, close, volume and open interest for every futures and options
        contract that traded or held open interest, from NSE&apos;s F&amp;O archive (2015 onwards).
        Loads newest first and stops at the database size limit.
      </p>
    </div>
  );
}
