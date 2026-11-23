# FinTrack v2 — Build Plan (full scope, sequenced)

> Created 2026-11-19. The complete scope (SCOPE.md), ordered by dependency. Everything is in
> scope; this is the order of construction, not a scope cut. Each phase has an exit gate that
> must be green before the next starts — that discipline is what prevents a fixing spree.
> Decisions locked in DECISIONS.md; design in ARCHITECTURE.md.

## The rule
Trust is bottom-up: clean data → correct indicators → correct scoring → sound risk → honest
fills → honest backtest → honest UI → forward-test → live. No phase starts until the phase
below is **proven** (gate green), not merely written. `PAPER_MODE=true` throughout; live is
Phase 11 and only after Phase 10 passes.

---

**Phase 0 — Scaffold & foundations (~1 wk).** Repo, `core/config.py` (all from `.env`, paisa
helpers), masked logging, single-process FastAPI, APScheduler `max_instances=1`, DB schema
(hypertables, Apache-build compatible; chunk sizing + in-process `drop_chunks` retention job), in-memory cache layer, test harness (frozen-clock /
mock-Kite / mock-LLM, candle factory), CI. *Gate:* boots; lint/type clean; CI green.

**Phase 1 — Data foundation (~2–3 wk) — keystone.** Ingest equity/index/futures/options
candles (daily→2015, intraday to Kite limit) + **tick storage** + **options chain** +
corporate actions (Kite primary) + fundamentals + FII/DII + bulk deals + economic calendar +
global/VIX + news. Dedup-by-trading-date, **1m as the single intraday source with higher timeframes derived on read by a session-anchored `time_bucket` function** (DEC-1B; supersedes the earlier "scheduled rollup job" fallback for continuous aggregates; same single-source derivation), raw + adjust-on-read,
validation + quarantine, resumable rate-limited extractor, Parquet cold store (the primary space saver; ticks exported before the retention job drops them). *Gate:*
coverage report green (≥95% symbols ≥250 clean daily bars, indices→2015, 0 dupes). **No
scoring before this passes.**

**Phase 1 — remaining work, in order (status 2026-11-21).** Slices 1a, 1b, 1c are built; the data gate
was opened 2026-11-20 (DEC-GATE1). Details live where linked; this list is only the order.
1. **1e — reference data** (DEC-1E-SOURCES, `PROPOSAL_1E_SOURCES.md` "Order of work"). Delivery,
   participant positions, deals, RSS, announcements, board meetings, market cap, calendar import
   and FRED ingestion are implemented. Retry previously errored participant files, load the PR history,
   and verify live coverage. GIFT Nifty and India VIX daily and minute history are wired into the
   extractors.
   MCX commodities are paused at the user's request. CDS history is reachable and requires a
   decision on exact sub-paisa prices.
   Live FRED ingestion waits for the key, and the calendar uses a reviewed CSV.
   **Priority 2026-11-22:** resolve the 27 remaining participant-file errors. Preserve
   fractional NSE contract figures exactly, identify how to surface source totals that
   disagree with their components, verify title/date anomalies against source evidence,
   and retry recoverable files. Keep unresolved reports explicitly flagged.
2. **Monday 2026-11-23:** Kite login → full-session tick probe to size storage under DEC-1D-*.
3. **Daily re-measure** — the full-fit speedup is implemented. Check the full morning job's timing
   before 1d is live so it ends before pre-open capture. Incremental re-fit remains a separate follow-up.
4. **1d — ticks, option chain, Parquet cold tier** — built 2026-11-21 (DEC-1D-*), tested on recorded/synthetic data only.
   **Monday 2026-11-23:** verify on live Kite (real packet layout, tick volume per minute, export of the first full day), then tune
   `TICKS_HOT_DAYS` and the buffer sizes. From 2026-12-07 the window already covers derivatives to 15:45.
5. **1f — F&O history** — built 2026-11-21 (NSE F&O archive, `app.fo_daily`); goes live when the backend is restarted after the 1e
   download finishes. Kite active contracts are covered by 1d.
6. **Backfill database safeguards (planned):** configure an idle-in-transaction timeout on
   FinTrack's PostgreSQL connections so an abandoned write releases its locks. Bound lock waits
   during backfill writes and show a specific error in Admin if retries are exhausted. Resume
   from the committed file ledger after a retry. Verify with two database sessions that an
   abandoned transaction releases its lock, committed rows survive, and retrying the interrupted
   file adds no duplicates.
7. **Phase 1 close-out:** every slice's coverage rows green on Admin → Phase 2.

**Phase 2 — Indicators + analytics + tests (~1–2 wk).** Full TA suite (config-driven warmup,
EMA200 only ≥200 bars) + options analytics (PCR, max-pain, gamma, IV rank). Golden tests vs
reference lib, convergence tests, regression-snapshot harness seeded. *Gate:* golden +
convergence green on real Phase-1 data.

**Phase 2 workspace implementation.** Technical analytics and options analytics use read-only
research endpoints. Technical checks use stored NSE candles and a recorded Kite minute fixture.
Options checks use independently priced fixtures and TimescaleDB integration tests. Live
option-chain validation and observations for the configured IV-rank tenor remain part of the
exit gate. Complete the Phase 1 close-out before deploying the analytics.

**Phase 3 — Scoring pipeline + strategies + calibration (~2–3 wk).** Deterministic swing +
intraday pipelines; **full strategy catalogue, long + short** (shorts behind runtime flag);
one calibrated conviction shared across both; News/research LLM agent (fail-closed); custom-
strategy config. *Gate:* every candidate number reproducible by hand; same inputs → same
conviction across paths; regression snapshot blessed.

**Phase 3 candidate explanation.** Each suggestion must include a visible explanation when
it first appears. Use the same evaluated facts as the scorer. Show the strategy and direction,
the trigger and timeframe, each rule's observed value and threshold, the data timestamp and
any missing or stale inputs, and the condition that invalidates the setup when the strategy
defines one. For a futures or options suggestion, show how the specific contract qualified,
including the available expiry, strike, liquidity and pricing checks. Explain the conviction
score with its calibration evidence when available and label it uncalibrated otherwise.
Phase 8 adds guided lessons and the on-demand Explainer agent.

**Phase 3 local preview.** Candidates evaluates stored finalized daily and 5-minute
candles. Validate the live 5-minute input and actual Kite contract snapshots before enabling
scheduled scans. Daily futures checks require verified 1f data. Conviction stays uncalibrated
until backtest evaluation publishes enough outcomes for the applicable strategy revision and
instrument. The news provider needs a live acceptance check before enabling it.

**Phase 4 — Risk & portfolio (~1–2 wk).** Sizing modes (fixed-% default, fixed-lots, Kelly-
gated), risk-% vs notional-cap, lot-aware, correlation-aware sizing, portfolio caps + daily-
loss circuit breaker, structure/ATR targets. *Gate:* sizing reproducible; caps enforced in
tests.

**Phase 4 implementation status.** Paper trade plans and portfolio check calculations are
verified on held fixtures and stored candles. Active portfolio balances/display, measured
correlation and broker margin require their data inputs. Kelly stays gated until measured
outcomes meet the configured validated-history minimum. Active trailing uses an actual
paper fill and subsequent prices in Phase 5.

**Phase 5 — Paper sandbox + safety (~2 wk).** Order types (MARKET/LIMIT/SL-M/GTT), fills with
slippage + full charge stack + partial fills, MTM, TSL, EOD square-off, SEMI/FULL-auto per
strategy; DB-sourced guardrails, idempotency, persisted kill-switch/pause, security
incidents, auth + webhook secret. *Gate:* guardrail/kill-switch/idempotency tests green;
paper P&L matches hand-computed cost example.

**Phase 5 manual replay slice.** The Paper trading workspace simulates NSE equity fills on
finalized as-traded daily bars. Replay accounts, orders, fills, positions, transaction charges,
P&L and safety controls persist in the paper ledger. Account-specific fill assumptions are
fixed at creation so a configuration change does not change an existing replay. Validate
live marking, derivative contracts and automatic stop/target/trailing and approval flows
before closing the full phase.

**Phase 6 — Backtesting (~2–3 wk).** Signal + **portfolio** backtesting, walk-forward
(in/out-of-sample), same cost model, chronological drawdown, **backtest↔live↔paper parity**,
segment analysis (strategy×beta×sector×time×regime), slippage calibration from ticks.
Calibration output feeds Phase 3 conviction + the explainer. *Gate:* parity green; backtest
P&L == paper P&L same trade+costs; zero-edge fixture net-negative after costs.

**Phase 7 — Options multi-leg (~2 wk).** Spreads, iron condor, straddle/strangle, covered
call, calendar; payoff-diagram engine; phase-unlock progression. *Gate:* payoff math
validated; multi-leg fills + margin modeled in the sandbox.

**Phase 8 — Frontend + learning layer + agents (~3–4 wk).** Design system (one formatter,
primitives, Beginner/Pro, dark/light), honest-UI, full nav (Dashboard, Journal, Backtest,
Analytics, **Options** flow, Learn, Settings, Admin), charts (openalgo-charts per DEC-CHART + Recharts +
payoff), responsive + mobile tabs + PWA. Learning: "Why this trade?" w/ history, risk slider,
payoff diagrams, pre-open tutorial, prediction-vs-outcome journal, glossary, curriculum.
Explainer + Q&A + commentary agents. *Gate:* `tsc` clean; a beginner understands any
candidate's why + ₹-risk; no UI number contradicts the backend.

**Phase 9 — Observability & ops hardening (~1–2 wk).** Metrics, error tracking, dead-man's-
switch, health dashboard; backups/PITR + replica; native prod deploy (systemd units + Caddy/nginx reverse proxy) behind
Tailscale. *Gate:* restore-from-backup tested; dead-man's-switch fires on a stalled scheduler.

**Phase 10 — Forward-test gate (4–6 wk paper, calendar).** Reconciliation (signal vs fill vs
modeled slippage), realized win-rate/expectancy vs backtest, monitoring, daily candidate-
quality report. **Pass criteria (all must hold; tune #2/#5 to risk appetite):**
(1) ≥100 closed paper trades total, ≥20/strategy, ≥4 weeks, profitable in ≥3 of 4 weeks;
(2) expectancy **≥ +0.25R net of costs**, still positive after dropping the top 2 winners;
(3) profit factor ≥ 1.3; (4) win-rate within ±10pp of the strategy's backtest (consistency,
not an absolute gate); (5) max drawdown ≤ 12% of paper capital; (6) modeled fill within ±25%
of the real printed post-signal price, no systematic optimism; (7) forward expectancy ≥ 65%
of backtested (overfit check); (8) zero unexplained orders/incidents, kill-switch + dead-
man's-switch verified, 100% trades reconciled; (9) a **small live pilot** (min size, semi-
auto, real orders) to measure real slippage before full-size live. *Gate:* all nine → go/no-go.

**Phase 11 — Live trading (gated).** Kite order placement, funds/margin, recovery, live P&L;
semi-auto first; Telegram confirm on first live trade. *Only after Phase 10 passes.*

**Phase 12 — Advanced.** Sector rotation, correlation/portfolio heatmaps, options-flow
dashboards (IV surface, max-pain heatmap), Kelly (post-data), multi-broker, native app.

---

## Standing rules
`PAPER_MODE=true` until Phase 10 passes · every change lands with its test, CI gates merges ·
nothing hardcoded, integer paisa · update `CHANGELOG.md` · destructive/outward ops snapshot-
first + confirm · a phase's gate must be green before the next starts.

## Timeline (rough)
~16–22 engineering weeks through Phase 9, + the 4–6 week forward-test window, before live is
on the table. Front-loaded on data (Phases 0–1) so the back half is assembly, not firefighting.
