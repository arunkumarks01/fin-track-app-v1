# FinTrack v2 — Complete Scope

> Created 2026-11-19. The FULL product scope, enumerated up front so it's planned, not
> discovered. Everything here is IN scope. `BUILD_PLAN.md` sequences the *order* of
> construction by dependency — sequencing is not scope-cutting; nothing below is dropped.
>
> Each area lists (a) the full capability set and (b) **decisions to lock** with a
> recommended default [R]. Confirm or redirect each; once locked, ARCHITECTURE.md and
> BUILD_PLAN.md are updated to match.

---

## 1. Market data & coverage
**Capabilities:** equity (cash), index, stock futures, index futures, options (CE/PE); F&O
universe (~200) + indices (Nifty, BankNifty, FinNifty, MidcpNifty, Sensex, Bankex).
Timeframes: tick, 1m, 3m, 5m, 15m, 30m, 1h, 1d, weekly. Depth: daily→2015, intraday to Kite
limit, **tick storage** (permanent, for slippage calibration). Live: Kite WebSocket ticks,
quotes, **full options chain** (OI, IV, greeks), **pre-open auction** feed. Reference:
delivery%, corporate actions (split/bonus/div/rights), FII/DII, bulk/block deals, economic
calendar. Global: India VIX, SGX/GIFT Nifty, US futures, commodities. News: RSS + corporate
announcements/filings.
**Decisions:** D1 tick storage — [R] yes, compressed hypertable + Parquet cold (needed for
slippage model). D2 options-chain snapshot cadence — [R] every 3–5 min market hours. D3
global/VIX source — [R] Kite where available, else vendor; not Yahoo.

## 2. Research & scoring pipeline
**Capabilities:** deterministic `market→data→TA→FA→news→strategy→risk→QC→execution`, run as
**two pipelines** — swing (EOD/daily) and intraday (5-min scanner). Full TA suite; FA scoring
+ red flags; LLM news/sentiment; **one calibrated conviction** (→ historical win-prob) shared
across both. Options analytics: PCR, max-pain, gamma wall, IV rank, OI buildup.
**Decisions:** D4 scoring cadence — [R] swing 09:20 + EOD; intraday every 5 min 09:15–15:00.
D5 conviction weights — [R] calibrated from backtest, not hand-set.

## 3. Strategy catalogue (full, both directions)
**Swing:** 52W breakout, EMA golden cross, RSI bounce, BB-squeeze daily, RS-vs-Nifty, long
CE/PE. **Intraday:** ORB, VWAP scalp, gap fill, momentum continuation, expiry-gamma, CPR
breakout, BB-Keltner squeeze, EMA 9/21 cross. **Directional:** long **and short**.
**Options multi-leg:** bull-call / bear-put spread, iron condor, short straddle/strangle,
covered call, calendar. **Custom strategies** added via DB config (no code change).
**Phase-unlock** progression (paper-performance gated).
**Decisions:** D6 shorts — [R] include from the start (full scope), long-only is a *runtime
toggle*, not a scope cut. D7 options multi-leg — [R] in scope, unlocked by phase progression
+ payoff-diagram education. D8 custom-strategy config format.

## 4. Risk & portfolio management
**Capabilities:** sizing modes — fixed-% risk, fixed-lots, **Kelly** (data-gated); risk-%
vs notional-cap separation; lot-aware; capital from Kite margin. Portfolio: max concurrent,
cash buffer, **correlation-aware sizing**, sector exposure caps, portfolio heat, daily
loss limit / circuit breaker. Targets/SL from structure+ATR; trailing stops.
**Decisions:** D9 default sizing mode — [R] fixed-% risk. D10 portfolio caps — values to set.
D11 Kelly — [R] in scope, locked until N months of validated data.

## 5. Paper trading (full sandbox)
**Capabilities:** order types MARKET / LIMIT / SL-M / **GTT**; realistic fills — slippage +
full charge stack (brokerage, STT per-instrument, exch txn, SEBI, GST, stamp), **partial
fills + liquidity**; position mgmt, MTM from live quotes, TSL, EOD square-off; execution
modes **SEMI_AUTO (Telegram approve) + FULL_AUTO** per strategy.
**Decisions:** D12 slippage model — [R] tick-calibrated once tick data exists, static model
until. D13 partial-fill realism depth.

## 6. Backtesting (full)
**Capabilities:** **signal** backtesting (per-strategy expectancy) + **portfolio**
backtesting (simultaneous positions, capital allocation, correlation); **walk-forward**
(in-sample/out-of-sample); honest costs; chronological drawdown; backtest↔live↔paper parity;
segment analysis (strategy × beta × sector × time-window × **market regime**); equity curve,
Sharpe, Sortino, max DD, profit factor; slippage calibration from ticks.
**Decisions:** D14 vectorized vs event-driven engine — [R] event-driven bar-replay (matches
live path for parity). D15 regime labels (bull/bear/high-vol) source.

## 7. Live trading (gated)
**Capabilities:** Kite order placement, smart order, order recovery by tag, funds/margin,
live P&L (Kite bug workarounds); all guardrails; kill-switch; security incidents;
Telegram confirm on first live trade. **Gated behind the forward-test pass.**
**Decisions:** D16 auto vs semi for live — [R] semi-auto only at first live, auto after a
further validation window.

## 8. Agents (genuine AI — your learning surface)
**Capabilities:** **News/research agent** (tool-use: fetch articles/filings, retrieve
context; structured output; fail-closed). **Explainer/tutor agent** (RAG over your own
backtest stats + the trade's real signals). **Q&A-over-your-data agent** (NL→SQL on your
trades/candles/backtests). **Market-commentary agent** (daily pre-open narrative).
**Options-flow analyst agent** (interprets OI/IV/PCR in plain English). All inform/teach —
**none decide, size, or compute trades.**
**Decisions:** D17 LLM provider — [R] Claude CLI now → Groq/paid later, behind one
abstraction with circuit-breaker + schema validation. D18 which agents in v2.0 vs later
(all in scope; order TBD).

## 9. Learning layer (first-class)
**Capabilities:** pre-open auction tutorial; strategy explainers; glossary tooltips; "Why
this trade?" w/ historical context; **options payoff diagrams**; **risk visualizer** (price
slider → ₹ P&L); post-trade **journal: prediction vs outcome**; backtest context on every
rec; **strategy-unlock progression** education; market-microstructure lessons. Beginner ⇄
Pro modes.
**Decisions:** D19 curriculum structure. D20 beginner/pro default per device.

## 10. Frontend / UX
**Capabilities:** Dashboard (global bar, pre-open, market pulse, candidates swing/intraday/
all, open positions w/ live charts, news); Journal; Backtest; Analytics (P&L, drawdown,
Sharpe, sector/correlation heatmaps); **Options** (chain, IV surface, PCR, gamma, max-pain);
Learn; Settings (4 tabs); Admin/Health. Charts: candlestick+indicators, equity curve,
heatmaps, payoff. Design system (tokens, primitives, one formatter), honest-UI, responsive
+ mobile bottom-tabs, dark/light.
**Decisions:** D21 candlestick chart lib. D22 PWA/mobile-app in scope — [R] PWA yes, native later.

## 11. Infrastructure & ops (prod-ready)
**Capabilities:** single-process backend (scheduler `max_instances=1`); **TimescaleDB**
(hypertables; Apache build → scheduled rollups + `drop_chunks` retention job instead of continuous aggregates/compression/retention policies, see D25) + **Parquet** cold store;
in-memory hot cache (no Redis); **backups/PITR + read replica** for the ledger;
**observability** (metrics, error tracking, dead-man's-switch, health dashboard); auth
(bearer + private network), secrets mgmt; CI/CD; full test strategy (golden, regression
snapshot, parity).
**Decisions:** D23 deploy — [R] single VPS behind Tailscale; **native, no Docker** (systemd + reverse proxy). D24 backup
cadence + where. D25 managed Postgres vs self-hosted (durability).

## 12. Notifications
**Capabilities:** Telegram — new candidate (approve/reject/later), intraday signal (2-min
approve), target/SL hit, TSL moved, pipeline failure, daily summary, security incident,
trade placed. Per-event preferences.
**Decisions:** D26 channels beyond Telegram (email/push)? — [R] Telegram only v2.0.

## 13. Advanced / later (in scope, sequenced last)
Sector rotation, correlation/portfolio heatmap, options-flow dashboards (IV surface, max-pain
heatmap), Kelly sizing, multi-broker (swap `broker/` module), native mobile app.

---

## How we lock this
Go through D1–D26. For each: **confirm the [R] default, or redirect.** Where there's no [R]
(values/formats), give the value. Once locked, I update ARCHITECTURE.md + BUILD_PLAN.md to
cover the *full* locked scope, and the build order becomes the sequenced delivery of all of
it — data foundation first, live trading last, learning woven throughout.

Open cross-cutting decision: **DEC-B** corporate-actions source (Kite vs NSE) — needed in the
data foundation regardless.
