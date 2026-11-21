"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { type CandlePage, FinTrackFeed, INTERVALS } from "@/lib/charts";
import { currentTheme, THEME_EVENT, type Theme } from "@/lib/theme";

type Provenance = { source: string; omitted: number; adjustment: string; interval: string };
type Adjustment = "adjusted" | "raw";
type WidgetHandle = {
  symbol(): string;
  exchange(): string;
  interval(): string;
  setDataVariant(v: { adjustment: Adjustment } | undefined): void;
};

/**
 * The OpenAlgo Charts widget over FinTrack's feed. The widget (and its 112 studies) loads
 * in the browser only, and is destroyed on unmount. No onOrder: the chart offers no trading.
 * The widget has no built-in price-variant control, so the Adjusted/Raw switch lives here and
 * is enabled only where the feed declares an as-traded series.
 */
export function ChartWorkspace() {
  const host = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<WidgetHandle | null>(null);
  const feedRef = useRef<FinTrackFeed | null>(null);
  const [provenance, setProvenance] = useState<Provenance | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adjustment, setAdjustment] = useState<Adjustment>("adjusted");
  const [rawOffered, setRawOffered] = useState(false);

  /** Re-read what the feed offers for the widget's current symbol + interval. */
  const refreshVariants = useCallback(async () => {
    const w = widgetRef.current;
    const feed = feedRef.current;
    if (!w || !feed) return;
    const caps = await feed.dataVariants({
      symbol: w.symbol(),
      exchange: w.exchange(),
      interval: w.interval(),
    });
    const offered = caps.adjustments?.includes("raw") ?? false;
    setRawOffered(offered);
    if (!offered) {
      setAdjustment("adjusted");
      w.setDataVariant(undefined);
    }
  }, []);

  useEffect(() => {
    let destroyed = false;
    let destroy: (() => void) | null = null;
    void (async () => {
      try {
        const [{ createWidget }] = await Promise.all([
          import("openalgo-charts/widget"),
          import("openalgo-charts/indicators"),
        ]);
        if (destroyed || !host.current) return;
        const feed = new FinTrackFeed(undefined, 5000, (page: CandlePage, req) =>
          setProvenance({
            source: page.source,
            omitted: page.omittedUnusable,
            adjustment: req.variant?.adjustment ?? "adjusted",
            interval: req.interval,
          }),
        );
        const widget = createWidget(host.current, {
          feed,
          intervals: INTERVALS,
          theme: currentTheme(),
          persist: "fintrack-v2-charts",
          indicators: true,
        });
        feedRef.current = feed;
        widgetRef.current = widget;
        const refresh = () => void refreshVariants().catch((e: unknown) => setError(String(e)));
        const offs = [
          widget.on("status", ({ text, kind }) => setError(kind === "error" ? text : null)),
          widget.on("symbol", refresh),
          widget.on("interval", refresh),
          widget.on("variant", ({ variant }) =>
            setAdjustment(variant?.adjustment === "raw" ? "raw" : "adjusted"),
          ),
        ];
        await widget.ready;
        // a link from the Stocks page (?symbol=NSE:XXX) opens that stock, keeping the interval
        const linked = new URLSearchParams(window.location.search).get("symbol");
        const [linkedExchange, linkedSymbol] = linked?.includes(":") ? linked.split(":", 2) : [];
        if (linkedExchange && linkedSymbol) {
          widget.setSymbol(linkedSymbol, linkedExchange);
          if (!widget.interval()) widget.setInterval("D");
        } else if (!widget.symbol()) {
          // first visit: nothing restored from the saved layout
          widget.setSymbol("NIFTY 50", "NSE");
          widget.setInterval("D");
        }
        refresh();
        const onTheme = (e: Event) => widget.setTheme((e as CustomEvent<Theme>).detail);
        window.addEventListener(THEME_EVENT, onTheme);
        destroy = () => {
          window.removeEventListener(THEME_EVENT, onTheme);
          offs.forEach((off) => off());
          widgetRef.current = null;
          widget.destroy();
        };
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      destroyed = true;
      destroy?.();
    };
  }, [refreshVariants]);

  const choose = (a: Adjustment) => {
    setAdjustment(a);
    widgetRef.current?.setDataVariant(a === "raw" ? { adjustment: "raw" } : undefined);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <div
          role="radiogroup"
          aria-label="Prices"
          className="flex overflow-hidden rounded border border-border"
        >
          {(["adjusted", "raw"] as const).map((a) => (
            <button
              key={a}
              type="button"
              role="radio"
              aria-checked={adjustment === a}
              disabled={a === "raw" && !rawOffered}
              onClick={() => choose(a)}
              className={`px-3 py-1 ${adjustment === a ? "bg-accent text-foreground" : "text-foreground/80 hover:bg-muted"} disabled:cursor-not-allowed disabled:text-muted-foreground/60`}
            >
              {a === "adjusted" ? "Adjusted" : "Raw (as traded)"}
            </button>
          ))}
        </div>
        {!rawOffered && (
          <span className="text-xs text-muted-foreground">
            Raw isn&apos;t available here: indices have one series, and 3m–1h are derived from the
            adjusted 1-minute bars.
          </span>
        )}
      </div>
      <div ref={host} className="h-[calc(100vh-13rem)] min-h-[420px] w-full" />
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {provenance ? (
          <>
            Source: {provenance.source} · {provenance.interval} · {provenance.adjustment}
            {provenance.omitted > 0 && (
              <span className="text-warn">
                {" "}
                · {provenance.omitted} one-minute bars omitted (no usable as-traded measurement for
                their day — never guessed)
              </span>
            )}
          </>
        ) : (
          "Loading…"
        )}
        {error && <span className="text-loss"> · {error}</span>}
      </p>
    </div>
  );
}
