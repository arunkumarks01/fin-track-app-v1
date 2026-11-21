"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { StockSearch } from "@/components/stock-search";
import { ThemeToggle } from "@/components/theme-toggle";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { fetchKiteStatus } from "@/lib/admin";

function KiteBadge() {
  const q = useQuery({
    queryKey: ["kite-status"],
    queryFn: () => fetchKiteStatus(),
    refetchInterval: 60_000,
  });
  const ok = q.data?.status === "connected";
  const STATE = {
    connected: "Kite ● connected",
    expired: "Kite ○ login expired",
    disconnected: "Kite ○ logged out",
    not_configured: "Kite ○ not set up",
  } as const;
  const text = q.isPending ? "Kite …" : q.error ? "Kite ? unknown" : STATE[q.data.status];
  return (
    <Link href="/admin" title="Kite session — log in from Admin">
      <Badge
        variant="outline"
        className={ok ? "border-gain/50 text-gain" : q.isPending ? "" : "border-warn/50 text-warn"}
      >
        {text}
      </Badge>
    </Link>
  );
}

export function AppHeader() {
  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b bg-background/90 px-4 backdrop-blur sm:px-6">
      <SidebarTrigger aria-label="Show or hide the menu" />
      <Separator orientation="vertical" className="h-5" />
      <StockSearch />
      <div className="ml-auto flex items-center gap-2">
        <KiteBadge />
        <Badge
          className="border-warn/60 bg-warn/15 text-warn"
          variant="outline"
          title="Paper mode only — no real orders exist"
        >
          PAPER
        </Badge>
        <ThemeToggle />
      </div>
    </header>
  );
}
