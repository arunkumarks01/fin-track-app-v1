"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { fetchKiteStatus, fetchLoginUrl, type KiteState } from "@/lib/admin";
import { formatIstDateTime } from "@/lib/format";

const LABEL: Record<KiteState, { text: string; className: string }> = {
  connected: { text: "▲ connected", className: "text-emerald-400" },
  expired: { text: "▼ expired", className: "text-amber-400" },
  disconnected: { text: "▼ not logged in", className: "text-amber-400" },
  not_configured: { text: "✕ not configured", className: "text-red-400" },
};

export function KitePanel() {
  const kite = useQuery({
    queryKey: ["kite-status"],
    queryFn: () => fetchKiteStatus(),
    refetchInterval: 30_000,
  });
  const [loginError, setLoginError] = useState<string | null>(null);

  async function login() {
    setLoginError(null);
    try {
      // a fresh URL every time: it carries a one-time state nonce the callback checks
      window.open(await fetchLoginUrl(), "_blank", "noopener");
    } catch {
      setLoginError("Couldn't get a login link from the backend.");
    }
  }

  if (kite.isPending) return <p className="text-neutral-400">Checking Kite session…</p>;
  if (kite.error) {
    return (
      <p role="alert" className="text-red-400">
        ✕ Couldn&apos;t read the Kite session — treat it as not connected.
      </p>
    );
  }
  const k = kite.data;
  const style = LABEL[k.status];
  return (
    <div className="flex flex-col gap-3 text-sm">
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
        <dt className="text-neutral-400">Session</dt>
        <dd className={style.className}>{style.text}</dd>
        {k.userId && (
          <>
            <dt className="text-neutral-400">User</dt>
            <dd>{k.userId}</dd>
          </>
        )}
        {k.expiresAt && (
          <>
            <dt className="text-neutral-400">{k.status === "connected" ? "Expires" : "Expired"}</dt>
            <dd>
              {formatIstDateTime(k.expiresAt)}
              {k.hoursLeft !== null && ` · ${k.hoursLeft.toFixed(1)} h left`}
            </dd>
          </>
        )}
      </dl>
      {k.status !== "not_configured" && (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={login}
            className="rounded border border-sky-600 px-3 py-1 text-sky-300 hover:bg-sky-950"
          >
            {k.status === "connected" ? "Log in again" : "Log in to Kite"}
          </button>
          <span className="text-neutral-500">Opens Kite in a new tab; you sign in there.</span>
        </div>
      )}
      {loginError && (
        <p role="alert" className="text-red-400">
          ✕ {loginError}
        </p>
      )}
    </div>
  );
}
