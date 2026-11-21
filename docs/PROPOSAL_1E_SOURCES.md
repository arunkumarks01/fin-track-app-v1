# Slice 1e — reference data: proposed sources (2026-11-21)

> **Approved 2026-11-21 as proposed** (DEC-1E-SOURCES): Q1 (a), Q2 (a), Q3 (a), `defusedxml`
> approved; `FRED_API_KEY` is yours to create. Build follows "Order of work" below.

DEC-P1: 1e sources are **proposed and approved before build**. This covers the Phase-1 reference
list (SCOPE §1, BUILD_PLAN Phase 1): delivery %, FII/DII, bulk/block deals, economic calendar,
India VIX, GIFT Nifty, US futures, commodities, news (RSS + company announcements/filings) and
fundamentals.

**Rules applied:**
- D3: Kite where available, a licensed vendor otherwise, never Yahoo.
- Honest client only: a fixed, truthful User-Agent. A **403 / refusal stops the source**; we never
  impersonate a browser or work around a block.
- Fail closed, nothing hardcoded, integer paisa.

**Every source below was probed on 2026-11-21:** from this machine, and from the VPS where the
local network failed. Nothing was stored. "Verified" means the file or feed answered with the
format shown.

## Summary

| Dataset | Proposed source | Status | History | Cadence |
|---|---|---|---|---|
| Delivery % | NSE archive `MTO_ddmmyyyy.DAT` | ✅ verified | **2015 →** (checked 2015-01-05, 2016-01-04, 2026-11-20) | daily, after close |
| FII/DII **derivatives** positioning | NSE archive `fao_participant_oi_ddmmyyyy.csv` + `_vol_` (Client / DII / FII / Pro) | ✅ verified | **2015 →** | daily |
| FII/DII **cash** flows | — | ❌ no honest source: NSE's website API refuses non-browser clients; NSDL FPI gives **no HTTP response** from this machine or the VPS | — | **your call** (see Q1) |
| Bulk / block deals | NSE archive `content/equities/bulk.csv`, `block.csv` | ✅ verified | **current day only** → we capture forward from the first run | daily, after close |
| Company announcements | NSE PR zip member `An*.txt` (already downloaded by 1c) | ✅ verified | **2015 →** | daily (end of day) |
| Board meetings / results dates | NSE PR zip member `Bm*.txt` | ✅ verified | **2015 →** | daily |
| Market cap / issue size | NSE PR zip member `mcap*.csv` | implemented | From **2024-02-01** per [NSE circular CMTR60423](https://nsearchives.nseindia.com/content/circulars/CMTR60423.pdf) | daily |
| India VIX | **Kite** `NSE:INDIA VIX` (token 264969) | implemented via existing daily and minute extractors, outside the stock universe | as Kite serves | with the existing jobs |
| GIFT Nifty | **Kite** `NSEIX:GIFT NIFTY` (token 291849) | ✅ listed in Kite's NSEIX dump; **history depth to verify Monday** (needs login) | to verify | daily + 1m; live via 1d ticks |
| Commodities | **Kite** MCX futures (CRUDEOIL, GOLD, SILVER, NATURALGAS, COPPER; near month) | ✅ listed in Kite's MCX dump; **historical access to verify Monday** | Kite has **no history for expired contracts** → near-month series builds forward | daily |
| USD/INR | **Kite** CDS `USDINR` futures | ✅ listed | as above | daily |
| US markets | **FRED API** (St. Louis Fed): S&P 500, Nasdaq Composite, Dow (cash, daily), VIXCLS, Brent, US 10Y, USD/INR reference | ✅ API host answers; **needs a free API key** in `.env` | S&P 500 on FRED is the last 10 years (licence) | daily (US close lands ~02:30 IST) |
| US **futures** (live, overnight) | — | ❌ no honest free source | — | **your call** (see Q2) |
| Economic calendar | Reviewed CSV import, each row with its source URL: RBI MPC schedule (rbi.org.in, RSS ✅), MoSPI CPI/IIP/GDP release calendar, FOMC dates (federalreserve.gov ✅), NSE holidays (already derived) | ✅ sources reachable; MoSPI's page is script-rendered, so its calendar is read by hand | forward + a year back | quarterly import, plus RBI RSS daily |
| News (market) | Publisher RSS: Business Standard markets ✅, Mint markets ✅, Economic Times markets ✅ | ✅ verified | **forward only** (RSS) | every 15 min in market hours |
| News (regulatory) | RBI press-release RSS ✅; SEBI RSS (answers, but **empty** today; kept, monitored) | ✅ | forward | hourly |
| Fundamentals (statements) | — | ❌ NSE's results pages and BSE's API (**403**) refuse non-browser clients; NSE's results RSS is stale (lists a long-delisted company) | — | **your call** (see Q3) |

## Per dataset (what gets built)

- **Delivery %** → `app.delivery_daily(symbol, trade_date, traded_qty, deliverable_qty, deliv_bps)`
  - Universe only, `EQ` series. Percent kept as integer basis points.
  - Same ledger and resume model as the 1c archive loader. **About 2,950 files, 56–131 KB each**,
    fetched once at ≤1 req/s.
- **Participant OI / volume** → `app.participant_positions(trade_date, client_type, …14 columns)`.
  The 2015→ history is about 2,950 tiny files.
- **Bulk / block deals** → `app.deals(trade_date, kind, symbol, client, side, qty, price_paisa)`
  - Forward only. A missed day is **recorded as missing**, never back-filled from elsewhere.
- **Announcements / board meetings** → `app.announcements`, `app.board_meetings`
  - Parsed from the same daily PR zips 1c reads. **Correction (2026-11-21):** 1c never saved the
    zips (read in memory only), so the history needs **one more download of every zip** (~2,950
    files, a few hours, at 1 request/second) via a `PARSE_VERSION` bump. Runs after any other load.
  - Headline text only. Classification is the Phase-3 news agent's job (LLM, fail-closed).
- **Kite instruments (VIX, GIFT Nifty, MCX, CDS)**
  - Join the instrument master (new segments `NSEIX`, `MCX`, `CDS`), **not the stock universe**.
  - Use the existing extractors.
  - Futures get a **near-month continuous series built forward**, never a back-adjusted guess.
- **FRED** → `app.macro_series(series_id, obs_date, value_scaled)`
  - Values are scaled integers (index points × 100, yields in bps); each series' unit is stored
    beside it.
  - Fail closed on a missing key.
- **Economic calendar** → `app.economic_events(event_ts, kind, country, title, source_url)`
  - Loaded by an authenticated import endpoint from a reviewed CSV. No dates in code.
- **News RSS** → `app.news_items(published_at, source, title, link, summary, fetched_at)`
  - Headline, link and summary only: **no full-text scraping**.
  - Each feed's `ttl` is respected.
  - Dedup on link.

## Questions for you

1. **FII/DII cash flows.** No honest automated source exists. Options:
   - (a) **Derivatives positioning only (recommended).** Participant OI by FII/DII/Pro/Client is
     daily from 2015 and arguably the better signal.
   - (b) You download NSE's FII/DII CSV from the website yourself and import it through an
     endpoint (manual, daily).
   - (c) A licensed vendor (cost).
2. **US futures.** Options:
   - (a) **Replace with GIFT Nifty (live, Kite) plus FRED's prior US cash close (recommended).**
     Together they are what the Indian pre-open actually reflects.
   - (b) A licensed vendor for live US futures.
3. **Fundamentals (statements).** Options:
   - (a) **Fundamentals-lite now (recommended):** results dates, announcements, delivery %, market
     cap where present. Statement-level FA moves to a later phase with a licensed vendor chosen
     then.
   - (b) Pick a licensed vendor now.
   - (c) Quarterly CSV imports you export from a service you're licensed to use.
4. **New dependencies (ask-first rule):**
   - `defusedxml`, to parse untrusted RSS/XML safely.
   - Nothing else: `fii_stats.xls` is skipped (participant OI covers it), so no `xlrd`.
5. **A FRED API key** goes in `.env` as `FRED_API_KEY`. You create it; I never see it.

## Order of work once approved (status)

1. ✅ **Delivery % plus participant OI/volume** — built 2026-11-21; history (2015→) loading.
2. **Bulk/block deals** — forward capture implemented. Check live coverage after rollout.
3. **News RSS** — scheduled fetch and read endpoint implemented with `defusedxml`.
4. **Announcements, board meetings and market cap** — loader and Stock page implemented.
   The two ledger entries share a single download per PR zip. History still needs to be loaded
   after the step-1 history finishes.
5. **Economic-calendar import** — authenticated CSV import and read endpoint implemented. Loading
   events requires a reviewed CSV with an official source link on each row.
6. **Kite instruments** — read-only checks on 2026-11-21 confirmed daily history for GIFT Nifty
   (1,460 bars in the 2021-06-02 to 2026-11-20 request), minute history (49,231 bars in the
   2026-09-23 to 2026-11-20 request), and five daily bars for each approved near-month MCX future.
   Every sampled GIFT Nifty and MCX bar passed the exact-paisa parser. Kite also returned five
   USDINR futures daily bars, but four failed the parser and the quoted tick size is ₹0.0025.
   CDS ingestion needs an explicit decision on exact sub-paisa representation. GIFT Nifty
   daily and minute ingestion use the existing extractors, with the instrument outside the stock
   universe and minute finality applied overnight. MCX commodity work is paused per the user.
7. **FRED** — ingestion and read endpoint implemented. Live fetching starts once `FRED_API_KEY`
   is configured.

Each step gets its own tests (fixtures, no network) and coverage rows on the Admin page.
