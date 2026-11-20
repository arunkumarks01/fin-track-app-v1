# FinTrack v2 — Architecture

> Created 2026-11-19. The design for a production-ready, learning-first agentic NSE research,
> paper-trading, and backtesting platform. Single user. Built data-first so the system is
> correct by construction, not corrected after the fact.
>
> This document encodes the hard-won lessons from v1. Every principle below exists because
> v1 broke there. Read it before writing code.

---

## 1. Vision & scope

A personal platform that:
- **Teaches** — agents, market microstructure, TA/FA, and trading, with every recommendation explaining *why* and *what has to go wrong to lose*.
- **Researches** — a deterministic multi-stage pipeline scores ~200 NSE F&O underlyings using TA, fundamentals, and news sentiment.
- **Paper-trades** — realistic fill simulation (slippage + full charge stack), so forward-test results are trustworthy.
- **Backtests** — an *honest* validator (costs, chronological drawdown, backtest↔live↔paper parity).
- **Gates live** — paper → backtest → forward-test → live. Never skip the gate. Live is out of scope until the gate passes.

Scope: **full** — equity, index, futures and options across the NSE F&O universe; swing +
intraday; long **and** short; options multi-leg; paper + (gated) live; backtesting + a
first-class learning layer. Nothing is cut. `BUILD_PLAN.md` sequences delivery by dependency
(data foundation first, live last, learning throughout); `SCOPE.md` enumerates the whole
scope; `DECISIONS.md` records every locked choice. Capabilities whose safety isn't yet proven
(shorts, auto-execute, live, Kelly) ship as code but stay behind **runtime gates** that open
only when their validation passes — a gate, not a scope cut.

---

## 2. Design principles (these are laws, not suggestions)

1. **Data-first.** No indicator, score, or signal is built until the data under it is clean, deep, dedup-keyed by *trading date*, corporate-action-adjusted, and validated. v1's #1 failure was scoring on dirty, shallow data.
2. **Honest surfaces.** Never present a wrong or uncertain number as certain. Stale/insufficient data is flagged or excluded, never silently scored.
3. **Deterministic where it's money; AI where it's language.** Indicators, scoring, sizing, risk, and execution are deterministic and testable. LLMs are used only for news/sentiment and for *grounded explanation* — never to decide, size, or compute.
4. **One source of truth.** Settings/guardrails live in the DB, read at decision time. Kill-switch and pause actually block execution and persist across restarts.
5. **One datastore, one process.** Postgres/TimescaleDB for durable state, in-process memory for hot/cache. No Redis dual-store (v1's ghost-data bug). Single supervised process model.
6. **Integer paisa everywhere.** Prices stored and computed as integer paisa; convert to ₹ only at the display edge.
7. **Nothing hardcoded.** URLs, keys, limits, rates, thresholds → config from `.env`.
8. **Test-with-every-change.** Golden indicator tests vs a reference lib, a regression-snapshot harness, and backtest↔paper parity exist from day one. A change isn't done without its test.
9. **Fail closed.** On data/LLM/compute failure, exclude or flag — never default to "tradeable/neutral/safe."
10. **Paper-first, validate-before-live.** `PAPER_MODE=true` is the default and the only mode until the forward-test gate passes.

---

## 3. Tech stack & rationale

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI, **single process** | Simpler, no cross-process state; APScheduler `max_instances=1` needs no distributed lock |
| Datastore | **TimescaleDB (Postgres) on the VPS** — the *only* store, **no local DB** | hypertables for OHLCV; one source of truth. Runs the VPS **Apache build** → aggregates/compression/retention via **fallbacks** (rollup job, Parquet cold tier, cleanup cron), not TSL features |
| Hot/cache | **in-process memory** (dict + TTL) | fastest for one user; can't drift from the DB (v1's Redis ghost bug) |
| Broker/data | **Kite Connect** (direct) | authoritative NSE data + execution; no OpenAlgo middle layer |
| LLM | provider abstraction: Claude CLI now → Groq later | used only for news + explanation; fail-closed |
| Scheduler | APScheduler in-process, `max_instances=1`, `coalesce=True` | no double-runs, no lock hack |
| Frontend | Next.js 15 + Tailwind; Recharts (analytics) + one candlestick lib | design-system first; honest-UI |
| Backtest | custom bar-replay + Parquet cold store | deterministic, cost-aware |
| Notifications | Telegram (webhook secret mandatory) | approvals + alerts |
| Deploy | single VPS, behind Tailscale/private network; bearer-token on mutating routes | personal app; no open order endpoints |

**No Redis. No OpenAlgo. No LangGraph-as-decoration.** (See §6 on where genuine agents live.)

---

## 4. System architecture

```
                        ┌──────────────── FastAPI (single process) ────────────────┐
  Kite REST/WS ──▶ Data Foundation ──▶ TimescaleDB (raw + adjusted-on-read)          │
   (candles,          (ingest →          ├─ ohlcv_candles (1d) + ohlcv_1m (1m)        │
    quotes,            validate →        ├─ corporate_actions, fundamentals, news     │
    ticks)             adjust →          ├─ trades (paper ledger), strategy_settings  │
                       coverage)         └─ backtest_runs/_trades                     │
                             │                                                         │
  APScheduler ──▶ Scoring Pipeline (deterministic) ──▶ candidates (DB) ──▶ Dashboard  │
  (single-inst)   market→data→TA→FA→news→strategy→risk→QC→execution                   │
                             │                              │                          │
  LLM agents (news, explain) ┘              Paper Sandbox (fills, MTM, P&L)            │
                                            Backtest engine (honest, cost-aware)        │
                        └───────────────── in-memory hot cache (ticks/quotes) ─────────┘
```

Data flows one way: **Kite → validated storage → deterministic scoring → honest display.** LLMs hang off the side (news in, explanation out), never in the decision path.

---

## 5. Data architecture (the foundation — build this first)

**The non-negotiables v1 got wrong:**
- **Dedup key = trading identity, not timestamp.** Daily candles: canonical `ts` = 00:00 IST on ingest; uniqueness on `(symbol, timeframe, ts)` (includes the hypertable partition column — legal). Intraday: exact `ts`. Same day can never land twice.
- **Depth.** Daily back to 2015 (verified per-symbol, not assumed); 5-min to Kite's practical floor. Deep history in Parquet cold store; recent in Timescale.
- **Session completeness — per instrument (DEC-1B-CAL).** Each 1-minute session is judged against the instrument's own norm (re-baselined on a persistent step-change, floored by a market-wide short-day check) in `app.intraday_session_status`; no cross-sectional benchmark or percentile.
- **Timeframe derivation — on-read `time_bucket` (design of record).** Fetch two source grains from Kite: **1d** (deep, to 2015) and **1m** (compact `ohlcv_1m` table — int4 paisa + smallint local id, newest-first, ~mid-2015 depth). Derive 3m/5m/15m/30m/1h **on read via `time_bucket`** — an **Apache-core** function (no TSL), zero extra disk, can't drift. Must be **session-anchored**: `time_bucket(interval, ts, timezone => 'Asia/Kolkata', origin => <that session's actual open>)`. A midnight default misaligns 30m/1h (09:00/10:00 bars), and a *fixed* 09:15 origin misaligns special sessions: the 2024-11-01 muhurat session opened at **18:00**, and Kite's own 60m bar is 18:00–18:59. Session opens come from the derived calendar `app.exchange_sessions` (earliest first minute on the exchange that day). Implemented once as the SQL function `app.intraday_bars()` (migration 0004). **One shared derivation** for live *and* backtest, **golden-tested against Kite's native bars**. Continuous aggregates / a materialized hot window are optional *later optimizations* only if on-read latency hurts — never a separate re-fetch (v1's drift bug).
- **Store unchanged, measure the adjustment, both views on read (DEC-B revised, Phase 1c).** Kite gives no as-traded history — its candles are already adjusted, more richly than NSE's action list and not always alike across grains — so the original "store raw + apply parsed factors" plan would produce wrong numbers. Instead:
  - **Adjusted view** = Kite's data exactly as fetched (`ohlcv_candles`, `ohlcv_1m`) — never rewritten (the only rewrite path is the audited `data_repairs` repair of provisional bars).
  - **As-traded daily** = NSE's official bhavcopy (`app.nse_bhav`) — exact, not reconstructed.
  - **Kite = a·NSE + b, measured per window** (`app.adjustment_days`: `f_daily` = a, `b_daily` = b paisa) with a minimax fit whose worst residual over every OHLC point is ≤ half the window's measured Kite price grid, never below ±2.5 paisa (±5 on 10-paisa-grid windows) — never a least-squares average; windows forced at NSE ex-dates, a/b may step only at a matching action (unmatched → flagged), short windows `underdetermined`. The map is affine because Kite subtracts dividends for part of history. Separate measured ratios for the 1m grain and for volume.
  - **As-traded 1m** = `app.minute_as_traded()` = (stored ÷ G − b) ÷ a; NULL (never guessed) where the day is a blip, suspect, underdetermined or unmeasurable.
  - **Every factor step is attributed** to NSE corporate actions (`app.corporate_actions`, from the archive's `Bc` file) or flagged (`app.adjustment_events`: explained / attributed / unexplained_small / suspect / official_mismatch).
  - **Drift re-check**: each catch-up re-fetches the newest stored session; a consistent rescaling means Kite re-adjusted history → `app.adjustment_drift` (adjusted-as-of-now = stored × `app.drift_factor()`); a symbol whose check didn't run is shown "may be stale".
- **Validation gate.** Every write passes: `close>0`, OHLC ordering, non-negative volume, duplicate-by-date, gap detection, large-gap flag-but-keep (suspected corporate action). Bad rows → quarantine, never silently stored or dropped.
- **Coverage report** is the trust gate: per symbol, unique clean bars, oldest date, % gaps. Scoring is disabled for symbols below a sufficiency threshold (freshness/sufficiency gate), so the dashboard never shows a signal computed on thin/stale data.

**Compression / retention / continuous aggregates are efficiency features, not correctness features.** They need the full Timescale build (TSL license — free to self-host), which the Apache-only Ubuntu `universe` package strips out. Use them *where available*; otherwise fall back — deep history already lives in the **Parquet cold store** (Timescale holds mainly hot/recent data, so compression is far less urgent), retention = a scheduled cleanup cron (mainly `ticks`), aggregates = **on-read `time_bucket`** (Apache core; see above). **Correctness never depends on any of the three.** **Pin the same Timescale build + version across the VPS dev/test DB, CI, and prod** so CI can't pass on features prod lacks — the dev/prod-parity gap we accepted when we dropped Docker. v2 runs on the **existing VPS Timescale (Apache build 2.25.1, shared PG18)** using the fallbacks above — no local DB, no upgrade. The TSL library is installed OS-wide per PG-major, so you cannot run the full build for v2 while leaving the shared v1 instance on Apache; getting the TSL features = a planned shared-instance upgrade (VPS snapshot, off-hours, `ALTER EXTENSION UPDATE` on v1/tabsettle/vidforge too) — deferred and optional, only if Parquet + fallbacks prove insufficient.

---

## 6. The scoring pipeline — and where real agents live

**Core scoring is deterministic** (this is a money path): `market_setup → data → TA → FA → news → strategy → risk → QC → execution`. Each stage is a pure, typed, testable function. Conviction is **one calibrated function** (score → historical win-probability from `backtest_trades`), used identically by swing and intraday — never three divergent copies.

**Where genuine agentic AI belongs (and where you'll actually learn agent engineering):**
- **News/research agent** — an LLM with tools (fetch articles/filings, retrieve prior context) that classifies sentiment + material events, structured-output-validated, **fail-closed** (failure → "unknown" → exclude/flag, never "neutral/tradeable").
- **Explainer agent** — on-demand, RAG-grounded over *your own* backtest stats and the trade's real signals ("this setup won 68% of the last 50 — here are the 3 it failed"). Restates retrieved facts only; never invents numbers.
- These are safe places for tool-use, retrieval, and multi-step agent patterns because they *inform and teach*, they don't decide trades.

This satisfies "I want to learn agents" honestly: you build real agents where they add value and can't lose money, and keep deterministic code where correctness is money.

**Indicators:** computed with sufficient warmup (config-driven limits), EMA200 only when ≥200 bars exist (else "unavailable", never a shorter EMA), validated against a reference library by golden tests.

---

## 7. Risk, execution & safety

- **Sizing:** separate **risk-per-trade %** (max loss) from **max-notional %** (position cap); lot-aware whole-lot rounding; capital from Kite margin when connected, config default otherwise. One shared sizing helper (no swing/risk duplication).
- **Targets** derived from structure/ATR, so R:R is real, not a constant — QC's R:R gate actually bites.
- **Guardrails enforced** (not cosmetic): max trades/day, max concurrent positions, cash buffer, min conviction — all read from DB at decision time.
- **Idempotency:** execution refuses a duplicate when an open position/pending order exists for the instrument.
- **Kill-switch / `EXECUTION_PAUSED`:** persisted to DB, blocks *all* order paths, survives restart; security incidents auto-pause and alert.
- **Paper sandbox:** fills model slippage + full charge stack (brokerage, STT per instrument on the right base, exchange txn, SEBI, GST, stamp); futures/options read the correct tick key; MTM from live quotes. **Two modes on one engine:** *Learning* (₹1 cr capital, **never blocks a trade** — any trade allowed, auto-exit optional so you can watch price run) and *Realistic* (full guardrails + auto SL/target; required for the Phase-10 forward-test gate). **Risk is always visible in both modes** — SL, target, and ₹-at-risk shown on every trade; in Learning it is *displayed but never enforced*. Hiding risk is forbidden (honest-UI); blocking a learning trade is forbidden.
- **Auth:** bearer token on all mutating routes; mandatory Telegram webhook secret; private-network deploy.

---

## 8. Backtesting (the honest validator)

- **Chronological equity curve** → drawdown over time-ordered trades (never batch-insertion order).
- **Full cost model** (same `compute_trade_cost` the sandbox uses) applied to every leg.
- **Parity contract:** the backtest detector path and the live scan path produce identical signals for the same bar; same SL cap, same warmup, same costs. A parity test enforces it.
- **Calibration output:** per-setup historical win-rate/expectancy feeds the conviction calibration (§6) and the explainer agent (§6) — closing the loop between backtest and "trust the candidate."

---

## 9. Learning layer (first-class, not an afterthought)

- **"Why this trade?"** with historical context from the backtest.
- **Risk visualizer** (drag price → P&L in ₹), **options payoff diagrams**, **pre-open auction tutorial**, **glossary tooltips** (one shared dictionary).
- **Journal = prediction vs outcome** — every closed trade shows predicted entry/SL/target/conviction vs what actually happened; this is how intuition (and trust in the system) is built, and it feeds calibration.
- **Beginner ⇄ Pro** mode (density/tooltips), dark-default + light theme.

---

## 10. Honest-UI & design system

- One `lib/format.ts` (paisa→₹, pct, sentiment on one scale) — no per-component formatters.
- Shared primitives (StatTile, ScoreBar, Badge, focusable Tooltip, Modal, Empty/Error/Skeleton states).
- Manual trade confirm by default (no surprise auto-fire); one honest portfolio-capital number end to end.
- Data-age / sufficiency badges so staleness is shown, never hidden.
- Kill-switch in the global header; nav includes a real **Learn** section; mobile bottom-tabs.

---

## 11. Testing strategy (from day one, not bolted on)

- **Golden** indicator tests vs `pandas-ta`/TA-Lib on fixtures.
- **Convergence** tests (EMA warmup sufficiency).
- **Regression-snapshot** harness: full pipeline on frozen fixtures → committed golden outputs; any score change fails CI until a human re-blesses it (catches "a score changed overnight").
- **Guardrail/safety** tests (kill-switch blocks, idempotency, caps).
- **Parity** tests (backtest↔live↔paper).
- Determinism spine: frozen clock, mocked Kite/LLM, in-memory cache — every test deterministic.
- CI gates merges on these; coverage floor on the money paths (scoring, sandbox, risk).

---

## 12. Runtime gates (everything is in scope; these are safety gates, not scope cuts)
All capabilities are built. These stay OFF at runtime until their gate passes:
- **Shorts/bearish** → enabled when backtest calibration shows positive bearish expectancy.
- **Auto-execute** → per-strategy, after a per-strategy paper-validation window.
- **Live trading** → only after the forward-test gate passes; semi-auto first.
- **Kelly sizing** → unlocked after ~6 months of validated data.
- **Options multi-leg phases** → unlocked by the strategy phase-progression.
- **Multi-process / Redis** → not needed for one user; documented Postgres-advisory-lock path exists if single-process ever saturates.

Locked: **corporate-actions source = Kite (splits/dividends) primary, NSE feed as
supplement** (DEC-B). See `DECISIONS.md` for all locked decisions.
