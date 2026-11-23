# FinTrack v2 — Test Plan

> Created 2026-11-19. Testing is a first-class principle ("test-with-every-change"), not a
> phase. This defines the layers, tooling, the per-build-phase gates, and the harnesses.
> Consistent with ARCHITECTURE.md §11 and BUILD_PLAN exit gates.

> Recovery status: the Phase 5 exit-flow and store-integration files contain skipped
> placeholders. They are not exit-gate evidence. The restored CI workflow also needs a
> successful hosted run; remaining integration work is listed in PHASE_1_5_HANDOFF.md.

## Principles
1. **Determinism or nothing** — frozen clock, mocked Kite/LLM/network, in-memory cache. No test depends on wall-clock or live data.
2. **Golden + regression** — golden = "is this number correct" (vs a reference lib); regression = "did an output change vs the last blessed run." You need both (v1's MA bug was correct-type but wrong-value, and nothing noticed it changed).
3. **Fail closed** — assert the *rejection* paths (QC rejects, guardrails block in Realistic mode, stale data excluded, LLM-failure → excluded), not just the happy path.
4. **A phase isn't done until its exit-gate test is green.** CI gates merges.

## Layers & tooling
| Layer | Purpose | Tool |
|---|---|---|
| Unit | indicators, sizing, scoring, QC, conversions | pytest, pytest-asyncio |
| Golden/reference | indicator values vs independent impl | pandas-ta / TA-Lib |
| Property | invariants (paisa≥0, OHLC order, whole-lots, conviction∈[0,100]) | hypothesis |
| Integration | agents vs a dedicated test TimescaleDB, mocked network | **local/CI TimescaleDB** (native — no Docker/testcontainers) |
| Contract/API | route shapes, status, auth 401s | httpx ASGITransport |
| Regression snapshot | full pipeline on frozen fixtures → committed goldens | syrupy / JSON |
| Frontend | hooks/components, formatter, honest-UI | vitest + RTL; playwright smokes |

## Enabling seams (built in Phase 0)
Candle factory (trend/chop/gap/split/bad-bar), frozen-clock fixture, mocked Kite + LLM,
in-memory cache fixture. These make every test deterministic.

## Per-phase exit gates (the spine — each is a test suite)
- **P0:** CI runs a real-but-empty suite green; lint/type clean.
- **P1 data:** coverage report green; dedup-by-date enforced (same day twice → 1 row); validation rejects bad bars to quarantine; split-bar kept not deleted.
- **P2 indicators:** **golden** (vs reference) + **convergence** (EMA warmup; EMA200 unavailable <200 bars) green on real P1 data.
- **P3 scoring:** every candidate number reproducible by hand; **same inputs → same conviction across swing/intraday**; news agent fails closed; regression snapshot blessed. Verify that each suggestion's visible explanation matches the evaluated rules, timestamps, contract checks and calibration status.
- **P4 risk:** sizing reproducible; Realistic-mode caps enforced; **Learning mode never blocks + always shows risk** (asserted).
- **P5 sandbox:** kill-switch/pause blocks all order paths + survives restart; idempotency rejects duplicates; paper P&L == hand-computed cost example.
- **P6 backtest:** **chronological** max-drawdown == hand value; **backtest↔live↔paper parity** green; zero-edge fixture net-negative after costs.
- **P7 options:** payoff math validated; multi-leg fills + margin modeled.
- **P8 frontend:** tsc clean; one formatter (no local ones); one sentiment value across all surfaces; no UI number contradicts the backend; a11y smoke.
- **P9 ops:** restore-from-backup tested; dead-man's-switch fires on a stalled scheduler.
- **P10 forward-test:** reconciliation + pass-criteria evaluation (calendar, not unit).

## The regression-snapshot harness (catches "a score changed overnight")
Freeze a representative market day (candles/fundamentals/news, ~20 symbols) as committed
fixtures → run the full pipeline (frozen clock, mocked LLM) → serialize every indicator value,
agent score, and the candidate list to committed golden JSON → CI diffs. Any numeric change
**fails** with a readable diff; intended changes are re-blessed with `--snapshot-update` after
a human reviews the diff. This is the net for *unanticipated* regressions — build it in P2,
extend it every phase.

Phase 2 runs the indicator and option reference comparisons from
`tests/fixtures/analytics_golden.json`. The generator uses TA-Lib 0.6.8 and QuantLib 1.40 from
an isolated environment. CI compares the checked-in values without those dependencies.
`pytest tests/unit/test_phase2_snapshot.py --snapshot-update` intentionally regenerates
`analytics_regression.json`. Review the diff before accepting the updated baseline.

## Parity tests (the honest-validator net)
- backtest detector path vs live scan path → identical signals for the same bar.
- backtest P&L vs paper P&L → equal for the same trade under the same cost model.
These enforce the ARCHITECTURE parity contract so the forward-test gate (P10) means something.

## CI
`on: [push, pull_request]` → services: TimescaleDB + (none; in-memory cache) → backend:
lint/type → `pytest -m "not slow" --cov` (floor on `scoring/`, `sandbox/`, `risk/`) →
`-m golden` → `-m regression`. frontend: `tsc --noEmit` → `vitest run` → playwright smoke.
Merge blocked unless golden + regression + guardrail suites are green.

## Standing rule
No change merges without its test. New bug found → first a failing test that reproduces it,
then the fix. The suite is the definition of "done."
