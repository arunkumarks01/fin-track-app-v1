# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FinTrack v2 — a single-user, learning-first NSE research / paper-trading / backtesting platform.
The plan lives in `docs/` and is **authoritative**: `ARCHITECTURE.md` (laws + design),
`SCOPE.md`, `DECISIONS.md` (locked choices — these govern over any skill or library default),
`BUILD_PLAN.md` (phases + exit gates), `TEST_PLAN.md`, `UX_DESIGN.md`, `TOOLING.md`.
Record every change in `docs/CHANGELOG.md`.

**Current phase: 0 (scaffold) complete.** Phases are gated: do not start the next phase (in
particular Phase 1 — any Kite call, ingestion, or populating candles) without explicit approval.

## Non-negotiable laws (ARCHITECTURE §2)

- **Data-first** — no indicator/score/signal code before the Phase-1 data gate passes.
- **Honest surfaces** — never show an uncertain number as certain; badge or exclude (e.g.
  `/health` returns 503 "degraded" rather than a fake ok; the frontend parses it strictly).
- **Deterministic money paths**; LLM only for news + explanation, always fail-closed.
- **One datastore** (TimescaleDB) + in-process memory. No Redis. **Single process** — no
  uvicorn workers, no `--reload` in prod.
- **Integer paisa** everywhere (`app/core/money.py` rejects floats and sub-paisa values);
  convert to ₹ only at the display edge.
- **Nothing hardcoded** — tunables come from `.env` via `app/core/config.py`. Exchange facts
  are constants, not config (e.g. `MARKET_TZ` in `app/core/tradingdate.py`).
- **Fail closed** on any error. **`PAPER_MODE=true`** is the only accepted value (config rejects anything else).
- **Locked stack:** Python 3.12 + FastAPI, raw **asyncpg (no ORM, no IoC container)**, ordered
  SQL migrations, uv, APScheduler 3.x, Next.js 15 + Tailwind + TanStack Query, Node 24.
  **No Docker/containers anywhere** (dev, CI, or deploy). Ask before adding any dependency.

## Commands

Backend (run from `backend/`):
```bash
uv sync                                   # install (CI uses --locked)
uv run ruff check . && uv run ruff format --check . && uv run mypy   # lint + strict types
uv run pytest -m "not db"                 # unit tests only (no DB needed)
uv run pytest                             # unit + DB integration
uv run pytest tests/unit/test_money.py::test_round_trip_is_lossless   # single test
uv run python -m app                      # run the API (single process)
```

Frontend (run from `frontend/`, Node 24 via `.nvmrc`):
```bash
npm ci
npm run lint && npm run format:check && npm run typecheck && npm test
npx vitest run src/lib/health.test.ts     # single test file
BACKEND_URL=http://127.0.0.1:8000 npm run dev    # build/dev fail without BACKEND_URL
```

Database: TimescaleDB 2.25.1 (**Apache build**) on PostgreSQL 18 lives on the VPS, and there's no local DB (D25) (`ssh fintrack-vps`), bound to
localhost only. Local dev reaches it through a tunnel:
```bash
ssh -N -L 5433:localhost:5432 fintrack-vps
```
DBs: `fintrack_v2` (app) and `fintrack_v2_test` (tests), owned by the least-privilege role
`fintrack_v2`. The VPS also hosts v1's `fintrack` DB and other apps' DBs — **never touch them**.
The `timescaledb` extension must be created by a superuser; the app role cannot.

## Architecture (big picture)

**Boot sequence** (`app/main.py`, lifespan — any failure aborts startup):
config → masked logging → asyncpg pool → `migrate()` → `apply_storage_policies()` →
APScheduler start → serve. `create_app(settings)` is a factory; tests pass explicit `Settings`.

**Migrations** (`app/db/migrate.py`, `app/db/migrations/NNNN_name.sql`): numbered contiguously,
each applied in its own transaction and recorded with a SHA-256 checksum in
`app.applied_migrations`. Editing an applied file, or a DB ahead of the code, aborts boot —
**never edit an applied migration; add a new one**. All app objects live in schema `app`.

**Apache-only TimescaleDB (D25).** The VPS runs Ubuntu's dfsg build, and CI forces
`timescaledb.license = 'apache'`: **never use TSL-only APIs** (compression/columnstore,
`add_retention_policy`, continuous aggregates, TimescaleDB background-job policies). The fallbacks:
- chunk intervals come from config at every boot (`app/db/policies.py`);
- retention is an in-process APScheduler job calling `drop_chunks` on `ticks` only
  (`app/db/retention.py`, `TICKS_RETENTION_CRON`); `ohlcv_candles` is never pruned, since
  daily history back to 2015 must survive;
- higher timeframes come from a scheduled rollup job and disk is handled by the Parquet cold tier (both Phase 1).

**Dedup key = trading identity.** `ohlcv_candles` is unique on `(symbol, timeframe, ts)`, and a
CHECK forces 1d bars to `ts` = 00:00 IST of the trading date (`canonical_daily_ts`). The same
day can never land twice. OHLC/positivity CHECKs are a DB backstop behind the (Phase-1)
validation/quarantine gate. A unit test asserts that the SQL zone matches `MARKET_TZ`.

**Auth** (`app/core/auth.py`): ASGI middleware requiring `Authorization: Bearer <API_AUTH_TOKEN>`
on every POST/PUT/PATCH/DELETE. It is fail-closed by construction: new mutating routes are protected
automatically, and any exemption (e.g. a future Telegram webhook) must be explicit.

**Scheduler** (`app/core/scheduler.py`): `max_instances=1` + `coalesce=True` are fixed; add jobs
only through `register_job()`, which refuses overrides. Timezone is IST. Jobs are registered in the
`app/main.py` lifespan (currently just the ticks retention job).

**Logging** (`app/core/logging.py`): one root handler whose formatter masks registered secrets
plus DSN passwords, bearer tokens and key=value credentials in the fully formatted record
(args and tracebacks included). `python -m app` runs uvicorn with `log_config=None` to keep it.

**Cache** (`app/core/cache.py`): `Cache` Protocol + bounded-LRU `MemoryCache` with TTLs on an
injected `Clock` (`app/core/clock.py`). Never a source of truth.

**Frontend**: the browser calls same-origin `/api/*`, which Next rewrites to `BACKEND_URL`
(`next.config.ts`). Responses are parsed strictly (`src/lib/health.ts`) — an unexpected shape is
an error, not a guessed value.

## Testing conventions

- Determinism seams in `backend/tests/fixtures/`: `FrozenClock`, `FakeKite` (kiteconnect
  method names, failure injection), `FakeLLM` (ok / timeout / error / malformed /
  schema-mismatch modes), `make_daily_candles()` (trend/chop/gap/split/bad-bar, integer paisa,
  canonical IST timestamps). Use them; tests never use wall-clock time or the network.
- Unit tests build settings with `build_settings(**overrides)` (`tests/conftest.py`), which never reads `.env`.
- DB tests are marked `db` (`tests/integration/`). They read `TEST_DATABASE_URL` from env/`.env`,
  **refuse any database whose name doesn't end in `_test`**, and drop + re-migrate schema `app`
  per test. Locally they skip without a DSN; with `REQUIRE_DB_TESTS=1` (CI) a missing DSN fails.
- Assert rejection paths, not just happy paths (fail-closed is the contract).

## CI

`.github/workflows/ci.yml` installs PostgreSQL 18 + TimescaleDB 2.25.1 **natively** on the
runner (apt, no `services:` containers, license forced to `apache`), provisions a throwaway `fintrack_v2_test`, then runs
ruff, ruff format, mypy, and pytest (backend), plus eslint, prettier, tsc, vitest and `next build` (frontend).
