# Changelog

## [Unreleased] — Phase 1a: data foundation core (2026-11-19)

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
