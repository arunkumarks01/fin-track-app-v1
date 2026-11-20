# Changelog

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
