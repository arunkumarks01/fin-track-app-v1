# Changelog

## [Unreleased] — Phase 0: scaffold & foundations (2026-11-19)

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

### Known / deferred
- `npm audit` reports PostCSS advisories in the copy bundled inside `next@15.5.x`. The fix requires Next 16 (breaks the locked Next 15). The affected code runs only at build time, on our own CSS. Revisit when upgrading.
- `ticks` retention (90 days by default) must exceed the Parquet export lag once Phase 1 adds the cold store.
- Rollup job (higher timeframes) and Parquet cold tier: Phase 1, not built in Phase 0.
