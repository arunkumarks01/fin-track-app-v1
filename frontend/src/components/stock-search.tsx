"use client";

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useMemo, useState } from "react";

import { type ChartSymbol, fetchSymbols } from "@/lib/charts";
import { cn } from "@/lib/utils";

/** Prefix matches first, then anywhere in the name. */
export function rankSymbols(list: ChartSymbol[], q: string, limit = 12): ChartSymbol[] {
  const t = q.trim().toUpperCase();
  if (!t) return list.slice(0, limit);
  const starts = list.filter((s) => s.tradingsymbol.startsWith(t));
  const has = list.filter((s) => !s.tradingsymbol.startsWith(t) && s.tradingsymbol.includes(t));
  return [...starts, ...has].slice(0, limit);
}

/** Search the universe; choosing a stock opens its Stock page. Keyboard: ↑ ↓ Enter Esc. */
export function StockSearch({ className }: { className?: string }) {
  const router = useRouter();
  const listId = useId();
  const symbols = useQuery({ queryKey: ["symbols"], queryFn: fetchSymbols, staleTime: 300_000 });
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const matches = useMemo(() => rankSymbols(symbols.data ?? [], q), [symbols.data, q]);
  const pick = (s: ChartSymbol) => {
    setQ("");
    setOpen(false);
    router.push(`/stocks?symbol=${encodeURIComponent(s.symbol)}`);
  };
  return (
    <div className={cn("relative w-full max-w-md", className)}>
      <Search
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
      />
      <input
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label="Search a stock"
        placeholder={
          symbols.error
            ? "Stock list unavailable"
            : symbols.isPending
              ? "Loading stocks…"
              : "Search a stock, e.g. RELIANCE"
        }
        disabled={!!symbols.error}
        className="h-9 w-full rounded-md border border-input bg-background pr-3 pl-9 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setActive((a) => Math.min(a + 1, matches.length - 1));
          else if (e.key === "ArrowUp") setActive((a) => Math.max(a - 1, 0));
          else if (e.key === "Enter" && matches[active]) pick(matches[active]);
          else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && matches.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute z-50 mt-1 max-h-80 w-full overflow-auto rounded-md border bg-popover text-sm text-popover-foreground shadow-lg"
        >
          {matches.map((s, i) => (
            <li
              key={s.symbol}
              role="option"
              aria-selected={i === active}
              className={cn(
                "flex cursor-pointer justify-between px-3 py-1.5",
                i === active ? "bg-accent text-accent-foreground" : "hover:bg-muted",
              )}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(s);
              }}
            >
              <span className="font-medium">{s.tradingsymbol}</span>
              <span className="text-muted-foreground">
                {s.exchange} · {s.kind === "index" ? "index" : "stock"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
