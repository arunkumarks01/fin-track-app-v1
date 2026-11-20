# FinTrack v2 — Decision Log

> Created 2026-11-19. Every scope decision (SCOPE.md D1–D26 + DEC-B) locked with the chosen
> answer. Defaults chosen by the architect from the full v1→v2 context; redirect any you
> disagree with and this log + the affected docs update. Config *values* marked "confirm"
> are sensible starting points, tune freely.

## Locked decisions

| ID | Topic | Locked choice |
|----|-------|---------------|
| D1 | Tick storage | **Yes** — hypertable + **Parquet cold** (primary space saver); native compression only if/when the VPS runs the full Timescale build. Required for slippage calibration |
| D2 | Options-chain cadence | Every **5 min** during market hours (OI/IV/greeks snapshots) |
| D3 | Global/VIX source | **Kite** where available; licensed vendor fallback; **not Yahoo** for NSE data |
| D4 | Scoring cadence | Swing **09:20 + EOD 15:40**; intraday every **5 min 09:15–15:00** |
| D5 | Conviction weights | **Calibrated** from `backtest_trades` (→ win-prob), not hand-set |
| D6 | Shorts | **Built from the start**; `SHORTS_ENABLED` runtime flag **default off** until bearish expectancy proven |
| D7 | Options multi-leg | **In scope**; phase-unlocked + payoff-diagram education |
| D8 | Custom strategy | DB/JSON strategy definitions (name, timeframe, detector + params, enabled, exec mode) — schema detailed in Phase 3 design |
| D9 | Default sizing mode | **Fixed-% risk** |
| D10 | Paper capital, caps & paper risk posture | **Paper capital ₹1 cr.** Caps (risk/trade **1%**, max-cap/trade **10%**, concurrent **5**, buffer **20%**, daily circuit-breaker **3%**, trades/day **5**) apply in **Realistic mode** (forward-test/live). **Learning mode never blocks a trade** — any trade is allowed; risk (SL, target, ₹-at-risk) is **always displayed**, never enforced. One engine, a `PAPER_SUBMODE` toggle (Learning default; Realistic required for Phase-10 forward-test). |
| D11 | Kelly sizing | In scope; **locked until ~6 months** validated data |
| D12 | Slippage model | **Static** until tick data exists, then **tick-calibrated** |
| D13 | Partial fills | Model fills capped at a configurable **% of bar/available volume** |
| D14 | Backtest engine | **Event-driven bar-replay** (shares the live path → parity) |
| D15 | Regime labels | Rule-based from **VIX + Nifty trend** (bull / bear / sideways / high-vol) |
| D16 | Live auto/semi | **Semi-auto only** at first live; auto after a further validation window |
| D17 | LLM provider | **Claude CLI now → Groq/paid later**, one abstraction + circuit-breaker + schema validation; fail-closed |
| D18 | Agents — phase-pinned (all in scope) | **News/Research → Phase 3**; **Explainer + Q&A-over-data + Market-commentary → Phase 8**; **Options-flow analyst → Phase 12**. "Later" = a named, gated phase (dependency-sequenced), never a drop. |
| D19 | Curriculum | Modules: market basics, order types, TA, FA, options basics, risk mgmt, per-strategy explainers, pre-open microstructure — detailed in Phase 8 |
| D20 | Beginner/Pro | **Beginner default**, persisted per device |
| D21 | Candlestick lib | **lightweight-charts** (TradingView) — performant, proven |
| D22 | PWA/mobile | **PWA yes**; native app later |
| D23 | Deploy | **Single VPS, native — no Docker/containers** — behind Tailscale: systemd-managed uvicorn (backend) + Next.js, Caddy/nginx reverse proxy for TLS, TimescaleDB installed natively; bearer-token on mutating routes; no `--reload` in prod |
| D24 | Backup cadence | **Nightly `pg_dump` + continuous WAL archiving (PITR)**; one copy off-box |
| D25 | Postgres host | **VPS TimescaleDB only — no local DB** (dev/test over SSH tunnel; see DEC-DB). Runs the **Apache build 2.25.1** → compression/retention/continuous-aggregates via **fallbacks**, not TSL. Backups/PITR per D24. Full-build upgrade = a deferred, optional, snapshot-first shared-instance maintenance (touches v1/tabsettle/vidforge), only if fallbacks prove insufficient |
| D26 | Notification channels | **Telegram only** for v2; email/push later |
| DEC-N | Node runtime | **Node 24 LTS** (Node 20 reached end-of-life in April 2026). Pinned via `.nvmrc` + `engines` (2026-11-19) |
| DEC-DB | Dev/test database host | Existing **VPS TimescaleDB 2.25.1 / PG 18**, `fintrack_v2` + `fintrack_v2_test` DBs, least-privilege role, reached via SSH tunnel; CI installs the same versions natively on the runner **with `timescaledb.license = 'apache'`**, so TSL-only APIs fail in CI exactly as on the VPS (2026-11-19) |
| DEC-K1 | Kite Connect app | **Reuse v1's Kite app, redirect repointed to v2** (`http://127.0.0.1:8000/api/kite/callback`); v1's Kite login stops working (2026-11-19) |
| DEC-K2 | Kite client | **`httpx` direct** (async, owned retry/rate-limit/fail-closed paths), not the sync `kiteconnect` lib; WebSocket client chosen at Phase 1d. Per-request windows (Kite doesn't publish them; v1-proven): minute/3m 60d · 5m 100d · 15m/30m 200d · 60m 400d · day 2000d — config tunables (2026-11-19) |
| DEC-V1 | v1 candle data | **Re-ingest everything from Kite; v1's data is not imported or used** (2026-11-19) |
| DEC-P1 | Phase 1 delivery | **Whole of Phase 1 approved**, delivered as slices 1a (auth, instruments, daily, validation, coverage gate) → 1b (intraday + rollups) → 1c (corporate actions + adjust-on-read) → 1d (ticks, options chain, Parquet) → 1e (reference data — **sources proposed and approved before build**) (2026-11-19) |
| DEC-V1R | Retire v1 | **v1 retired 2026-11-19 at user request, without a backup**: `fintrack-backend` / `fintrack-frontend` / `fintrack-gate` stopped + disabled, `DROP DATABASE fintrack` (31 GB) → VPS free disk 27 → 58 GB. Left in place: v1 code, venv, unit files, oauth2-proxy config, `fintrack` role, `pg_hba` lines; fintrack.arunkumarks.com is offline |
| DEC-1B | Intraday storage + derivation | **1-minute is the single stored intraday source**, in a compact hypertable `app.ohlcv_1m` (int4 paisa, smallint **local** id from the stable `app.instrument_ids` map — not Kite's bigint token, ~90 B/row). Depth: **as far back as Kite serves** (~mid-2015), fetched **newest-first in round-robin windows across all symbols**, resumable, under a hard **35 GB DB budget** (58 GB free = 35 DB + ~3 WAL + ~7 one local dump + ~13 WAL archive/PITR for D24) that fails closed. **3m/5m/15m/30m/1h are derived on read** by ONE SQL function `app.intraday_bars()` using `time_bucket(…, 'Asia/Kolkata', origin => session open)` — anchored to **each session's actual open** from the derived calendar `app.exchange_sessions` (a fixed 09:15 origin is wrong for muhurat sessions). Live and backtest **must** use this function (no pandas resample). Golden-tested to reproduce Kite's native 5m/15m/30m/60m bars **exactly**. **1d stays Kite's native daily bar** (NSE's official close ≠ the last 1m close). Completeness is **calendar-aware**: expected minutes = the exchange benchmark's count that session (NIFTY 50 / SENSEX), so short sessions are flagged for review, never auto-quarantined. A materialized hot window is the escape hatch **only if** on-read latency hurts. Supersedes the "scheduled rollup job" fallback (2026-11-19) |
| DEC-B | Corporate actions source | **Kite (splits/dividends) primary**, NSE corporate-actions feed as supplement/fallback |

## Standing architectural locks (from ARCHITECTURE.md)
Single-process · TimescaleDB + in-memory (no Redis) · integer paisa · deterministic scoring,
LLM only for news + explanation (fail-closed) · DB-sourced guardrails · dedup-by-trading-date
· raw + adjust-on-read · golden/regression/parity tests from day one · PAPER_MODE default,
live behind the forward-test gate.

## To confirm from you (values only — not blockers)
- Tune if you like: expectancy floor (**+0.25R** default) and max-drawdown ceiling (**12%** default) in the Phase-10 gate; paper caps (D10) and ₹1 cr capital are set.

## Phase-10 forward-test gate (locked — tune #2/#5 to appetite)
All must hold before live: (1) ≥100 closed paper trades, ≥20/strategy, ≥4 wks, profitable ≥3
of 4 wks; (2) expectancy **≥ +0.25R net of costs**, positive after dropping top 2 winners;
(3) profit factor ≥ 1.3; (4) win-rate within ±10pp of backtest (consistency, not an absolute
gate); (5) max drawdown ≤ 12% of paper capital; (6) modeled fill within ±25% of real printed
post-signal price, no systematic optimism; (7) forward expectancy ≥ 65% of backtested;
(8) zero unexplained orders/incidents, kill-switch + dead-man's-switch verified, 100%
reconciled; (9) small live pilot (min size, semi-auto) measures real slippage before full live.
**Win-rate is a cross-check, not the gate — expectancy is. Paper can't fully prove slippage —
hence the live pilot.**
