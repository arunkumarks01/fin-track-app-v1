# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FinTrack v2 — a single-user, learning-first NSE research / paper-trading / backtesting platform.
The plan lives in `docs/` and is **authoritative**: `ARCHITECTURE.md` (laws + design),
`SCOPE.md`, `DECISIONS.md` (locked choices — these govern over any skill or library default),
`BUILD_PLAN.md` (phases + exit gates), `TEST_PLAN.md`, `UX_DESIGN.md`, `TOOLING.md`.
Record every change in `docs/CHANGELOG.md`.

**Current phase: 1 (data foundation), approved as a whole (DEC-P1), built in slices 1a→1e.**
Slices 1a (Kite auth, universe, daily extractor, validation, coverage gate), 1b (1-minute store +
on-read derivation) and 1c (NSE archive, measured adjustments, drift re-check) are built.
**The Phase-1 data-coverage gate was opened by the user on 2026-11-20 (DEC-GATE1).** The five 1d decisions are
recorded as **provisional** (DEC-1D-*): `websockets` + `pyarrow`; Full-mode ticks, compact schema, short hot window,
fail-closed Parquet export; a narrow option chain; raw snapshots only (greeks in Phase 2); new slice 1f (F&O
history from the archive). Tick sizing waits on a full-session probe; build 1d only after the user locks them. 1e (reference data) sources are
**approved 2026-11-21** (DEC-1E-SOURCES, `docs/PROPOSAL_1E_SOURCES.md`): FII/DII = derivatives positioning only,
GIFT Nifty + FRED instead of US futures, fundamentals-lite, `defusedxml` approved; build in the proposal's order
(Kite segments after Monday's history check; FRED waits for the user's `FRED_API_KEY`). Decided 2026-11-21:
**DEC-2015** (intraday analysis starts `INTRADAY_ANALYSIS_START` = 2016-01-01; 2015 1m kept, opt-in, badged) and
**DEC-CORROB** (below).

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
- higher intraday timeframes are derived **on read** from `ohlcv_1m` by `app.intraday_bars()` (DEC-1B), and disk is handled by the Parquet cold tier (Phase 1d).

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

**Kite (Phase 1a)** — `app/broker/kite/`:
- `auth.py` + `app/api/kite.py`: the v1 OAuth flow (checksum = SHA-256(key+request_token+secret),
  expiry at the next 06:00 IST) plus a one-time `state` nonce sent via Kite's `redirect_params`.
  The callback rejects logins this app didn't start.
- `session.py`: the token lives in memory + `app.kite_session` (restart recovery, masked in logs).
  `current()` returns None once expired (fail closed).
- `client.py`: async `httpx` (DEC-K2), token-bucket limits (3/s historical, 10/s other), backoff
  on 429/5xx/network. **Only `TokenException` invalidates the session**; other 403s, such as
  `PermissionException`, surface as `KiteRequestError`. Prices are parsed with
  `json(parse_float=Decimal)` to exact paisa, never floats.
- Tests never hit the network: `create_app(..., http_transport=httpx.MockTransport(...))`
  with `tests/fixtures/kite_http.py`.

**Data foundation** — `app/data/`:
- `instruments.py`: the master is a full snapshot, replaced atomically. The universe is NFO stock-futures
  underlyings ∩ NSE EQ, plus `UNIVERSE_INDEX_SYMBOLS`. Members are deactivated, never deleted.
  `symbol` is `EXCHANGE:TRADINGSYMBOL` everywhere.
- `validate.py`: the gate. Rejects go to `app.ohlcv_quarantine` with a reason and the raw payload.
  A duplicate trading date quarantines *every* copy; large gaps and missing sessions are
  flag-but-keep (`app.ohlcv_flags`).
- `extract_daily.py`: one Kite window per transaction (bars + quarantine + flags + cursor together).
  A 1d bar is final only the NEXT morning (`DAILY_BAR_FINAL_NEXT_DAY_IST`, 08:15 — Kite publishes the
  official close overnight; same-evening bars were provisional, DEC-1C-FINAL). The cursor on the newest window
  advances only to the last bar received. A re-fetched bar that differs from the stored one is
  quarantined as `conflicts_with_stored`, never overwritten. `KiteAuthError` stops the run.
- `jobs.py` + `app/api/data.py`: ingestion runs **in-process** (one job at a time) from
  `POST /api/data/ingest/daily` or `DAILY_INGEST_CRON`. `GET /api/data/coverage` is the
  Phase-1 gate report (`coverage.py`).

**Adjustments (Phase 1c, DEC-B revised — measure, don't model)** — `app/data/nse_archive.py`,
`nse_load.py`, `adjustment.py`, `drift.py`, `repair.py`:
- Kite's candles are ALREADY adjusted (more richly than NSE's action list, not always alike across
  grains). Stored Kite data = the adjusted view, never rewritten. As-traded daily = NSE bhavcopy
  (`app.nse_bhav`). Kite's map is **affine** (it subtracts dividends for part of history):
  Kite = a·NSE + b, **measured** per window by a minimax fit (worst residual ≤ half the window's measured
  Kite price grid, never below ±2.5 paisa, or it is not accepted), stepping only at matching NSE actions (`app.adjustment_days`, `adjustment_events`), with
  separate ratios for the 1m grain and volume; as-traded 1m = `app.minute_as_traded()` = (K÷G − b)÷a.
  Never model prices from `app.corporate_actions` — it only explains steps.
- An unlisted step is `corroborated` (data kept measured, always on the review list) only when NSE's own prices
  prove a clean p/q split/bonus across a one-session boundary (DEC-CORROB, `_corroborate()`); anything less stays
  `suspect`. Never accept an action from any other source.
- NSE names are resolved per day via `app.symbol_changes` (NSE `symbolchange.csv`); official rows prefer
  EQ, then BE/BZ (trade-for-trade). Bumping `PARSE_VERSION` re-scans older days once (missing rows only).
- NSE source = the static archive (`nsearchives.nseindia.com` PR zips, Bc + Pd members) with an honest
  User-Agent. A 403 stops the load; **never impersonate a browser or work around a refusal/CAPTCHA**.
- Each catch-up re-fetches the newest stored session (drift re-check): consistent rescaling →
  `app.adjustment_drift`; the only bar-rewrite path is the audited `repair.py` (`app.data_repairs`).
- Closing auction (CAS): since 2026-08-03 (SEBI 16 Jan 2026, …/I/2765/2026) F&O stocks' continuous trading ends 15:15
  (1m ends 15:14; others and indices 15:29), CAS 15:15–15:30, derivatives to 15:40, rolled out per instrument.
  **From 2026-12-07** (SEBI 15 Oct 2026, …/I/23843/2026): continuous trading to 15:30 for all stocks (1m back to 375),
  CAS 15:30–15:40, derivatives to 15:45 (expiring index contracts 15:30), post-close 15:50–16:00; expiry settlement =
  VWAP of the last 30 min of continuous trading. The per-instrument calendar re-baselines both steps.
- Daily gate gap check is listing-aware: bars before a verified listing (gap ≥ 60 NSE sessions with no
  NSE trading) are flagged `pre_listing` and not counted.

**Intraday (Phase 1b, DEC-1B)** — `app/data/intraday.py`, `extract_intraday.py`, `intraday_coverage.py`:
- `app.ohlcv_1m` is the **only** stored intraday source (int4 paisa, smallint `sid` from
  `app.instrument_ids`; never Kite's bigint token).
- Higher timeframes come **only** from the SQL function `app.intraday_bars()` via
  `derive_bars()`, anchored to each session's real open in `app.exchange_sessions`. Never
  resample in pandas, and never use a midnight or fixed-09:15 origin (muhurat opens at 18:00).
  The golden tests in `tests/integration/test_intraday_derive.py` must reproduce Kite's native
  bars exactly.
- The extractor fetches newest-first in round-robin windows, is resumable, and stops on
  `INTRADAY_DB_BUDGET_GB`.
- Completeness is **per instrument** (DEC-1B-CAL, `app/data/session_calendar.py`): each session vs the
  instrument's own norm, re-baselined on a persistent step-change (the 2026 closing auction 375→360),
  with a market-wide short-day floor; never a benchmark/percentile. Shortfalls are flagged, never quarantined.
  A session with no bars is `not_traded` (not missing) only for an equity NSE's archive says didn't trade that day.
- 1d candles stay Kite-native (the official close is not the last 1m close).

**Stock page (DEC-STOCKPAGE)** — `app/data/stock.py`, `/api/data/stock`, frontend `/stocks`: read-only, one stock's
names, share-series actions vs Kite's measured prices, data health — reusing the gate's own queries so no number differs
from Admin. Corporate actions count only share series EQ/BE/BZ (bonds/preference shares share the symbol). Pages are
full width.

**Charts (DEC-CHART)** — `app/data/candles.py`, `/api/data/candles`, frontend `/charts`: openalgo-charts over
FinTrack's own candles. The backend serves every interval (never re-bucket in the browser); Raw = as traded, for 1m/D/W
equities only. The widget's studies are visual-only, never the engine; no `onOrder`.

**UI (DEC-UI)**: shadcn/ui components live in `frontend/src/components/ui/` (ours to edit; add more with
`npx shadcn@4.23.0 add <name>`, then re-pin any package it bumps and keep `shadcn` itself out of `package.json`). Use theme
tokens (`bg-card`, `text-muted-foreground`, `text-gain`/`text-loss`/`text-warn`), never raw greys; direction always has
▲ ▼ – too. Theme = `.dark` on `<html>` (`src/lib/theme.ts`). App shell: `src/components/app-sidebar.tsx` +
`app-header.tsx`; pages render inside `SidebarInset` (a `<main>`), so pages use `<div>`.

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
