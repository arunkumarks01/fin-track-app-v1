# Changelog

## [Unreleased] — Runnable Phase 6 daily-long studies

- Connected verified NSE/NIFTY inputs and all five materialised signals to a causal fixed-share
  strategy engine. Added entry-day stop/target protection, next-session trails, time exits,
  shared paper slippage/fees/cost basis, partial liquidation and complete-trade metrics.
- Fixed the materialiser discarding previous relative strength, which suppressed crossovers.
- Added migration 0023 for fill reasons and ending open-position state, without editing any
  applied migration. Runs record exact assumptions and a data fingerprint; incomplete inputs
  and unsupported corporate actions fail explicitly. Added study CLI, bearer-protected run
  creation, read-only results APIs and an actual-results `/backtest` view.
- Added causal unit fixtures and a real materialiser-to-HTTP ledger fixture on `_test`.
  Corrected pre-existing type annotations in tick-export tests so strict checks can run.
  A fixture advisory lock now serializes independent destructive `_test` invocations.
- Validation: backend Ruff/format/mypy and 529 offline tests pass; the serial VPS `_test`
  integration suite passes all 180 tests. Frontend lint/format/types, 114 tests, and production
  builds locally and on the VPS staging release pass. A targeted four-test backtest integration
  rerun also passes after recording the exact signal definitions/thresholds.
- Pushed `2aa97a6` and deployed backend/frontend in a new VPS release, keeping prior deployment
  files. Both feature endpoints return 200; PAPER_MODE=true. Migration 0022's checksum is
  unchanged and 0023 is applied; all 23 applied checksums match source. Production studies 1–20 completed: five strategies × four
  symbols, 2025-01-01–2026-11-20, 800 warm-up/469 study sessions, ₹1,00,000 and ten shares.
- Committed `docs/data/phase6_daily_long_20261124.json` with exact assumptions/definitions,
  input/result hashes, metrics, open state and independent API reconciliation. BUILD_PLAN.md
  compares every result. RSI bounce is an exploratory forward-data candidate, not a validated
  strategy; EMA's positive sample has only seven closed trades. No strategy was promoted and
  no forward paper orders were created. Portfolio, independent out-of-sample and full parity
  gates remain open; this is not Phase 6 completion or live-market acceptance.
- Verified desktop/mobile results, run selection, expanded assumptions/fills and the equity
  curve with no browser console errors/warnings. Renamed navigation to “Backtest results”.

## [Unreleased] — Phase 6 historical run ledger and inputs

- Added migration 0022 for immutable backtest runs, fills, marked daily equity and performance
  metrics. A dedicated `_test` database integration check proves a queued run persists its
  inputs and causal replay result atomically.
- Added the first five daily-long signal materialiser and verified-history loader. Stock bars
  come only from NSE-verified bhavcopy dates; NIFTY daily closes align the relative-strength
  signal. These modules do not yet execute historical trades or expose user-visible results.

## [Unreleased] — Phase 6 replay performance foundation

- Added a deterministic performance summary for causal replay output: marked ending equity,
  net return, chronological drawdown, realized wins/losses and profit factor. Profit factor
  is intentionally unavailable until a replay has a realized loss, avoiding an artificial
  infinity on an incomplete sample.
- Unit checks cover the shared paper-cost model and open-position reporting boundary. This is
  offline backtest infrastructure, not a strategy runner, portfolio backtest or Phase 6 gate.

## [Unreleased] — Phase 6 daily-long study specifications

- Added explicit, versioned research baselines for the first five daily-long studies. Each
  declares next-session entry, stop, target, maximum holding sessions and single-position
  rule rather than allowing an undisclosed discretionary exit.

## [Unreleased] — Conservative paper trailing stops

- Realistic equity paper orders can persist an optional trailing-stop distance. A final daily
  bar may tighten the stop only for the following replay session, so unknown high/low order
  within a daily bar cannot produce a favourable exit after the fact.
- Added a dedicated `_test` database replay covering entry, a trail earned on the next
  finalized session, and a subsequent gap-through protective exit. Stop/target, trailing and
  mark persistence now have end-to-end evidence; derivatives, approvals and live-input
  acceptance remain Phase 5 work.

## [Unreleased] — Realistic paper stop exits

- Realistic-mode paper entries with a stop and target now receive an automatic protective
  exit only from the following finalized session. The exit uses the bar's gap-aware trigger
  price, adverse configured slippage and the normal fee stack, then persists its fill,
  P&L and link to the protected entry. Learning-mode exits remain manual.
- Replaced the skipped automatic-exit placeholder with a dedicated test-database replay
  fixture. It proves no same-day look-ahead, the next-session stop exit, persisted linkage,
  slippage and filled ledger record. The remaining Phase 5 work is trailing stops,
  derivatives, approval flows and live-input acceptance.

## [Unreleased] — Persisted paper replay marks

- A completed replay session now saves each held position's dated close mark, MTM and a
  corresponding immutable mark ledger entry. It uses the historical session timestamp, so
  replayed marks are never described as live prices. The paper API integration test verifies
  the persisted mark, MTM and mark history.

## [Unreleased] — Account-aware candidate guardrails

- Candidate plans can now take `paper_account_id`. They load that account's persisted risk
  settings, mode, cash, open-position count, realised P&L and fills for the proposed trade
  day before calculating sizing and guardrails. The normal research view remains account-free
  and explicitly reports unavailable portfolio inputs.
- Intraday scans now wait for the configured finalisation time, with the default scheduled
  run at 16:00 IST instead of attempting to evaluate partial 5-minute bars during the session.
  Account-aware plan and finalisation tests pass; live input acceptance remains separate.

## [Unreleased] — Paper replay safeguards

- Replay now stops without advancing when a held equity lacks a current-session mark, instead
  of silently carrying a stale mark into the next day. A test-database API test verifies that
  positions, cash and the replay cursor remain unchanged.
- Removed an incomplete automatic-exit hook that suppressed exceptions and could submit a
  misleading order using the wrong day's execution path. The workspace now states explicitly
  that automatic stops and targets are unavailable. Implementing and verifying those exits,
  trailing stops, live marks and derivative execution remains open Phase 5 work.
- Full database-marked suite on the dedicated `_test` database: 179 passed, two skipped
  placeholders for automatic-exit integration. Offline backend suite: 513 passed. These
  results do not close the phase exit gates.

## [Unreleased] — Experimental tick retirement transaction

- Added an unscheduled, internal retirement operation. It serializes against other exporters,
  blocks tick writers, rechecks the file SHA-256 and every record against the locked database
  snapshot, marks the range dropped, and removes exactly one chunk in the same transaction.
  The archived-range trigger rejects writers released after commit.
- Dedicated test-database tests cover successful retirement, late changes and concurrent
  writers, corrupt files and rollback on a simulated drop failure. Automatic retention is
  still disabled: full crash/restart recovery, production-sized lock-duration and capture
  throughput need evidence before this operation is connected to scheduled cleanup.

## [Unreleased] — Late tick guard

- Added migration 0021 to reject inserts or timestamp updates into ranges already marked
  dropped in the export ledger. Existing migrations and their checksums are unchanged.
- Dedicated test-database checks cover retained versus dropped ranges and both sides of a
  proposed maintenance lock: a pre-existing writer completes before the lock, and a later
  writer sees the committed dropped marker. This is one component of the protocol, not a
  completed retention solution. Cleanup remains disabled; atomic verified deletion,
  recovery, and the trigger's live-write performance still need verification.

## [Unreleased] — Kite login capture recovery

- A successful Kite login during the configured weekday capture window starts the capture
  service when its schedule is enabled, including when the scheduled start was missed.
  The existing service guard prevents duplicate runs, and a paused schedule stays paused.
  Mock-broker integration tests cover the active, outside-window and paused cases. Live
  market capture, exchange holiday behavior and a full-session probe remain unverified.

## [Unreleased] — Tick export integrity work

- File promotion now flushes the completed Parquet file and containing directory before the
  verification ledger is written. A per-chunk database advisory lock prevents two exporter
  processes from promoting conflicting snapshots. Export temporary filenames are unique.
  A previously dropped range that reappears is flagged instead of being ignored or
  overwriting its archive.
  The exporter still retains all chunks: protection against late tick writers and complete
  crash recovery remain required before cleanup can be enabled.
- Export verification now includes a duplicate-sensitive, order-independent digest of every
  tick field, including nulls, and always exports a fresh snapshot on retry. Even direct
  exporter calls retain database chunks; safe cleanup still needs a protocol for concurrent
  writers and durable promotion. Verification is of a snapshot, not of later writes.
- Tested every field, ordering and duplicates, and exercised export and failure paths on the
  dedicated test database. This does not close the Phase 1 or Phase 5 exit gates.

## [Unreleased] — Labelled participant research data

- Exposed 19 exact NSE participant files that reconcile within the configured tolerance
  and five files whose participant components reconcile but printed totals differ. They
  remain separate from clean analytics, with source values, independently derived totals,
  warnings, source checksums and explicit quality labels in a research-only workspace.
- Two participant dates remain gaps: the NSE file for 13 June 2018 has no title/date, and
  the file requested for 8 June 2021 states only “Jun 08”, without a year. A filename and
  the existence of a market session do not independently verify the contents' date. The
  missing participant archive remains an explicit gap.

## [Unreleased] — NSE participant source consistency review

- Added reproducible checks for each exact participant source, using the existing configured
  tolerance and retaining the original values and differences. The corrected v2 check
  distinguishes participant-component totals from the published grand total; the first
  v1 run is retained as audit history and superseded by v2.
- On the VPS, v2 classified 19 files as reconciled within tolerance, five with materially
  inconsistent published long/short totals, and two with unverified source dates. These
  are source checks, not approval to use the files as verified clean analytics data.

## [Unreleased] — Exact NSE participant review imports

- Added separate review-only storage for exact NUMERIC participant values, including NSE's
  own total row, source checksum, date status and published total differences. Import is
  idempotent and never changes the original failure ledger or clean participant table.
- Imported 26 archived sources (130 rows) to review storage on the VPS. All remain marked
  review-required; two source dates remain unverified. Missing source files remain gaps.

## [Unreleased] — NSE participant review report

- Added a read-only review report for archived participant files. It represents source
  values as exact decimal strings, reports differences in published totals and keeps
  unverified dates explicit. Reviewed files remain quarantined and are not used as clean
  participant positions. The Admin page links to the report.

## [Unreleased] — Backtest cadence

- Recorded the user's direction to validate each strategy on historical data and then use
  forward paper testing; historical backtests are not required every day.
- Added a checksummed, version-preserving archive of NSE participant CSV files that fail
  strict validation. The source and the failure ledger are saved atomically; retained
  files remain excluded from clean participant data until their issues are resolved.

## [Unreleased] — NSE participant CSV exact counts

- Accept quoted Indian- and western-grouped contract counts with an all-zero decimal
  suffix, without rounding fractional counts. Validation of dates and source totals is
  unchanged. Recovered the 12 December 2019 open-interest file after test-database
  verification; the other 26 failed files remain flagged for source-data review.

## [Unreleased] — Phase 6 replay foundation

- Added an offline single-symbol daily replay building block. Close-of-session instructions
  execute no earlier than the next supplied session using paper's fill, fee and cost-basis
  functions. Added causal, cost, drawdown, partial-fill and test-database paper-parity
  checks. This is not yet a complete strategy backtester or Phase 6 gate.
- Added a small Learn page explaining backtests, drawdown, win rate, slippage and
  overfitting, with a link to the clearly labelled fictional backtest example.
- Added an explicit warning to the paper workspace that automatic stop and target exits
  are not verified and must not be relied on to close positions.

## [Unreleased] — Backtest learning preview

- Added an explicitly fictional four-trade example that explains return, win rate and
  drawdown. Values are computed from the same example trades; no backtest engine or real
  results are implied. The preview can be developed alongside Phase 6.
- Deployed the current backend and frontend as separate single-process VPS systemd
  services. The F&O import was stopped cleanly on the local backend and resumed from its
  committed ledger on the VPS. The database and services remain private to the VPS;
  access to the frontend is through the existing email-allowlisted Google gate.

## [Unreleased] — VPS UI preview

- Built the current frontend on the VPS and exposed it at fintrack.arunkumarks.com behind
  the existing Google sign-in gate, restricted to its configured email allowlist. The
  initial preview used a temporary SSH tunnel; the later VPS deployment replaces it.

## [Unreleased] — Phase 1–5 work in progress

- Connected tick capture, option-chain and scanner jobs to application services, with
  schedules for configured Kite and enabled scanners. Corrected default weekday crons.
- Tick status reports capture as unavailable when Kite credentials are not configured.
- Capture now reports buffered, dropped or interrupted rows as a failed run. Restored the
  native CI workflow and added scheduling and test-database integration checks.
- Automatic tick cleanup remains suspended: full-field export verification and concurrent
  writer protection remain unfinished. See `PHASE_1_5_HANDOFF.md` for remaining work and
  separate live-market acceptance. No phase exit gate is claimed by these changes.

## [Unreleased] — Recovered feature startup integration

- Registered the recovered analytics, scoring and paper routers in the main app and
  initialized the shared news-research service used by candidate routes.
- Preserved configurable database timeouts and corrected exit-trigger prices/quantities.
  Scheduled age-only tick deletion remains suspended pending export-path verification.
- The full feature push is imported. Earlier reports of absent feature modules no longer
  describe this checkout; the recovered implementation still requires integration review.

## [Unreleased] — Phase 5.1: Automatic exits (2026-11-22)

- **Task 1 (auto exits) complete.** Built `app.sandbox.exits`: detect when a filled order's SL or target is hit in a bar, generate exit order. Exit detector checks SL first (stop_hit reason), then target (target_hit reason). Exit instruction reverses side + quantity, always MARKET. P&L calculated as (exit−entry)×qty−fees. Detector supports trailing SL override (Phase 5.2 builds on this).
- **Migration 0017** adds Phase 5 schema: columns for exit tracking + trailing stops + live marks + derivatives + Telegram approvals. Applied as one transaction; all tasks build on it.
- **13 unit tests** (`test_paper_exits.py`): SL/target detection, exit order generation, P&L calculation, edge cases (no fill, no levels, trailing override). **15 integration tests** (`test_paper_exit_flow.py`) structures end-to-end flows.
- **Next:** trailing stops (Task 2), live marks (Task 3, parallel), derivatives (Task 4), Telegram (Task 5).

## [Unreleased] — Stock opportunity scan

- Swing and intraday stock scans run as manually controllable backend jobs, independently
  of optional schedules. Completed results are saved in TimescaleDB and the Candidates
  page shows the latest 30 reports per mode, including unavailable symbols. Selecting a
  result evaluates the stock again with current data. Stopped or failed runs do not
  replace a completed report.

## [Unreleased] — Entry-window validation

- Realistic-mode trade plans now flag an unconfirmed next trading session and block the
  plan until the entry window can be verified.

## [Unreleased] — Phase 3 news matching

- Match complete ticker names in news headlines so short symbols do not select unrelated
  words. News remains unknown until a provider returns a validated classification.

## [Unreleased] — NSE participant backfill follow-up

- Recorded the 27 remaining participant-file failures as a priority for 22 November.
  Two 17 September 2019 files were recovered after handling a duplicated title year.

## [Unreleased] — Manual paper replay

- Added a Paper trading workspace backed by independent replay accounts and a transactional
  NSE equity ledger for orders, fills, positions and P&L. Replay advances only on explicit
  user action and uses finalized as-traded daily bars.
- Market, limit, stop-market and GTT replay orders use session volume caps, configurable
  simulated fees and explicit handling of partial fills. Pause, kill switch and order
  idempotency persist across restarts. Realistic mode applies the available portfolio limits.
- Historical corporate actions stop affected replays. Live marks, derivatives, automatic
  exits, trailing stops and Telegram approval are separate remaining Phase 5 work.

## [Unreleased] — Phase 4 paper trade planning

- Qualified candidates can show a tick-aligned entry trigger and price limit, a session
  cutoff, a structure/ATR stop and target, and a trailing-stop rule. Position sizing and
  reward-to-risk use the worst permitted entry and exclude execution costs.
- Added fixed-risk sizing, fixed lots and a gated Kelly calculation. Paper previews use
  configured capital. Realistic checks require portfolio state, while Learning surfaces
  warnings without blocking. The portfolio ledger, margin checks and trailing after a fill
  need the Phase 5 integration.

## [Unreleased] — Phase 3 suggestion explanations

- Candidates now show a compact results list and an explanation-first detail panel. The
  supporting chart marks the evaluated candle and relevant price levels or EMAs.

- Added research candidates and rule-by-rule explanations for swing and 5-minute
  intraday strategies, plus separate futures and option contract checks. The UI shows
  actual values, thresholds, data age, invalidation and calibration status.
- Added revisioned custom strategy definitions, a calibration read path, opt-in
  scheduled scans and a bearer-protected, fail-closed Claude CLI news review.
- Live inputs, the first held-out calibration runs, and production scanner acceptance
  still need verification. Guided lessons and the Explainer agent remain in Phase 8.

## [Unreleased] — Admin job controls and NSE reference repair

- Added database timeout safeguards to the Phase 1 roadmap after an abandoned announcement
  transaction blocked the reference backfill. Implementation and verification are pending.
- Admin can start supported ingestion, backfill and capture jobs, stop a selected run, and
  pause or resume its schedule for the current backend session. Actions require the existing
  API bearer token. Completed ingestion windows remain in the database when a run stops.
- Admin now shows recent reference-file failures and reports interrupted database connections
  while a backfill retries.
- The participant parser accepts the verified NSE title variants, worksheet calculations
  after the official rows and a checked long-plus-short checksum. It still requires a full
  trade date and validates the four participant rows and exchange totals.
- Accept a trailing period after a complete NSE participant-file date, as found in 2020
  archive titles. The parser still checks the reported date against the requested session.
- Accept mixed CR and CRLF archive CSVs and record any remaining CSV errors per file so
  unrelated dates can continue loading.

## [Unreleased] — Charts indicators and Options analytics UI

- Charts opens with adjusted prices even if a saved layout last used Raw. Raw remains
  selectable and explains the price jumps from splits and bonus issues.
- Charts follows the selected symbol and interval for server indicators. It shows EMA20,
  EMA50 and EMA200, recent EMA history, and the other Phase 2 indicator groups. EMA20 is
  included by default and compared with TA-Lib in the golden tests.
- Options shows saved chain coverage, open interest by strike, expiry metrics, IV and Greeks.
  Pricing inputs are optional overrides. Missing, partial and stale reports are labelled in the UI.

## [Unreleased] — Phase 2 research analytics

- Added `GET /api/analytics/indicators` for stored daily and intraday candles. It returns
  SMA, EMA, RSI, ATR, ADX and directional indicators, MACD, Bollinger and Keltner bands,
  stochastic, Supertrend, ROC, OBV, relative volume and strength, prior Donchian and
  52-week levels, central pivot range, opening gap and range, and session VWAP estimated
  from bar typical prices and volume.
- Preserved the existing 2016 intraday analysis floor. Indicator state resets across known
  missing sessions, intraday calculations skip unverified sessions, and EMA200 requires
  200 usable bars. Responses report the source, warm-up and benchmark freshness.
- Added `GET /api/analytics/options` for the captured strikes of each expiry. It reports
  OI and volume PCR, max-pain ties, prior-snapshot OI and premium changes, implied volatility,
  Greeks, gross gamma walls and constant-tenor ATM IV rank. Pricing uses European
  Black-Scholes-Merton and positive, uncrossed bid/ask midpoints. Rate and dividend assumptions
  are explicit. IV rank never extrapolates outside the captured maturities and needs enough
  comparable days. Raw snapshots remain the source of record.
- Added deterministic regression snapshots, warm-up convergence checks, TA-Lib and QuantLib
  golden comparisons, and integration tests for both endpoints.

## [Unreleased] — Resume reference downloads during daily ingestion

- The daily job's reference-data stage uses the same database reconnect and ledger-based
  resume path as a manually started reference download.

## [Unreleased] — Slice 1d: live ticks, Parquet export, option-chain snapshots (2026-11-21)

Built to the locked DEC-1D-* choices. Tested without a live market (no market today); the real check is Monday 23 Nov with a Kite login.

- **Tick parser** (`app/data/kite_ticks.py`): Kite's binary format, strict. Full-mode tradables (184 bytes, 5-level depth), index
  packets (28/32), quote (44) and LTP (8). Anything malformed (wrong length, overrun, trailing bytes) is refused and counted.
  Layout checked against the 2026-11-20 probe; real packets are verified Monday.
- **Compact table `app.ticks`** (migration 0014, replaces the empty Phase-0 placeholder): smallint instrument id, int4 paisa, 5-level
  depth as fixed columns (not arrays), daily chunks. A value that doesn't fit its column is refused, never clipped.
- **Capture** (`app/data/ticks.py`): one in-process job a day (`TICKS_CAPTURE_CRON`, 08:58 IST) over the universe + INDIA VIX +
  GIFT NIFTY, window 09:00-16:00 (`TICKS_CAPTURE_START/END`, covers the 15:45 close and post-close from 2026-12-07).
  - No Kite session: nothing connects, the run says so.
  - A dropped or silent connection reconnects with backoff; a database drop keeps rows in memory and retries; past the buffer cap the
    oldest rows are dropped and counted.
  - The token appears only in the connect URL, never logged. One record per session in `app.tick_capture_runs`.
- **Parquet export, fail closed** (`app/data/tick_export.py`): a chunk older than `TICKS_HOT_DAYS` (7) is read in one snapshot,
  written to zstd Parquet, read back, compared (row count + 5 checksums), and only then dropped. Mismatch, error or an unwritable folder
  keeps the chunk and flags it in `app.tick_exports`.
  - The old unconditional 90-day drop job is **removed**; `TICKS_RETENTION_DAYS` is gone (now `TICKS_HOT_DAYS`).
  - File work runs in threads so the API stays responsive.
- **Option chains** (`app/data/option_chain.py`, tables `app.option_snapshots` + `_meta`): every 5 minutes 09:15-15:30, NIFTY, BANKNIFTY,
  FINNIFTY, MIDCPNIFTY + the top 20 stocks by 20-session option turnover (from the 1f history; `CHAIN_STOCK_OVERRIDE` wins), 2 expiries,
  ATM +-15 strikes. Raw values only: price, volume, OI, best bid/ask, last trade time, plus spot and near-month future. Missing quotes make
  a 'partial' snapshot; no spot makes an 'error' row; nothing is guessed. `KiteClient.quote()` is rate-limited to 1 request/s.
- **Admin:** a "Live ticks and option chains" panel (`GET /api/data/ticks`); `GET /api/data/chain?underlying=` returns the newest raw chain.
- **Dependencies (approved, pinned):** `websockets` 17.4, `pyarrow` 26.0.1 (they had been dropped from `pyproject.toml` by the 1e commit).
- **Tests:** 3 parser, 5 capture (real local WebSocket server: reconnect, bad packets, unknown token, no session, silence, database
  unreachable), 3 export (verified-then-dropped, altered file keeps the chunk, unwritable folder), 5 chain-selection unit tests,
  5 chain DB tests (fake Kite /quote), 1 endpoints test, 3 frontend.
- **Pre-open:** capture starts at 09:00, so pre-open ticks are recorded from the first run (DEC-1D-OWNERS).

## [Unreleased] — Downloads survive database drops; economic-calendar draft (2026-11-21)

- **Why the 1e download kept stopping:** the SSH tunnel to the database drops every 20-30 minutes ("server not responding"), the job
  lost its connection and failed. The 1e reference job, the 1f F&O job and the NSE archive job now wait and run again
  (`INTRADAY_DB_RETRIES`, `INTRADAY_DB_RETRY_WAIT_SECONDS`, same as the 1-minute job). Every loader skips what its ledger already
  holds, so a re-run continues where it stopped. Tests: lost twice then works; gives up after the limit; other errors aren't retried.
- **Economic calendar draft:** `docs/data/economic_calendar_draft.csv`, the 16 FOMC meetings of 2026-27 read from federalreserve.gov
  (decision day; times left blank, the page gives none). Parsed with the real importer. **Not imported yet**: it waits for the user's
  review. RBI policy dates and MoSPI release dates are not in it: their sites could not be read from here, and nothing is guessed.

## [Unreleased] — Slice 1f: futures and options history (2026-11-21)

Sources approved in advance by the user (DEC-1D-OWNERS): NSE's F&O bhavcopy archive, same honest-client rules as 1c/1e.

- **Two file formats**, one normalised row:
  - old (2015 to mid-2024): `content/historical/DERIVATIVES/<YYYY>/<MON>/fo<DD><MON><YYYY>bhav.csv.zip`;
  - UDiFF (from 2024-01-02, verified): `content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip`.
  - `FO_UDIFF_FROM` picks the format by date; both exist through mid-2024.
- **Table `app.fo_daily`** (migration 0013): one row per contract per day, prices as exact integer paisa, contracts,
  open interest and its change; the newer format also gives the previous close, the spot price and the lot size.
  - Only contracts that traded or hold open interest are kept (about 30,000 of 45,000 a day now; about 5,000 of 29,000 in 2015).
  - Estimated 36 million rows, about 4 GB.
- **Rules:** newest first, one transaction per day, resumable, a 404 or a mislabelled file is a gap, a 403 stops.
  - New: the load stops when the database reaches `FO_DB_BUDGET_GB` (35).
  - A row that doesn't parse or has high below low is rejected.
  - NSE's own quirk is kept and counted: in thin contracts the close can sit outside the day's high–low (seen 2015-01-05: MOTHERSUMI, SUNTV).
- Job `fo_history_ingest`, `POST /api/data/ingest/fo-history`, `GET /api/data/fo`; Admin shows a "Futures & options history" panel.
- Tests: 5 parser tests on verbatim lines of real files, 4 DB tests (both formats, a gap, resume, a mislabelled file, a 403, the size
  budget, the endpoint), 3 frontend tests.
- **Not live yet:** the backend running now was started by another tool and is mid-download of the 1e files; it needs a restart (and
  `POST /api/data/ingest/fo-history`) after that download finishes.

## [Unreleased] — Isolate Next.js dev output

- The Next.js dev server uses `.next-dev`. Production build and start use `.next`, so a build
  can run alongside local development without replacing the dev server's chunks.

## [Unreleased] — NSE market cap and historical participant files (2026-11-21)

- Migration 0012 stores NSE's published equity market cap in integer paisa and the issue size
  in shares. The loader scans from the exchange's first publication date, February 1, 2024.
  Market cap and PR text share the same archive download and have independent coverage rows.
  Stock shows the latest figure with its date and series. The API carries bigint values as
  decimal strings so the browser can display them without losing precision.
- Historical participant titles can put the year immediately after the comma. Verified the
  format in the May 2, 2025 NSE reports and added a regression test. Previously errored files
  are retried by the existing ledger rule.
- Added GIFT Nifty and India VIX daily and native minute history using the existing Kite extractors, outside
  the NSE stock universe. Its minute catch-up waits for overnight finality, with coverage visible
  in Admin. MCX commodities are paused at the user's request.
- Updated the Admin integration test to include the scheduled RSS job.

## [Unreleased] — Slice 1e deals, PR text, news, calendar and FRED (2026-11-21)

- Migration 0011 adds deal, announcement, board-meeting, news, calendar and macro storage.
  The NSE ledger records missed deal sessions after capture begins. Board meetings retain the
  earliest date they appeared in the archive, regardless of download order.
- News ingestion reads publisher RSS and Atom with pinned `defusedxml`, observes feed TTLs and
  stops retrying on a refusal. News and FRED each have an independent ingestion lock. The
  daily follow-up uses the same lock as a manual FRED run.
- FRED stores exactly representable scaled integers and reads the two latest observations with
  their source status. It requires `FRED_API_KEY`. Calendar import rejects malformed CSV and
  invalid encoding before any writes and counts the rows inserted by that request.
- Stock shows stored announcements, meetings and results dates, and bulk/block deals with
  rounded prices labelled. Admin shows the expanded NSE ledger and coverage counts for the
  calendar, RSS, and FRED.
- The full adjustment fit avoids repeated scans and runs with bounded per-symbol concurrency.
  The handoff benchmark reported identical output on 218 stocks and a drop from 269 s to 45 s.
  The fit still recomputes the full history.
- Added integration coverage for feed refusal, TTL and deduplication, incremental FRED fetching,
  the macro read contract, and authenticated, atomic, idempotent calendar import.

## [Unreleased] — Slice 1e step 1: delivery % and FII/DII F&O positions (2026-11-21)

Built per DEC-1E-SOURCES step 1. Both from NSE's public archive, 2015 onwards, using the same honest client and
refusal rules as the 1c archive load.

### Data
- **Delivery %** (`MTO_ddmmyyyy.DAT`) → `app.delivery_daily`:
  - universe shares only (series EQ, then BE, then BZ); a company's bonds in the same file are skipped;
  - old names are mapped per day (e.g. SRTRANSFIN → SHRIRAMFIN);
  - % is computed exactly from the two quantities (integer basis points) and checked against NSE's printed %;
    a row that disagrees, or has more delivered than traded, is rejected.
- **F&O positions by participant** (`fao_participant_oi/vol_ddmmyyyy.csv`) → `app.participant_positions`:
  - Client, DII, FII and Pro, 14 counts each, open interest and volume;
  - every row's long/short totals must equal its legs, and the four rows must equal NSE's TOTAL row.
- **NSE's own rounding:** NSE's open-interest files are often off by 1–2 contracts between the rows and the TOTAL.
  - Measured on 40 random days: 16 OI files off by ≤2; volume files exact. In recent months it's ~90% of days.
  - Those days are stored as given, and the gap is written to the ledger and counted on Admin.
  - A gap above `PARTICIPANT_TOTAL_TOLERANCE_CONTRACTS` (default 2) refuses the day.
- **Ledger and rules:** one ledger row per dataset and day (`app.reference_files`, migration 0010).
  - Newest first, one transaction per file.
  - 404 → gap, a mislabelled file → gap, unreadable → error (retried), 403 → the whole load stops.
  - Resumable; runs after each daily job (`REFERENCE_AFTER_DAILY`) and on demand at
    `POST /api/data/ingest/reference`.
- `NseArchiveClient.fetch_file()` is the one fetch path for all archive files (PR zips use it too).

### Screens
- **Stock page:** a Delivery % card with the volume-weighted average over the last 20 days, the latest day's
  quantities, and a bar chart.
- **Dashboard:** "F&O positions by participant", with net index futures (and change vs the day before), the long
  share, and net stock futures and index options, in contracts, labelled not live.
- **Admin:** a "Reference data from NSE" table: days loaded, range, gaps, errors, rejected rows, rounding days.

### Fixed
- The Data foundation panel read any running job's progress as the 1-minute download's, and errored while any other
  job ran (archive load, re-measure, and now 1e). It now reads that progress only for the 1-minute job; a test
  pins it.

### Tests
- 9 parser tests on verbatim real files (2015 and 2026, including NSE's off-by-one TOTAL).
- 4 DB tests: three datasets, resume, gap, mislabelled, bad total, refusal, missing name map, endpoints.
- 7 new frontend tests.

## [Unreleased] — Sidebar: same text size as the page, visible collapse that persists (2026-11-21)

- Sidebar text now matches the main screen: 16px menu items, 14px section labels and footer, 18px icons
  (was 14/12/16).
- A visible **Collapse menu / Expand menu** button at the bottom of the sidebar (also: the top-bar button,
  Ctrl/⌘+B, the sidebar edge). The state is saved in the `sidebar_state` cookie and read by the root layout, so a
  collapsed menu stays collapsed after a reload. Pages are now server-rendered on request (they read that cookie).
- Next.js's dev-only badge moved to bottom-right so it no longer covers the collapse button.

## [Unreleased] — New look: shadcn/ui app shell, light + dark, Dashboard (DEC-UI) (2026-11-21)

- **UI kit:** shadcn/ui components copied into `frontend/src/components/ui/` (sidebar, card, badge, button, chart,
  table, tooltip, skeleton, sheet, dropdown, input, separator). New pinned packages are listed in DEC-UI. The
  shadcn CLI is not an app dependency; its base CSS is vendored as `src/app/shadcn-base.css`. `npm audit` shows only
  the two existing PostCSS advisories inside Next 15.
- **App shell:**
  - the sidebar folds to icons, becomes a slide-out menu on phones, and stays dark in both themes;
  - the top bar has stock search from any page, a Kite status badge (links to Admin), a PAPER badge and the
    theme switch.
- **Light and dark:** a full light palette (soft grey page, white cards, indigo) and a dark one (deep navy).
  - Gain, loss and warning colours are tokens.
  - The choice is saved per device and applied before the page paints (no flash).
  - Charts switch with it (`widget.setTheme`).
  - Every page's hard-coded greys were moved to theme colours.
- **Dashboard (`/`):**
  - one card per index (NIFTY 50 first): last stored close, change ▲▼ in points and %, a 30-session trend line;
  - clicking a card shows that index's last 250 sessions in a large chart, with the change over the period;
  - System and Data foundation panels below;
  - everything is labelled "last stored daily close · not live".
- **Stocks:** search moved to the top bar (one search box); the page follows `?symbol=` so top-bar searches work
  from any page.
- **Tests:** `theme.test.ts` (saved choice, system fallback, broken storage), `market.test.ts` (change in paisa and
  basis points, symmetric rounding, fewer than two closes → nothing, strict page). 89 frontend tests; build OK.

## [Unreleased] — Stock page (DEC-STOCKPAGE), full-width pages, bond actions excluded (2026-11-21)

### Stock page — `/stocks`
- Search any of the 224 universe members (keyboard: ↑ ↓ Enter Esc); the address becomes `/stocks?symbol=NSE:X`,
  so a stock can be bookmarked. "Open chart" goes to `/charts?symbol=NSE:X`.
- Shows, in plain words:
  - basics: name, tick size, F&O lot size (nearest live futures contract), earlier names;
  - daily and 1-minute data health, exactly the gate/Admin numbers (`symbol_coverage()`, `completeness(symbol)`);
  - corporate actions, each with what Kite's measured prices did, plus filters (splits & bonuses, dividends, other);
  - price steps NSE didn't list (tiny ones hidden behind a toggle);
  - days with a usable real traded price;
  - the 1e datasets as "not loaded yet".
- Backend: `GET /api/data/stock?symbol=` (`app/data/stock.py`), 404 for a stock outside the universe. 7 DB tests
  (statuses, rename chain with a loop guard, nearest live future, health numbers, index, 404).
- Frontend: strict parser `src/lib/stock.ts` (unknown status, non-integer paisa or a missing field is an error),
  `formatRupees` in the one formatter, shared plain-English labels in `src/lib/labels.ts` (Admin uses them too).
  6 parser/wording tests.
- Found on the page and fixed before release: dividends showed as "Other" (the SQL built a nested array).

### Full-width pages
- Home, Admin, Charts and Stocks drop the fixed max-width; content uses the whole screen with side padding.

### Bonds and preference shares no longer count as share actions (`load_actions`)
- NSE lists a company's NCDs, bonds and preference shares under the same symbol in other series (MB, YB, N5, O1, P1…).
  The analysis read them as share actions: 750 such entries across 21 companies (e.g. SHRIRAMFIN: 327 interest
  payments). They forced extra window splits and could label a real step "can't check".
- Now only the share series NSE prices come from (EQ, BE, BZ — `PD_SERIES`) count, in the analysis and on the page.
  A DB test pins it.

## [Unreleased] — Decisions taken: DEC-2015, DEC-CORROB, DEC-1E-SOURCES (2026-11-21)

The user took every recommendation in `OPEN_DATA_DECISIONS.md` and `PROPOSAL_1E_SOURCES.md`.

### DEC-CORROB — the `corroborated` class (built)
- `app/data/adjustment.py`: an unlisted step (no NSE action, price step, no 1m-grain step) that would be
  `suspect` becomes `corroborated` only if NSE's own prices prove it (`_corroborate()`):
  1. the older line equals the newer one × a clean p/q (p, q ≤ `ADJ_CORROBORATE_MAX_TERM` = 10) within `ADJ_MATCH`;
  2. official close ÷ (previous official close × p/q) − 1 is inside ±20% (the widest equity band, an exchange
     constant) while the raw return is outside it;
  3. one-session boundary: no day between the windows, and the step day's official prev_close equals the older
     window's last official close.
- Plain English on Admin: the class shows as "confirmed by NSE prices", and the detail starts "not in NSE's action list, but NSE prices confirm a x1/4 step…". The internal name stays `corroborated`.
- Both windows stay `measured`; the evidence is in the event detail; every case is on the Admin review list
  (after suspects and official mismatches). Admin labels the class.
- Migration **0009_corroborated** widens the classification CHECK (the list stays closed — a test inserts an
  unknown class and expects the CHECK to refuse it).
- Tests: corroborated at ×1/4, ×1/5, ×1/2; stays `suspect` when NSE's prices are continuous, at an unclean ratio,
  at a clean ratio beyond the max term (1/11), across an unmeasured day, or when a listed action disagrees. Each
  rejection test was checked to fail with its condition switched off.
- `ADJ_CORROBORATE_MAX_TERM` (default 10, 2–50) in config and `.env.example`.
- **Live re-measure (224 symbols, 2026-11-21)** against a before-snapshot. Exactly **2** of 1,165 events changed
  class:
  - 360ONE 2023-03-02 (×1/4): implied return −0.51%, raw −75.13%.
  - SHRIRAMFIN 2025-01-10 (×1/5): implied return −5.33%, raw −81.07%.
  - Both went suspect → corroborated, and **3,349** days went suspect → measured; nothing else moved.
  - NMDC's two steps stay `suspect`; only 26 suspect days remain.
  - 360ONE's raw 1m now loads on both sides of the bonus with 0 bars omitted.
  - Review list: 70 rows (2 suspect, 2 corroborated, 66 material). One fewer material row because the run
    stored exact sizes in place of the 0008 back-fill parsed from rounded text: NMDC 2021-10-11 is 0.2462%, not
    0.25%, so it now counts as noise (777).

### DEC-2015 — intraday analysis starts 2016-01-01 (built as config)
- `INTRADAY_ANALYSIS_START` (default 2016-01-01), for Phase 2+ intraday backtests/statistics. No data change.
- `/api/data/coverage/intraday` reports it as `analysis_start`; the Admin 1-minute panel shows "Analysis starts"
  (strict parse: a missing or non-ISO value is a contract error).

### Planned, not built — incremental re-measure
- Today every analysis pass re-fits all 224 symbols from 2015 (~30 min). Plan:
  - **skip unchanged symbols:** store a per-symbol fingerprint of everything the fit reads (1d bars, NSE rows,
    actions, 1m aggregates, name map) plus a rule version (code + `ADJ_*` settings); re-fit only on a mismatch,
    and a missing fingerprint means re-fit (fail closed);
  - **re-fit only the tail in time:** keep the settled windows and re-fit from the start of the last determined
    window, since a daily run gives every symbol one new day.
- Accepted only with a golden test (incremental result == full result) and a weekly full pass as a cross-check.
- Rule changes (like DEC-CORROB) still need one full pass.
- Build it before 1d goes live: the 08:30 daily job plus a full pass can run past 09:00, when pre-open tick
  capture starts in the same process.

### DEC-1E-SOURCES — 1e approved
- FII/DII = derivatives positioning only; GIFT Nifty + FRED prior close instead of US futures; fundamentals-lite
  now, statement-level FA later with a licensed vendor; `defusedxml` approved. `FRED_API_KEY` to be created by
  the user. Recorded in DECISIONS, CLAUDE.md, and the two docs (now marked decided/approved).

## [Unreleased] — Waiting-for-Monday work: CAS circulars, open decisions, 1e sources, Admin fixes (2026-11-21)

### Closing auction: both SEBI circulars read
- **16 Jan 2026** (…/I/2765/2026), in force since 2026-08-03 per instrument: F&O stocks' continuous trading ends
  15:15 (1m ends 15:14), CAS 15:15–15:30, derivatives to 15:40.
- **15 Oct 2026** (HO/47/11/11(3)2025-MRD-POD2/I/23843/2026), **from 2026-12-07**:
  - continuous trading to 15:30 for all stocks: equity 1m goes back to 375 minutes
  - CAS 15:30–15:40, with a random close 15:38–15:40
  - derivatives to 15:45 (expiring index contracts stop at 15:30)
  - post-close 15:50–16:00
  - expiry settlement = VWAP of the last 30 minutes of continuous trading
  - iceberg-order changes from 2027-02-08
  - Read from SEBI's PDF and its Sep 2026 consultation paper (honest client). SEBI's search endpoint answered
    530 and was not retried. The January text itself is still unread; the consultation paper's comparison table
    gives its timings.
- **For 1d:** Monday's probe runs under the current regime. From 7 Dec, tick capture must cover derivatives to
  15:45, and the CAS price/quantity is disseminated after 15:40.
- **Calendar:** the per-instrument calendar re-baselines a step **up** as well as down. A unit test pins 360 → 375.

### Open decisions written up — `docs/OPEN_DATA_DECISIONS.md`
- **2015 policy:**
  - 1m 94.16% complete
  - 4,550 market-wide short days (vs 160–336 in later years)
  - 156/224 instruments
  - Recommended: store, but default-exclude from intraday analysis (start 2016-01-01).
- **"Corroborated" actions:**
  - 360ONE (×0.25) and SHRIRAMFIN (×0.20) are visible in NSE's own prices; implied day returns −0.5% / −5.3%.
  - Today's rule costs 3,349 as-traded-unusable days.
  - Recommended: an automatic, listed `corroborated` class with three conditions. NMDC stays suspect.

### 1e sources proposed — `docs/PROPOSAL_1E_SOURCES.md` (awaiting approval)
- **Verified:**
  - NSE archive delivery % (MTO, 2015→)
  - participant-wise OI/volume (2015→)
  - bulk/block deals (current day only)
  - announcements and board meetings inside the PR zips already fetched (2015→)
  - Kite India VIX, **GIFT NIFTY (NSEIX)**, MCX and CDS instruments
  - FRED API (needs a free key)
  - publisher RSS (Business Standard, Mint, ET) and RBI RSS
- **Refused or unreachable** (stopped, not worked around):
  - BSE API: 403
  - NSDL FPI and FRED's CSV host: no response from here or the VPS
  - NSE results pages: browser-only
- **Four questions for the user:**
  - FII/DII cash flows
  - US futures
  - statement-level fundamentals
  - the `defusedxml` dependency

### Admin fixes
- **Coverage report 6.7–7.1 s → 2.8 s.**
  - Quarantine and flag counts are aggregated once instead of one correlated subquery per symbol.
  - JIT is off for the report's queries: Postgres spent ~1.8 s compiling ~600 functions for a sub-second query.
  - Results are identical on live data: 224 rows, 40 unresolved and 4,860 total quarantined, 8,417 flags.
- **Review list: material steps only.**
  - Migration **0008** adds `adjustment_events.magnitude`. New runs write it; existing rows are back-filled once
    from our own detail text.
  - Unexplained steps of at least `ADJ_REVIEW_MIN_STEP` (default 0.25%) are listed, largest first. Smaller ones are
    counted as refit noise: median 0.03%, 776 of 843.
  - A step of unknown size is always listed.
  - Live: 100 rows (96 noise) → **71** (4 suspect + 67 material).
  - The threshold is bound as an exact decimal. A float bound landed just above 0.0025 and moved three steps sized
    exactly at the threshold into the noise count. Caught on live data; regression test added.
- **1m view: suspensions are not data gaps.**
  - An equity session with no bars counts as `not_traded` only when NSE's archive loaded that day and has no row
    for the symbol. Indices, days NSE traded, and days the archive can't vouch for stay `missing`.
  - Live: FORCEMOT's 76 missing sessions → 0 missing, 76 not traded. 213 real missing sessions elsewhere remain
    flagged.
  - The endpoint went 0.6 s → 1.5 s, with JIT ruled out.

### Noted
- NSE lists each corporate action once per series (EQ, BE, BL, IQ, T0, TL): 13,808 rows, 4,985 distinct. By design;
  the matcher dedupes by action.

## [Unreleased] — Charts page: OpenAlgo Charts over FinTrack candles (2026-11-20)

**DEC-CHART** revises D21: openalgo-charts 2.6.9 (exact pin, Apache-2.0) replaces lightweight-charts for
candle charts. Built before 1d at the user's request; read-only, nothing scores from it.

### Added
- `app/data/candles.py` + `GET /api/data/symbols` and `GET /api/data/candles?symbol&interval&adjustment&before&count`
  (integer paisa, epoch-second times, `has_more`/`next_before` paging, `omitted_unusable`, a `source` string).
  Intervals 1m 3m 5m 15m 30m 1h D W — 3m–1h from `derive_bars()` (session-anchored), W built from D (Monday 00:00 IST).
  Adjusted = stored Kite × recorded drift; Raw = NSE bhavcopy (D/W) or `app.minute_as_traded()` (1m). Raw on a derived
  interval → 422; unknown symbol → 404; `pre_listing` bars excluded. Config `CHART_MAX_BARS` (default 5000).
- The page cursor is the earliest minute **scanned**, so a page whose minutes were all omitted still pages on
  (caught on live RELIANCE data: Friday's 1m had no as-traded measurement yet).
- Frontend `/charts` (nav link): the widget with 112 studies loaded client-side only; `FinTrackFeed` (`src/lib/charts.ts`)
  with strict parsers (rejects non-paisa units, fractional prices, bad OHLC order, non-ascending times); `paisaToRupees`
  at the display edge. A host Adjusted/Raw switch (the widget has none) enabled only where the feed declares raw; a
  banner labels studies as visual-only; a provenance line shows each page's source and omitted-bar count. No order entry.
- Tests: 15 DB tests (`test_candles_api.py`), 12 vitest (`charts.test.ts`).

### Verified in the browser
NIFTY 50 D; RELIANCE D adjusted → Raw ("NSE official bhavcopy"); 15m with RSI (Raw disabled, falls back to Adjusted);
1m Raw ending 15:14 (closing auction) with 360 omitted minutes reported. Layout persists across reloads. The first-visit
default (NIFTY 50, D) could not be exercised in the pane (the widget restored its saved layout every time).

### Noted
- `npm audit`: PostCSS advisories inside Next 15's bundled copy (pre-existing; the fix is Next 16, outside the locked stack).

## [Unreleased] — 1d decisions recorded as provisional (2026-11-20)

The user's five 1d calls were agreed on 2026-11-20, adjusted against measured data, and recorded in DECISIONS as **DEC-1D-DEPS / -TICKS / -CHAIN / -GREEKS / -OWNERS** (provisional). No code, schema or dependency was added; 1d is built only after they are locked.

- **Dependencies:** `websockets` + `pyarrow`. `kiteconnect`/KiteTicker isn't a locked dependency (DEC-K2 chose `httpx`) and would bring 25 packages; this was tested in a throwaway Python 3.12 environment.
- **Ticks:**
  - Full mode, compact schema with depth as fixed columns, hot window ~5 sessions, Parquet as the permanent record
  - fail-closed export (verify before any drop); the unconditional 90-day drop is replaced first
  - **sizing is pending Monday's full-session probe**
- **Option chain:** NIFTY, BANKNIFTY, FINNIFTY and MIDCPNIFTY plus the top 20 stocks, current and next expiry, ATM ±15: about 2,800 contracts and ~13 s per 5-minute snapshot.
- **Greeks:** raw snapshots with the spot and near-month future price; IV/greeks computed in Phase 2.
- **Owners:** new slice **1f — F&O history (archive)**; pre-open feed in Phase 8, with raw pre-open snapshots recorded from 1d.

### Measurements behind them (read-only probes; scratch only, nothing committed)
- **Ticks** (2026-11-20, 13:27–15:40, 224 instruments, Full mode):
  - 1,548,744 packets; median 13,100/min in continuous trading
  - 184-byte stock packets and 32-byte index packets; ~2.3 MB/min on the wire
  - rate roughly halves from 15:15 (closing auction)
- **Option chain:** 45,047 NFO contracts; a full snapshot needs 181 quote requests of 250 (500 per request fails above ~8 KB of URL), 190 s, ~44 MB; 63% have open interest.
- **Synthetic sizing** (to check the pipeline, not the budget): compact Postgres row with 5-level depth arrays ≈ 320–350 B/tick; zstd Parquet ≈ 33 B/tick. Real values come from the full-session probe.

## [Unreleased] — Phase-1 data-coverage gate opened (2026-11-20)

- **The user opened the gate** on the recomputed coverage report (recorded as **DEC-GATE1** in DECISIONS):
  - daily gate 224/224, 0 duplicate dates, indices back to 2015-01-01
  - repairs 218/218 equal NSE official; 0 drift
  - 1m completeness 95.47% (2016+); as-traded-usable equity days 88.2% (2016+)
- **Carried open, not blocking:** the 2015 data policy; the "corroborated" rule for 360ONE and SHRIRAMFIN (both stay `suspect` until decided); the CAS circular text.
- **Next:** lock the five 1d decisions against the measured probe numbers, with Monday's full-session tick count still to come; then build 1d.

## [Unreleased] — Phase 1c close-out: renames, trade-for-trade series, line-based matching (2026-11-20)

The user asked to finish the open 1c items and bring the recomputed coverage report for the Phase-1 gate. DEC-B and CLAUDE.md are updated.

### Added / changed
- **Renames** (migration **0007**, `nse_archive.py`, `nse_load.py`):
  - NSE's `symbolchange.csv` (same static archive host, honest User-Agent) is loaded into `app.symbol_changes` and refreshed every load
  - each day's NSE names are resolved to our current symbol by walking renames backwards, chains included (LTI → LTIM → LTM, ALSTOMT&D → GET&D → GVT&D)
  - `nse_bhav.source_symbol` and `corporate_actions.source_symbol` record NSE's name on that day
  - without any symbol-change list (fetch failed and none stored) the load **does not run**; a 403 stops it as a refusal
- **Trade-for-trade series:** the official row is EQ, else BE, else BZ. New listings trade their first ~10 sessions in BE (e.g. JIOFIN), and relistings or surveillance in BE/BZ. T0 and debt series never count.
- **Parser versioning** (`PARSE_VERSION` = 2, `nse_archive_files.parse_version`): a version bump re-fetches each older day once and inserts only missing rows; stored rows are never changed.
- **Step matching on the affine line** (`adjustment.py`). A step is explained when some combination of the boundary's actions predicts the older window's line from the newer one within `ADJ_MATCH` across the older window's price range.
  - The Kite map is old = a_new·(r·P − s) + b_new: a split or bonus scales a and keeps b; a subtracted dividend shifts b.
  - Kite may apply **only some** of a day's actions (INFY 2018-09-11: the bonus, not the dividend). "None applied" is never an explanation; it only sizes the step.
  - This fixed the blended-ratio misreads, e.g. IOC's bonuses under a −₹14 offset.
- **Parser:** "FV SPL-RS10TORS5" (no spaces around TO, TECHM 2015) now parses.
- **Stored purposes are re-parsed** with the current parser at the start of each analysis, so fixes reach old rows without a re-download (2 rows changed).
- **Tests:** rename parsing and chains; BE fallback and EQ preference; the version-2 re-scan adding only missing rows; refusal, fallback and stop rules for the symbol-change list; line matching (IOC, INFY, CUB patterns, none-applied); re-parse. Backend 385 passed.

### Applied on real data (2026-11-20)
- **Re-scan:** all 2,944 archive days are now at parser version 2 (2018-01-02 stays an accepted gap).
  - +36,531 official rows: 34,968 under former names across 25 old symbols, 2,241 BE and 36 BZ; now 555,680 rows
  - 2,047 corporate actions stored under former names; 13,808 actions in total
- **Measured adjustment** (224 symbols, 0 errors):
  - **unmeasurable days fell from 36,293 to 913**
  - **suspect steps fell from 28 to 4**: 360ONE 2023-03-02 (×0.25) and SHRIRAMFIN 2025-01-10 (×0.20) are actions **missing from NSE's own corporate-action file** but visible in NSE's official prices; NMDC 2020-08-21 (3.25%) and 2022-07-12 (4.01%) have no action at all
  - steps: 235 explained, 83 attributed, 843 unexplained_small
  - **usable for as-traded 1m: 2016+ 88.2%** (2015: 82.9%)
  - 63 symbols carry under-determined windows (Kite vendor noise beyond the grid bound); the top 10 hold 44%
- **Closing auction:** **SEBI circular HO/47/11/11(3)2025-MRD-POD2/I/2765/2026, 16 Jan 2026**, "Introduction of Closing Auction Session (CAS) in the Equity Cash Segment and certain modifications in the Pre-Open Auction Session", found via SEBI's own site search.
  - Its body is script-loaded and couldn't be read here. Status: **identified, text not yet read** (no browser impersonation).
  - The data shows it took effect on 2026-08-03 and rolled out per instrument.
- **Daily gate:** PASS, 224/224, 0 duplicate dates, max missing-weekday gap 5.19%, indices back to 2015-01-01.
- **Repairs:** 218/218 equal NSE's official close. 0 drift events.

### Open for the user
- **Unlisted but visible actions** (360ONE, SHRIRAMFIN): keep them `suspect` (current rule), or add a rule that a large step matching NSE's own official price gap on the ex-date is `corroborated`.
- **2015 data policy** (1m 94.16% complete vs 95.47% from 2016; as-traded-usable 82.9% vs 88.2%).

## [Unreleased] — Phase 1c, round 3: grid-aware fit tolerance (2026-11-20)

Approved by the user ("Allow grid tolerance"). DEC-B, ARCHITECTURE §5 and CLAUDE.md are updated.

### Changed
- **The fit bound is half the window's measured Kite price grid, never below ±2.5 paisa** (`adjustment.py`):
  - ±2.5 on a 5-paisa grid, ±5 on a 10-paisa grid
  - a window counts as 10-paisa only if every Kite price in it is a multiple of 10 and it has ≥ `ADJ_MIN_WINDOW_DAYS`×4 prices, so a 5-paisa series can't qualify by chance
  - every check (joining, re-fitting, blips, merging, step detection, the official identity check) uses each point's own window bound; a fit across mixed grids can't borrow slack
  - each day records the bound used (`tol_paisa`), and the report counts days per bound
- **Tests first:**
  - 10-paisa-grid windows fit and invert within ±5
  - a 5-paisa grid with ±4 jitter is still rejected (the bound stays ±2.5)
  - too few prices to prove a 10-paisa grid keeps ±2.5
  - every accepted day stays within its own bound
- **Totals:** backend 373 passed; frontend unchanged (59 passed).

### Real-data re-run (all 224 symbols, 0 errors)

| | strict ±2.5 | grid-aware |
|---|---|---|
| measured symbol-days | 429,565 (74.9%) | **452,326 (78.8%)** |
| underdetermined | 81,202 (14.2%) | **58,092 (10.1%)** |
| days fitted at ±5 (10-paisa grid) | — | 90,763 |
| steps: explained / attributed / unexplained_small / suspect | 164 / 160 / 604 / 27 | 176 / 168 / 754 / 28 |

- More small unexplained steps is expected: windows that used to be under-determined are now fitted, so their steps are visible and flagged instead of hidden.
- **The remaining under-determined days are concentrated:** 56 symbols, with the top 10 holding 45% (ULTRACEMCO, OFSS, MCX, GODFRYPHLP, BPCL, BAJAJ-AUTO, BAJFINANCE, PETRONET, NAUKRI, ITC), mostly across all of 2015–2024. That's Kite's own daily OHLC noise beyond the grid bound. It's flagged, and it affects only *derived* as-traded 1m; as-traded daily is NSE's official data.
- **2016+ equity days usable for as-traded 1m: 82.1%.**
- **Daily gate:** PASS, 224/224, 0 duplicate dates.

## [Unreleased] — Phase 1c, round 2: affine measurement, per-instrument calendar, listing-aware gate (2026-11-20)

All approved by the user 2026-11-20 with conditions. Decisions: DEC-B (now affine) and the new **DEC-1B-CAL** (per-instrument calendar); both supersede the earlier ratio and cross-sectional wording. ARCHITECTURE §5 and CLAUDE.md are updated.

### Added / changed
- **Affine measurement** (`adjustment.py`, migration **0006**). Kite = a·NSE + b per window, fitted **minimax**: the worst residual over every open/high/low/close point must be ≤ `ADJ_TOLERANCE_PAISA` (±2.5), or the fit is not accepted.
  - Windows are forced at NSE ex-dates and grown greedily; a and b may step only at a matching NSE action, and an unmatched step is flagged.
  - Windows with fewer than `ADJ_MIN_WINDOW_DAYS` (5) measured days are `underdetermined` (no as-traded).
  - Neighbours merge only when their union fits and their 1m grain agrees.
  - As-traded 1m = (Kite ÷ G − b) ÷ a, in `app.minute_as_traded()`.
  - **Golden tests:** subtractive dividends, bonus plus subtracted dividends on the 5-paisa tick, the inverse recovering NSE, under-determined short windows, noise never accepted beyond tolerance, tick rounding never making steps, zero Kite volume never making a zero volume factor.
- **Per-instrument session calendar** (`session_calendar.py`, `app.intraday_session_status`, DEC-1B-CAL):
  - each session is judged against the instrument's own norm (the mode of its first 20 sessions)
  - the norm is re-baselined only on a **persistent** step: a new length held for 10 consecutive sessions (`INTRADAY_NORM_STEP_SESSIONS`; a week-long feed gap doesn't qualify)
  - **market-wide short days** (≥ 50% of an exchange's instruments short that day) are `market_short`: reported, not flagged
  - indices are judged against their own norm, with no percentile over 4 symbols
  - refreshed after every intraday run and analysis
- **Listing- and suspension-aware daily gap check** (`coverage.py`). A long gap (≥ 60 NSE sessions per the archive ledger) with no NSE trading for the symbol is:
  - **pre-listing** if NSE never traded the symbol before it; earlier bars are flagged `pre_listing`, kept, and not counted
  - a **suspension** if NSE did trade it before; those weekdays aren't counted as missing and nothing is flagged
  - Gaps the archive can't vouch for stay counted as missing.
- **Audited archive-day reset**, `POST /api/data/nse-archive/reset-day?day&reason[&purge]`. Purged official rows are logged one by one in `app.data_repairs`, and the day is queued for one re-fetch. A zip whose members carry another day's date is `missing` (an accepted gap), not retried daily.
- **Admin page:** market-wide short days and "no norm yet" on the 1m panel; the `underdetermined` quality on the adjustments panel.
- **Tests:** backend 369 passed (unit + DB); frontend lint, format, types and 59 vitest pass.

### Applied on real data (2026-11-20)
- **2018-01-02:** 162 wrong rows (really 2019-01-02 prices) purged, every one logged. The single re-fetch was again refused ("zip for 2018-01-02 holds bc file dated 2019-01-02"), so the day is an **accepted one-day gap**. 2018-01-03 re-fetched cleanly (156 rows); the prev-close chain holds.
- **2025-03-28:** re-loaded with keep-one-if-identical; CGPOWER, HYUNDAI, INDHOTEL and SWIGGY now have their rows.
- **Daily coverage gate: PASS, 224/224 sufficient (100%)**, 573,489 clean bars, 0 duplicate dates.
  - **The 3 earlier failures were a gate bug: ghost pre-listing segments with zero post-listing holes.**
    - SBICARD: 20 bars in Jan 2015, listed 2020-03-16
    - DELHIVERY: 8 bars in 2016, listed 2022-05-24
    - 360ONE: 256 bars from 2015 to Jan 2016; real history from its 2019-09-19 IPO, as IIFLWAM until the 2023-01-23 rename, is kept
  - **FORCEMOT** is a suspension, not a hole: NSE's archive has no FORCEMOT in any series on 2023-12-01 or 2024-01-15. Its 79 suspended weekdays aren't counted; gap 4.96%.
- **1m completeness (per instrument):**
  - 566,709 sessions: 530,288 complete, 25,020 short (518,107 missing minutes), 579 excess, **10,822 market-short**, 0 unknown, 289 missing sessions
  - 2016+: 95.47% complete of judged sessions; 2015: 94.16%
  - Market-short days are exactly the special sessions (Muhurat 2026-11-08, 2025-10-21, 2024-11-01, 2023-11-12; NSE DR drills 2024-03-02 and 2024-05-18)
- **Affine measurement, all 224 symbols:**
  - 429,565 symbol-days measured (74.9%), 1,386 carried, 2,944 blips, 4,717 suspect, **81,202 underdetermined (14.2%)**, 36,298 unmeasurable, 17,661 indices (not applicable)
  - steps: 164 explained, 160 attributed, 604 unexplained_small, 27 suspect
  - 2016+ equities: 78.1% of days usable for as-traded 1m

### Found — the ±2.5-paisa condition can't be met for part of history (decision needed)
- **Kite's adjusted prices are not always on a 5-paisa grid.** For some symbols and years every price is a multiple of **10 paisa** (ADANIENT 2015, BAJFINANCE, BAJAJ-AUTO 2019; 38 of 80 symbols in 2025 Q3), so rounding alone is up to ±5 paisa.
- **Kite's daily OHLC carries a little vendor noise against NSE's official OHLC.** BAJFINANCE 2024 fits a = 0.100000 exactly, yet deviates by up to 35 paisa. It's probably built from Kite's own ticks.
- **Best achievable minimax residual on action-free quarters:**

  | Quarter | Symbols | within ±2.5 p | ±5 p | ±10 p |
  |---|---|---|---|---|
  | 2019 Q1 | 112 | 85 | 99 | 104 |
  | 2025 Q3 | 80 | 71 | 77 | 78 |

- **Consequence:** under the strict rule, ADANIENT's 2015 history breaks into one-day windows, and that is where most of the 81,202 under-determined days come from.
- **Option for the user:** make the tolerance **half the window's measured Kite price grid** (±2.5 on a 5-paisa grid, ±5 on a 10-paisa grid). This is the rounding bound the ±2.5 rule was based on; genuine vendor noise beyond it stays flagged.

### Other notes
- **Closing auction:** inferred, pending the circular text. Not confirmed by reading sebi.gov.in; we will not impersonate a browser to read it.
- **Renames:** NSE's `symbolchange.csv` (same archive host) shows IIFLWAM → 360ONE (2023-01-23) and BAJAJTEMPO → FORCEMOT (2019-08-19). Official rows under old names weren't loaded, so pre-rename days are `unmeasurable`. Mapping them would need that file and a re-load of the old-name rows (not done).

## [Unreleased] — Phase 1c: measured adjustments, NSE archive, daily-bar finality (2026-11-20)

DEC-B revised (**measure, don't model**) and DEC-1C-FINAL; ARCHITECTURE §5 rewritten. Approved
by the user 2026-11-20, including the one destructive step (the audited provisional-bar repair).

### Fixed — 1a provisional daily bars
- **Kite publishes a session's official daily close overnight.** Bars fetched the same evening were provisional: on 2026-11-19, 223 of 224 bars were later re-served with the official close.
- **Finality is now next-morning.** A session's 1d bar is final from D+1 at `DAILY_BAR_FINAL_NEXT_DAY_IST` (08:15). The daily job moved to `30 8 * * 1-6`.
- **1m finality is unchanged and now separate** (`INTRADAY_FINAL_AFTER_IST` 16:00). Same-evening 1m bars were verified identical the next morning.
- **Audited repair** (`app/data/repair.py`, `POST /api/data/repair/provisional`). It is the only code path that rewrites a stored bar:
  - it only touches sessions that are final now
  - the replacement must pass the validation gate
  - old values go to quarantine (`superseded_provisional`) and to `app.data_repairs`
- **Real run:** 224 candidates; 218 repaired, 6 confirmed unchanged, 0 errors. **Acceptance: 218/218 repaired closes equal NSE's official close.** Done.

### Added — measure, don't model
- **Migration 0005:**
  - `app.nse_archive_files` (load ledger), `app.corporate_actions`, `app.nse_bhav` (official as-traded)
  - `app.adjustment_days`, `app.adjustment_events`, `app.adjustment_drift`, `app.adjustment_checks`, `app.data_repairs`
  - per-session 1m aggregates on `app.intraday_sessions`
  - quarantine reason `superseded_provisional`; flag `official_mismatch`
  - functions `app.minute_as_traded()` and `app.drift_factor()`
- **NSE archive** (`nse_archive.py`, `nse_load.py`):
  - daily PR zips from `nsearchives.nseindia.com` with an honest User-Agent; only the `Bc` (actions) and `Pd` (official prices) members are read, in memory, size-capped
  - formats from 2015–2026 handled (member-name case, `dd/mm/yyyy` vs ISO dates)
  - 404 → `missing`, 5xx → retried, then `error`; **403 stops the load**. `www.nseindia.com` refuses non-browsers, and we never impersonate one.
- **Measured factor per symbol/day** (`adjustment.py`). Kite's candles are already adjusted, more richly than NSE's list (e.g. INFY ×0.9568 and ITC ×0.9536 on 2025-10-16 with no split or bonus). So:
  - F = Kite ÷ NSE official close, flat inside windows bounded by NSE ex-dates and confirmed jumps (`ADJ_TOLERANCE_PAISA`); a lone deviating day is a flagged **blip**
  - **the 1m grain gets its own ratio G.** Verified, not assumed: RELIANCE 1m/daily = 0.991 until the 2020-05-13 rights ex-date, then 1.000; INFY had a 0.1% step 2018-12-24 → 2021-05-11
  - **volume gets its own factor V.** Kite scales volume only for share-count events: ASHOKLEY 2016 price ×0.4763, volume ×2.0; 75 adjusted symbol-days had unchanged volume
  - every step is classified: explained / attributed / unexplained_small (badge, data kept) / suspect (both windows: no as-traded derived) / official_mismatch (newest window ≠ 1)
- **Drift re-check.** Each daily and 1m catch-up now resumes **on** the newest stored session. A consistent rescaling means Kite re-adjusted history: it goes to `app.adjustment_drift` (stored bars are never rewritten) and is applied on read by `app.drift_factor()`. Every check is recorded, so a symbol that missed its check shows "may be stale".
- **API:**
  - `POST /api/data/ingest/nse-archive`, `POST /api/data/adjustments/analyse` (bearer)
  - `GET /api/data/adjustments[?symbol]`
  - `GET /api/data/as-traded?grain=1d|1m`: 1d is NSE official, 1m derived, NULL where unusable
  - `GET /api/data/repairs` (acceptance against official)
  - the daily job chains the archive load and analysis (`NSE_ARCHIVE_AFTER_DAILY`)
- **Admin page:** a corporate actions & adjustments panel (archive days, step classes, day quality, stale checks, repair acceptance, review list) and the new job names.
- **Tests (first):**
  - 17 golden analysis cases (split, bonus, stated/unstated dividend, Kite ignoring a dividend, small/large unexplained steps, a disagreeing action, rounding micro-noise over 20 seeds, blips, carried and unmeasurable days, the rights-style grain step, official mismatch, the provisional newest bar, indices)
  - 35 archive parser/fetcher cases
  - 12 finality cases
  - DB tests for the load, analysis, as-traded SQL, repair (refused before finality, repaired, confirmed, unusable replacement), daily and 1m drift, non-proportional conflicts, and the daily chain
  - 8 frontend contract cases

### Real-data results (2026-11-20)
- **NSE archive:** 2,945/2,945 trading days loaded (2015-01-01 → 2026-11-19), 0 missing, 0 errors; 519,149 official as-traded rows and 11,761 corporate-action rows. The parsed actions are 1,879 dividends, all with a stated amount; 24 bonuses; 15 splits; 40 "other" (buy-backs, demergers, a rights first call), which are never applied.
- **Rejected, never guessed:** 461 corporate-action rows without an ex-date (debt series such as CHOLAFIN D1/S1 "CONVERSION INTO EQUITY"), and 4 price rows on 2025-03-28 that NSE listed twice.

### Fixed while loading (both found on real data)
- **NSE's archive mislabels a day.** `PR020118.zip` (2018-01-02) contains the **2019-01-02** files. The loader now refuses any zip whose member files are dated differently from the requested date; tested on that exact case.
  - The `prev_close` chain check (each day's previous close must equal the prior day's close) finds no other broken day in 2,944.
  - The 162 rows already stored under 2018-01-02 need removing; the correction waits for approval.
- **NSE repeats rows verbatim.** On 2025-03-28 it listed BAJAJHFL, CGPOWER, HYUNDAI, INDHOTEL and SWIGGY twice with identical values. Identical repeats are now kept once; repeats with different values are still rejected entirely.

### Found — Kite's adjustment is affine, not a ratio (approved 2026-11-20 → built; see the next section)
- The first analysis pass made ~27 "small unexplained steps" per symbol. The cause: for part of history Kite **subtracts** dividends rather than scaling, so Kite/NSE tracks price inside a window.
- Fitting **Kite = a × NSE + b** per window is exact to Kite's 5-paisa tick:

  | Window | a | b | max error, affine | max error, ratio |
  |---|---|---|---|---|
  | ASHOKLEY 2018 Q1 | 0.49980 | −₹2.45 | 2.9 p | 32.7 p |
  | ASHOKLEY 2015 Q1 | 0.50001 | −₹2.47 | 2.8 p | 42.4 p |
  | TATASTEEL Aug–Oct 2022 | 0.97720 | −₹3.52 | 0.5 p | 34.9 p |
  | INFY / ITC mid-2025 | — | ≈ 0 (pure ratio) | 9.4 p / 5.1 p | same |

- Kite's adjusted prices are on the 5-paisa tick, so the tolerance must allow ±2.5 p of rounding.
- The interim ratio-model results (52 symbols) were fully replaced by the affine run below.

### Found — the 15:14 equity cutoff is a closing auction (not a Kite defect)
- **What the data shows:** from 2026-08-03, NSE equities' 1m series ends at 15:14 (indices still 15:29), and the 1m share of daily volume drops from ~0.99 to 0.94–0.97 (RELIANCE 0.993→0.937). The missing volume trades after 15:14 and never appears in minute bars.
- **SEBI** published "Review of certain aspects of the Closing Auction Session…" on 15 Oct 2026, so a closing-auction session exists. Its text couldn't be read here (script-loaded page; the browser pane refused sebi.gov.in).
- **Phased rollout:** 206 of 218 equities switched on 2026-08-03; the rest on 08-26 (3), 09-30 (3) and 10-28 (6), e.g. MAHABANK, ANANDRATHI, COROMANDEL. These look like F&O-inclusion cycles (inferred).
- **Consequence for the calendar:** expected minutes depend on the **instrument**, not only the exchange and date. That's an input to the open p95/clean expected-minutes rule (gate).

## [Unreleased] — Admin/Health page + 1c pre-build findings (2026-11-20)

### Added
- **`/admin` page**, the Phase-1 subset of UX_DESIGN "Admin/Health". It is read-only, refreshes itself, and parses every response strictly. It shows:
  - system health
  - the **Kite session**, with a *Log in to Kite* button that gets a fresh one-time-state URL each click and only accepts a `kite.zerodha.com` URL
  - scheduled jobs and their next run (IST)
  - recent job runs
  - daily coverage and 1-minute completeness, plus the symbols needing the most review
  - quarantine and flag counts by reason
  - symbols whose last fetch failed
  - Job-start buttons are deliberately left out: they need the bearer token, which never reaches the browser.
  - A Home · Admin nav bar.
- **`GET /api/admin/overview`** returns the scheduler's jobs, an in-memory run history, the quarantine and flag breakdown, and cursor errors.
  - The run history has a bounded size (`JOB_HISTORY_SIZE`, default 20) and starts empty after each restart. The response reports this through `history_since`, and the page shows it.
- **Tests:** a DB integration test for the overview; the 1-minute end-to-end test now asserts the finished run appears in it; a unit test checks the history is bounded and newest first; 17 frontend parser tests, including rejection cases.

### Found while preparing 1c (not built yet; waiting on approval)
- **Kite's history is adjusted, and Kite has no corporate-actions API.** TATASTEEL's stored close on the day before its 1:10 split is ₹90.25, against a real price near ₹1,000; the 1-minute bars are adjusted the same way. Comparing stored closes with NSE's official bhavcopy closes shows adjustments beyond splits and bonuses. On 2025-10-16, 181 of 215 stocks match exactly, but INFY reads ×0.9568 and ITC ×0.9536.
- **The 2026-11-19 daily bars are provisional.** All 224 were fetched at 18:18–18:27 IST, before Kite published the official close. Kite now returns the official close, and 8 of 8 re-fetched symbols differ from what we stored. All older days and the 1-minute bars are unaffected (RELIANCE: 360 of 360 minutes identical). This means `DAILY_BAR_FINAL_AFTER_IST=16:00` is wrong.
- **NSE's archive works with an honest client.** `nsearchives.nseindia.com` daily PR zips (back to 2015) contain the corporate-actions file (`Bc`) and official prices (`Pd`). The `www.nseindia.com` API returns 403 to non-browser clients, and we don't impersonate a browser.

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
