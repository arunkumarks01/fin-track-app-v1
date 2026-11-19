"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchHealth } from "@/lib/health";

export function HealthPanel() {
  const { data, error, isPending } = useQuery({
    queryKey: ["health"],
    queryFn: () => fetchHealth(),
    refetchInterval: 30_000,
  });

  if (isPending) return <p className="text-neutral-400">Checking backend…</p>;
  if (error) {
    return (
      <p role="alert" className="text-red-400">
        ✕ Backend unreachable — nothing on this page can be trusted until it is.
      </p>
    );
  }
  const healthy = data.status === "ok";
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
      <dt className="text-neutral-400">Status</dt>
      <dd className={healthy ? "text-emerald-400" : "text-amber-400"}>
        {healthy ? "▲ ok" : "▼ degraded"}
      </dd>
      <dt className="text-neutral-400">Database</dt>
      <dd>{data.db}</dd>
      <dt className="text-neutral-400">Scheduler</dt>
      <dd>{data.scheduler}</dd>
      <dt className="text-neutral-400">Backend</dt>
      <dd>v{data.version}</dd>
    </dl>
  );
}
