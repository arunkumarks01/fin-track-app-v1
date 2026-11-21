"use client";

import { useQuery } from "@tanstack/react-query";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatInt } from "@/lib/format";
import {
  fetchReference,
  longShareBps,
  nets,
  PARTICIPANT_LABEL,
  PARTICIPANTS,
  type ParticipantDay,
} from "@/lib/reference";
import { cn } from "@/lib/utils";

/** Signed contracts with an arrow: direction is never shown by colour alone. */
function Signed({ n, className }: { n: number; className?: string }) {
  const tone = n > 0 ? "text-gain" : n < 0 ? "text-loss" : "text-muted-foreground";
  const arrow = n > 0 ? "▲" : n < 0 ? "▼" : "–";
  return (
    <span className={cn("tabular-nums", tone, className)}>
      {arrow} {formatInt(Math.abs(n))}
    </span>
  );
}

function Rows({ today, prev }: { today: ParticipantDay; prev: ParticipantDay | undefined }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[40rem] text-left text-sm">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-1 pr-4 font-normal">Who</th>
            <th className="py-1 pr-4 text-right font-normal">Index futures, net</th>
            <th className="py-1 pr-4 text-right font-normal">Change vs {prev?.date ?? "—"}</th>
            <th className="py-1 pr-4 text-right font-normal">Index futures long</th>
            <th className="py-1 pr-4 text-right font-normal">Stock futures, net</th>
            <th className="py-1 text-right font-normal">Index options, net</th>
          </tr>
        </thead>
        <tbody>
          {PARTICIPANTS.map((who) => {
            const p = today.oi[who];
            if (!p) {
              return (
                <tr key={who} className="border-t">
                  <td className="py-1.5 pr-4">{PARTICIPANT_LABEL[who]}</td>
                  <td colSpan={5} className="py-1.5 text-muted-foreground">
                    not in that day&apos;s file
                  </td>
                </tr>
              );
            }
            const n = nets(p);
            const before = prev?.oi[who];
            const share = longShareBps(p);
            return (
              <tr key={who} className="border-t">
                <td className="py-1.5 pr-4">{PARTICIPANT_LABEL[who]}</td>
                <td className="py-1.5 pr-4 text-right">
                  <Signed n={n.indexFutures} className="font-medium" />
                </td>
                <td className="py-1.5 pr-4 text-right">
                  {before ? <Signed n={n.indexFutures - nets(before).indexFutures} /> : "—"}
                </td>
                <td className="py-1.5 pr-4 text-right tabular-nums">
                  {share === null ? "—" : `${(share / 100).toFixed(1)}%`}
                </td>
                <td className="py-1.5 pr-4 text-right">
                  <Signed n={n.stockFutures} />
                </td>
                <td className="py-1.5 text-right">
                  <Signed n={n.indexOptions} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Who is long or short in equity derivatives — NSE's participant-wise open interest. */
export function ParticipantsCard() {
  const q = useQuery({ queryKey: ["reference", 2], queryFn: () => fetchReference(2) });
  const [today, prev] = q.data?.participants ?? [];
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>F&amp;O positions by participant</CardTitle>
        <CardDescription>
          Open contracts held at the close{today && ` on ${today.date}`}, from NSE&apos;s
          participant-wise file. Net = long − short, in contracts (not ₹). Index options net = calls
          (long − short) + puts (short − long). Last stored day, not live.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {q.error ? (
          <p role="alert" className="text-loss">
            ✕ Couldn&apos;t load participant positions: {q.error.message}
          </p>
        ) : q.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : !today ? (
          <p className="text-muted-foreground">Not loaded yet.</p>
        ) : (
          <Rows today={today} prev={prev} />
        )}
      </CardContent>
    </Card>
  );
}
