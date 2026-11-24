# Phase 1–5 implementation handoff

This is a work queue, not evidence that any phase has passed its exit gate. The governing
requirements and gates remain in BUILD_PLAN.md, DECISIONS.md and TEST_PLAN.md. Reconcile
this list against the current checkout and database before working; do not replace existing
local changes or change applied migrations. Keep `PAPER_MODE=true`. Destructive tests must
use only the dedicated `_test` database; broker and Telegram calls must be mocked.

## Implementation work, in dependency order

1. **Scheduling and service registration.** Capture, chain and scanner services are wired
   in `app/main.py`. Confirm scheduled and manual control, cancellation, restart and failure
   reporting, particularly after a login that occurs after the capture schedule. Named
   weekday defaults fix APScheduler's numeric weekday interpretation; check any local cron
   overrides separately. Scheduled scans default off. Keep cleanup suspended.
2. **Export integrity and live inputs.** `app/data/tick_export.py` still compares only a
   subset of tick fields and can drop a chunk after a concurrent write. Implement a
   full-record, duplicate-sensitive verification, durable file promotion and a database
   concurrency protocol that makes verification and dropping atomic with respect to
   writers. Test corruption of each field, nulls and duplicates, concurrent late writes,
   failures, crash recovery and restart against TimescaleDB. Until then, do not enable
   export-driven deletion. Implement complete, session-anchored live intraday bars from
   verified inputs and exclude partial/stale bars; check timestamps and volume semantics.
3. **Analytics and scanner validation.** Verify calculations with independent golden,
   convergence and real-data fixtures; test quality, freshness, warm-up and causal inputs.
   Validate both scanner paths and deterministic explanations, missing inputs and contract
   selection. Do not claim the Phase 2 or 3 gate is passed without the specified evidence.
4. **Account-aware risk.** `app/risk/report.py` currently passes no portfolio to risk checks.
   Define explicit account selection and load the correct cash, positions, exposure and
   loss state consistently. Apply guardrails to plans and paper submissions, including
   concurrency, lot sizes and unknown/stale inputs. Add database integration tests.
5. **Paper execution.** Existing equity orders have verified stop/target exits, conservative
   trailing stops without look-ahead, dated marks and P&L. Complete and test partial fills,
   derivatives, square-off, approval and restart/idempotency flows. Replace any remaining
   placeholders with executable tests against `_test`; mock all external messaging. Verify
   guardrails, kill switch and a hand-computed P&L example.
6. **Verification and documentation.** Run backend lint, formatting, mypy and full
   unit/integration suite on the test database, plus frontend lint, format, typecheck,
   tests and build. The restored CI workflow still requires an actual GitHub run. Update
   BUILD_PLAN/TEST_PLAN and CHANGELOG with evidence and open issues, not just code presence.

## Live-market acceptance, separate from implementation

After safe offline work passes, authenticate Kite and perform the market-session tick and
option-chain probe, confirm intraday freshness and scanner inputs, validate live option
snapshots and F&O/reference coverage, check storage/retention behavior on a test dataset,
and record measured evidence against each phase gate. Live news and other external feeds
need separate acceptance if enabled. No real broker orders are authorized by this handoff.

## Phase 6 follow-on status

The five daily-long strategies now have a runnable verified-history service and CLI, causal
next-open entries, protective/time exits, conservative trailing-stop support, shared paper
cost functions and persistent audited results. `/api/backtest` and `/backtest` expose actual
run status/results. Existing v1 study specs keep trailing disabled. Entry-day protection and
unclipped adverse market slippage are explicit study assumptions; full paper/scanner parity
is not claimed. Missing sessions, insufficient warm-up and non-dividend corporate actions
fail the run. Open positions remain marked, not forced closed. Dividend income is excluded.

Production runs 1–20 now cover all five strategies on SBIN, ICICIBANK, ASIANPAINT and
SUNPHARMA over 2025-01-01–2026-11-20, using 800 warm-up/469 study bars, ₹1,00,000 and ten
shares per request. See BUILD_PLAN.md and `docs/data/phase6_daily_long_20261124.json` for the
comparison, input hashes, audit and limitations. RSI bounce is an exploratory forward-data
candidate (3/4 positive marked outcomes, 23 closed trades); EMA's 4/4 positives have just seven
closed trades. Neither is validated or automatically promoted. No forward paper orders were
created. Backend/frontend release is deployed at `/backtest`; applied checksums are preserved.

Next: broader, predeclared symbol coverage, independent out-of-sample and portfolio evaluation,
corporate-action/dividend handling, and full scanner/paper parity. Existing v1 assumptions must
stay versioned. Phase 6 completion and live-market acceptance remain unproven.
