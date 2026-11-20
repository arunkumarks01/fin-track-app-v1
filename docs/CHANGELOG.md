# Changelog

## [Unreleased] — Phase 1b: 1-minute intraday source + on-read derivation (2026-11-19)

### Added
- **Migration 0004:**
  - `app.ohlcv_1m`: the single stored intraday source. A compact hypertable with int4 paisa, smallint local `sid` and PK `(sid, ts)`, no default ts index, about 90 B/row.
  - `app.instrument_ids`: a stable smallint map for the universe (never Kite's bigint token).
  - `app.intraday_sessions`: per-symbol session counts.
  - `app.exchange_sessions`: the derived calendar (session open + benchmark minutes).
  - `app.intraday_cursor`.
  - quarantine reason `out_of_range`.
- **THE derivation** `app.intraday_bars()` (SQL) + `derive_bars()` (Python, the only entry point; whole sessions only). It uses `time_bucket(…, 'Asia/Kolkata', origin => session open)`. **Golden-tested against real Kite native 5m/15m/30m/60m bars: exact match** on timestamps, OHLC and volume, both symbols, a regular day and the 2024-11-01 **muhurat session (opened 18:00, which a fixed 09:15 origin would misalign)**.
- **1-minute validation:** minute alignment, positive/ordered prices, int4 range, duplicate minutes (every copy quarantined).
- **Newest-first round-robin extractor:**
  - every symbol's window *k* lands before any window *k+1* (round barrier)
  - bounded concurrency (`INTRADAY_CONCURRENCY`), with Kite's 3 req/s still enforced
  - resumable cursors; floors at `INTRADAY_FLOOR_DATE` or after `INTRADAY_EMPTY_WINDOWS_STOP` empty windows, so it never reaches back into pre-listing "ghost" history
  - **hard `INTRADAY_DB_BUDGET_GB` = 35 GB that fails closed**, with headroom for WAL, a local dump and the PITR archive documented in DEC-1B
- **Calendar-aware completeness:** expected minutes = the exchange benchmark's count that session, never a hardcoded 375. Sessions are complete / short / excess / unknown, plus missing sessions. Short sessions are **flagged for review, never auto-quarantined**.
- **API:**
  - `POST /api/data/ingest/intraday` (bearer, background, live progress)
  - `GET /api/data/coverage/intraday[?review=SYMBOL]`
  - `GET /api/data/bars?symbol&timeframe&start&end` (≤31 days)
  - `INTRADAY_INGEST_CRON` 16:40 IST
- **Reliability:**
  - any DB-connectivity error is an **honest 503 "database unavailable"** on every endpoint (it was a raw 500 on `/api/data/coverage`)
  - the intraday job **auto-resumes after a lost DB** (`INTRADAY_DB_RETRIES` × `INTRADAY_DB_RETRY_WAIT_SECONDS`) but never retries a budget or auth stop
- **Frontend:** the panel shows live 1-minute progress (rounds, windows, bars added, how far back every symbol reaches, DB size vs budget).

### Changed
- ARCHITECTURE §5 / BUILD_PLAN / SCOPE / CLAUDE.md: the derivation of record is on-read session-anchored `time_bucket` (DEC-1B), superseding the "scheduled rollup job". §5 corrected from a fixed 09:15 origin to the **per-session open**. 1d stays Kite-native (NSE's official close ≠ the last 1m close).
- **v1 retired** (DEC-V1R): its services were stopped and disabled and its 31 GB database dropped (no backup, at the user's request). VPS free disk went from 27 GB to 58 GB.

### Fixed
- **An overflowing volume crashed the whole backfill instead of being quarantined.** On NSE:COROMANDEL, 2015-03-13 09:15, Kite returned a volume of `18446744073709551614` (2⁶⁴−2, an unsigned underflow). The minute validator bounded prices but not volume, so the bigint `COPY` raised `OverflowError` after 2h20m and 206.8M bars. Both validators now reject any value beyond bigint as `out_of_range`; the daily one checks prices, volume and OI. Covered by unit cases using the real payload, plus a DB regression test showing the run completes and quarantines exactly that minute. Cursors were intact, so the run resumed where it stopped.

- **`/api/data/coverage/intraday` took over 5 minutes on real data.** Its `missing_sessions` used a correlated anti-join per exchange session against an unindexed CTE, which is quadratic. It now uses a linear count: benchmark sessions in each symbol's span minus that symbol's matched sessions (exact, because `(sid, session_date)` is unique). The report now takes **0.49 s**. A new DB test pins the semantics: a mid-span hole counts, and sessions before a late listing don't.

### Real-data results (2026-11-20, VPS `fintrack_v2`)
- The backfill finished: status ok, 0 symbol errors, every symbol walked back to its floor (newest-first). The first run reached 2015 before the overflow crash; the resume took 2 rounds and 225 windows.
- **210,756,908 1-minute bars**, 224 symbols, **2015-01-09 → 2026-11-19**, database **19.96 GB of the 35 GB budget**. 156 symbols reach back to early 2015; the other 68 start at their listing date (2016–2025).
- 566,709 symbol-sessions:
  - 508,832 complete, 41,637 short, 16,240 excess, 0 unknown
  - 289 missing sessions, and 788,645 missing minutes
- 1m quarantine: 39,703 `non_canonical_ts`, 24,475 `non_positive_price`, 39 `ohlc_order`, 1 `out_of_range`.
- **The intraday gate is NOT green.** Findings that need a decision (below).

### Findings needing a decision (gate stays closed)
1. **The benchmark is not a reliable session calendar.** NIFTY 50's own 1m series is gappy:
   - 254–280 minutes on many 2015 sessions
   - 336 on 2017-02-21, 374 on 2023-06-05, 368 on 2023-06-14
   - 54 on 2021-02-24, NSE's outage day, when equities show 224

   On 107 dates, ≥100 symbols "exceed" the benchmark. That accounts for 16,154 of the 16,240 excess sessions. On those dates, a stock with the same gaps would be falsely "complete", which breaks the honest-surfaces law. The DEC-1B rule "expected = benchmark count" needs revising.
2. **Since 2026-08-03, every NSE equity's 1m series ends at 15:14 while indices run to 15:29.** This holds on all 76 sessions since then (360 vs 375). It might be a real market-structure change (for example a closing-auction session) or a Kite feed defect; this isn't verified yet. It makes about 16.5k sessions "short by 15". RELIANCE's 360/375 on 2026-11-18 is this same effect.
3. **Kite's 2015 1m data is dirty:**
   - 39,703 bars stamped a second or more past the minute (for example 12:48:01). All are from 2015, across 155 symbols, and they're quarantined, which leaves holes.
   - Whole sessions of zero prices: ICICIBANK, INFY and ITC on 2015-04-27 → 05-13 (4,500 bars each).
4. FORCEMOT has a contiguous 76-session hole at the source (2023-10-26 → 2024-02-13); daily has the same gap. It's correctly flagged for review.

### Observed on real data
- Kite's 1-minute history starts in the first half of 2015 (none on 2015-01-06; full sessions from 2015-06-02).
- RELIANCE on 2026-11-18 has 360/375 minutes: 15:15–15:29 is missing at the source, and Kite's own 5m bars stop at 15:10.
- Throughput: 0.42 windows/s sequential (2.4 s/window, latency-bound) → 1.26 windows/s with 4 in flight (about 18.7k rows/s).
- The dev SSH tunnel is reset by the network path roughly every 25–60 minutes. This is handled by a supervised tunnel plus job auto-resume; running the backend on the VPS (Phase 9) removes the tunnel entirely.

## Phase 1a: data foundation core (2026-11-19)

### Added
- **Migration 0002**: `instruments`, `universe`, `kite_session`, `ingest_cursor`, `ohlcv_quarantine`, `ohlcv_flags`.
- **Kite auth** (ported from v1): login URL with a one-time `state` nonce (CSRF), callback token exchange, honest `/api/kite/status` (not_configured / disconnected / expired / connected + hours left), bearer-protected `/api/kite/logout` (local + remote invalidation). The session persists in Postgres plus memory, and the token is masked in logs.
- **Kite REST client** (`httpx`, DEC-K2): per-endpoint token-bucket rate limits, exponential backoff on 429/5xx/network, fail-closed on `TokenException` only. Exact `Decimal` → paisa parsing.
- **Instrument master + F&O universe**: atomic snapshot replace; universe = NFO stock-futures underlyings ∩ NSE EQ + 6 configured indices. A missing configured index or a malformed relevant row fails the refresh.
- **Validation gate**: quarantine with reason + raw payload; duplicate dates quarantine every copy; large-gap / missing-session flags (flag-but-keep).
- **Daily extractor**: resumable, idempotent, one transaction per Kite window. Never ingests a partial day; picks up late-published bars; revised bars are quarantined, not overwritten; an auth error stops the run.
- **Coverage report** (the Phase-1 gate): `GET /api/data/coverage` with per-symbol bars / oldest / newest / gap% / staleness / scorable. Checks: ≥95% of symbols with ≥250 clean bars, indices back to 2015, 0 duplicate dates. An empty universe fails.
- **In-process ingestion jobs**: `POST /api/data/ingest/daily` (bearer, background, one at a time), `GET /api/data/jobs`, scheduled `DAILY_INGEST_CRON` (16:15 IST weekdays).
- Config: Kite, universe, ingestion, validation and coverage tunables (all in `.env.example`).

### Changed
- `httpx` moved from dev-only to a runtime dependency (DEC-K2).
- Decisions recorded: DEC-K1 (reuse v1's Kite app, redirect repointed), DEC-K2, DEC-V1 (re-ingest; v1 data unused), DEC-P1 (Phase 1 sliced 1a–1e).

### Changed: validation v2 (migration 0003), learned from the first real Kite backfill
- **Legacy daily timestamps:** Kite stamps pre-Dec-2018 daily bars for some symbols at 09:15 IST or other odd times. These are now **re-stamped to 00:00 IST of their IST trading date** and flagged `retimed_ts`, with the original timestamp kept in the flag. If a native 00:00 bar exists for that date, it wins and the re-stamped copy is quarantined. Any other multiplicity quarantines every copy.
- **Index gaps:** a >`VALIDATION_INDEX_MAX_GAP_PCT` (20%) day-over-day gap on an **index** is quarantined as `index_gap`; indices have no corporate actions. Equities keep flag-but-keep.
- **Coverage gate:** a symbol is *sufficient* only with ≥250 clean bars **and** ≤`COVERAGE_MAX_WEEKDAY_GAP_PCT` (10%) of weekdays missing; holidays alone are ≈5%. Indices must be both deep and hole-free. Quarantine counts only **unresolved** rows. Re-quarantining the same bad row is deduplicated.
- **Targeted re-processing:** `POST /api/data/ingest/daily?symbols=…&reset_cursor=true&refresh_universe=false` re-fetches only the listed symbols. Stored bars are never touched; a reset with no explicit symbol list is refused.

### Real-data results (2026-11-19, VPS `fintrack_v2`)
- **Initial backfill:** 56,249 instruments; universe **224** (218 F&O stocks + 6 indices); **5,69,181** clean daily bars in 672 Kite requests, about 9 minutes, 0 errors. The gate **failed** on NIFTY BANK depth; 4,613 bars across 8 symbols were quarantined for legacy timestamps.
- **After validation v2 + targeted re-process** (8 symbols, 16 s): 4,592 bars recovered. **Coverage gate PASS**:
  - 221/224 sufficient (98.7%), all 6 indices from 2015-01-01 with 5.0–5.2% gaps
  - 0 duplicate dates, 0 stale
  - **5,73,773** clean daily bars
- **Still quarantined (correctly):**
  - MAZDOCK's 8 all-zero pre-listing rows.
  - 4 trading days (2015-06-29, 07-02, 08-10, 11-11) where Kite sent **two conflicting** off-midnight bars for NIFTY BANK and NIFTY FIN SERVICE; both copies are kept out. The unresolved count is per row, so these 4 days show as 16 rows per index.
- **Not sufficient (honestly):**
  - **360ONE, SBICARD, DELHIVERY** carry *ghost history* from a different, earlier security on the same token: a few bars in 2015–2016, then a multi-year gap until their real listing. They are excluded from scoring; removing the ghost segments is a pending decision.

### Fixed
- `.gitignore` had a bare `data/` rule (meant for a future Parquet dir) that also matched `backend/app/data/`. The data package would have been left out of git, **and ruff silently skipped it** because ruff honours `.gitignore`. The rule is now anchored as `/data/`; ruff covers all 64 files, and 6 lint issues it had been hiding are fixed. Verified by exporting the staged tree to a clean folder and running the full CI suite there.

### Open (to resolve in 1c)
- Kite's historical candles are believed to be **already split/bonus-adjusted**, which conflicts with "store raw as-traded". Re-adjustments surface as `conflicts_with_stored` quarantine rows rather than overwrites. Slice 1c must settle raw-vs-adjusted against real data.

## Phase 0: scaffold & foundations (2026-11-19)

### Added
- **Repo** — `backend/` (uv, Python 3.12) + `frontend/` (Next.js 15, Node 24), root `.env.example`, `.nvmrc`, `CLAUDE.md`.
- **Config** (`app/core/config.py`) — every tunable comes from `.env`, and invalid config fails at startup. `PAPER_MODE` only accepts `true`. `API_AUTH_TOKEN` needs at least 32 chars. The DSN scheme is validated.
- **Money** (`app/core/money.py`) — integer-paisa conversions that reject floats and sub-paisa values (no silent rounding).
- **Masked logging** — registered secrets plus DSN passwords, bearer tokens and key=value credentials are masked across message, args and tracebacks.
- **Clock + cache** — injectable `Clock`; `Cache` Protocol with a bounded-LRU `MemoryCache` (get/set/setex/delete with TTL).
- **DB** — ordered, checksummed SQL migrations recorded in `app.applied_migrations`. They fail closed on an edited migration, a DB ahead of the code, or a missing timescaledb extension. Migration `0001_init` adds two hypertables:
  - `ohlcv_candles`, dedup key `(symbol, timeframe, ts)`, with a CHECK forcing 1d bars to 00:00 IST
  - `ticks`
  Chunk intervals come from config at boot. Retention is an in-process `drop_chunks` job on `ticks` (`TICKS_RETENTION_CRON`). There is no retention on OHLCV.
- **App** — single-process FastAPI. Boot order: config → logging → pool → migrations → chunk sizing → scheduler (with the retention job). APScheduler is locked to `max_instances=1` and `coalesce`. `/health` reports degraded (503) honestly. Bearer-token middleware guards every mutating method.
- **Test seams** — `FrozenClock`, `FakeKite`, `FakeLLM` (5 modes), deterministic candle factory (trend/chop/gap/split/bad-bar). The DB test harness refuses any database not named `*_test`.
- **Frontend** — Tailwind, TanStack Query provider, a strict `/health` contract parser, a health panel, and a `/api` rewrite to `BACKEND_URL`. The build fails without `BACKEND_URL`. Tooling: ESLint + Prettier + `tsc --strict` + vitest.
- **CI** — GitHub Actions with native PostgreSQL 18 + TimescaleDB 2.25.1, license forced to `apache` to match the VPS (no containers): ruff, ruff format, mypy --strict, pytest (unit + DB); eslint, prettier, tsc, vitest, next build.
- **Infra** — `fintrack_v2` role (least privilege) plus `fintrack_v2` and `fintrack_v2_test` databases created on the VPS, with TimescaleDB 2.25.1 on PG 18.

### Changed
- **Apache-only TimescaleDB (D25).** The VPS runs Ubuntu's dfsg build, which has no TSL module. That was found when migration 0001 failed with `FeatureNotSupportedError … "apache" license`. Changes:
  - columnstore settings and compression/retention policies removed from 0001 and `policies.py`;
  - retention is now a scheduled `drop_chunks` job;
  - rollups and the Parquet cold tier are recorded as Phase 1 deliverables (BUILD_PLAN);
  - `OHLCV_/TICKS_COMPRESS_AFTER_DAYS` removed and `TICKS_RETENTION_CRON` added.
- DSN validation now rejects an unescaped `@` in the password, with a clear "URL-encode the password" error.
- Node **24 LTS** instead of 20: Node 20 reached end-of-life in April 2026 (recorded in DECISIONS).
- `MARKET_TZ` is a code constant (IST is an exchange fact, and the DB CHECK encodes it), not a `.env` tunable.

### Fixed
- CI backend job: GitHub's runner image doesn't auto-create a PostgreSQL cluster on install, so the workflow now runs `pg_createcluster 18 main` and writes its settings to `conf.d`. `actions/checkout` bumped to v5 (Node 20 deprecation).

### Known / deferred
- `npm audit` reports PostCSS advisories in the copy bundled inside `next@15.5.x`. The fix requires Next 16 (breaks the locked Next 15). The affected code runs only at build time, on our own CSS. Revisit when upgrading.
- `ticks` retention (90 days by default) must exceed the Parquet export lag once Phase 1 adds the cold store.
- Rollup job (higher timeframes) and Parquet cold tier: Phase 1, not built in Phase 0.
