# FinTrack v2 — UX Design

> Created 2026-11-19. The target UI/UX for the full scope. Learning-first and honest by
> construction. Consistent with ARCHITECTURE.md, SCOPE.md, DECISIONS.md.

## North star
Two rules govern everything: **(1) Honest** — never show a wrong/uncertain number as certain;
stale or insufficient data is badged or excluded, never silently scored. **(2) Learning-first**
— every recommendation explains *why* and *what has to go wrong to lose*, in plain English,
with the risk in ₹ always visible.

## Information architecture
**Top nav (desktop):** Dashboard · Candidates · Journal · Backtest · Analytics · Options ·
Learn · Settings · Admin. **Header (global):** logo · market status · Beginner⇄Pro toggle ·
PAPER/LIVE + submode badge · ticker health · **Kill switch** · light/dark.
**Mobile:** bottom tab bar (Dashboard · Candidates · Journal · Learn · More) — no horizontal
scroll; PWA-installable.

## Design system
- **One formatter** (`lib/format.ts`): paisa→₹, pct, sentiment on a single 0–100 scale. No per-component formatters (v1's inconsistency bug).
- **Primitives:** StatTile, ScoreBar, Badge, focusable Tooltip, Modal/Sheet, EmptyState, ErrorState, Skeleton. Standard loading/empty/error everywhere.
- **Tokens:** dark-first palette + a real light theme (D14); semantic colors always paired with an icon/arrow (colorblind + a11y), never color alone.
- **Beginner ⇄ Pro** (D20): Beginner = plain-English-first, more tooltips, fewer raw numbers; Pro = density, keyboard shortcuts. Same components, a mode flag, persisted per device.
- **Charts (D21 → DEC-CHART):** openalgo-charts (candles; in-browser studies are visual-only), Recharts (equity/heatmaps/payoff).

## Screens

**Dashboard** — global bar (Nifty/BankNifty/VIX/SGX/US futures, from Kite); **pre-open
auction** panel (09:00–09:15) with live tutorial overlay; **market pulse** (plain-English
"what today looks like and why"); **candidates** (tabs: swing / intraday / all; compact rows →
expand); **open positions** with live MTM + per-position chart; **news** panel (relevant,
symbol-tagged). Every candidate carries a **data-age / sufficiency badge** — a thin/stale
symbol is badged or absent, never shown as a confident signal.

**Candidate card + "Why this trade?"** — symbol, instrument badge, **calibrated conviction**
("~X% of similar setups hit target historically", from backtest), entry/SL/target in ₹, R:R
with Good/Fair/Low. The modal: per-indicator plain-English explanations, the weighted/
calibrated breakdown, FA interpretation (PE/EPS/beta in words), red flags, and the **historical
context** ("won 68% of the last 50 — here are the 3 it failed and why"). One sentiment number,
one scale, everywhere.

**Trade confirm (paper)** — **manual by default** (no surprise auto-fire). ₹1 cr paper capital
shown honestly (matches the backend). **Risk always visible** — "Max loss if stop hit ₹X ·
Target gain ₹Y" and ₹-at-risk — even in Learning mode where nothing blocks the trade. For
options: a **payoff diagram** before placing. A **risk slider** (drag price → ₹ P&L live).
Learning vs Realistic submode indicated.

**Journal** — every closed trade: **predicted vs actual** (entry/SL/target/conviction vs what
happened, exit reason). Running stats by strategy / conviction bucket. Plain-English post-trade
note ("you had no stop; here's how far it ran against you") — this is the core learning loop
and it feeds calibration.

**Backtest** — runner; equity curve; win-rate by signal/sector/time/regime; segment heatmaps;
"has this strategy earned trust yet" framing tied to phase-unlock.

**Analytics** — portfolio P&L, drawdown, Sharpe/Sortino, sector exposure, correlation heatmap.

**Options** — chain (OI/IV/greeks), IV surface, PCR, gamma wall, max-pain — each with a
plain-English "what this tells you" tooltip.

**Learn** — pre-open tutorial, per-strategy explainers, glossary, market-microstructure
lessons, the **phase-unlock progression** shown as a visible path ("50 paper trades + positive
win rate → spreads unlock").

**Settings** — guardrails (+ the Learning/Realistic paper toggle), strategy config, data
sources, API keys/notifications. Kill-switch mirrored here, primary in the header. Copy never
claims enforcement that isn't real.

**Admin/Health** — system health, pipeline runs, security incidents (red), log viewer,
dead-man's-switch status.

## Honest-UI laws (non-negotiable)
- Never present a computed number the backend can't stand behind; badge staleness/insufficiency.
- One capital number end-to-end; one sentiment scale; one formatter.
- Manual trade confirm default; auto-place is an explicit, loud opt-in (never in live).
- In paper Learning mode: **never block a trade, always show the risk.**
- Guardrail copy states only enforcement that actually exists.
- Focusable tooltips; keyboard-navigable modals; arrows+color, never color alone.

## Build note
Delivered in BUILD_PLAN Phase 8 on top of a proven backend — UX sits on correct numbers, never
the reverse. The design system + `lib/format.ts` land first so screens can't drift.
