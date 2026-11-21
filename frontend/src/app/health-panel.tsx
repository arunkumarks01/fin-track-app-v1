"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchHealth } from "@/lib/health";

export function HealthPanel() {
  const { data, error, isPending } = useQuery({
    queryKey: ["health"],
    queryFn: () => fetchHealth(),
    refetchInterval: 30_000,
  });

  if (isPending) return <p className="text-muted-foreground">Checking backend…</p>;
  if (error) {
    return (
      <p role="alert" className="text-loss">
        ✕ Backend unreachable — nothing on this page can be trusted until it is.
      </p>
    );
  }
  const healthy = data.status === "ok";
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
      <dt className="text-muted-foreground">Status</dt>
      <dd className={healthy ? "text-gain" : "text-warn"}>{healthy ? "▲ ok" : "▼ degraded"}</dd>
      <dt className="text-muted-foreground">Database</dt>
      <dd>{data.db}</dd>
      <dt className="text-muted-foreground">Scheduler</dt>
      <dd>{data.scheduler}</dd>
      <dt className="text-muted-foreground">Backend</dt>
      <dd>v{data.version}</dd>
    </dl>
  );
}
