# Open data decisions — options for the user (2026-11-21)

> **Decided 2026-11-21:** both recommendations taken. §1 → **B** (DEC-2015), §2 → **B**
> (DEC-CORROB). See `DECISIONS.md`; this file keeps the facts and options as the record.

Two questions DEC-GATE1 left open (not blocking). Each lists the measured facts, the options,
what each changes, and a recommendation.

---

## 1. The 2015 data policy

### Facts (measured 2026-11-21 on the VPS DB)

| Year | 1m sessions judged complete | Market-wide short days | Instruments with 1m | Equity days with measured as-traded 1m* |
|---|---|---|---|---|
| 2015 | **94.16%** (28,315 / 30,072) | **4,550** | 156 of 224 | 79.8% |
| 2016 | 95.35% | 160 | 163 | 81.1% |
| 2017 | 96.78% | 336 | 176 | 82.0% |
| 2018 | 96.64% | 182 | 183 | 82.6% |

\* share of all `adjustment_days` rows that are `measured` (the gate report's 82.9% / 88.2% use a
narrower denominator; the trend is the same).

- Kite's 1-minute history starts **2015-01-09**, so 2015 is also a partial year.
- 2015 has **~14–28× more market-wide short days** than any later year. Those are Kite's
  feed holes, not real short sessions.
- 2015 is where most under-determined adjustment windows sit (e.g. ADANIENT on a 10-paisa grid).
- **Daily** 2015 is fine and is required by the gate (indices → 2015). This question concerns
  **intraday** only.

### Options

| | What it means | Changes |
|---|---|---|
| **A. Keep, badge only** (today) | 2015 1m is stored, charted and backtestable, with completeness flags | nothing |
| **B. Store, default-exclude** | 2015 1m stays stored and chartable; intraday backtests and statistics **start 2016-01-01 by default**, and 2015 is an explicit opt-in that carries a "2015: feed gaps" badge | one config value (`INTRADAY_ANALYSIS_START`, default `2016-01-01`) read by Phase 2+ code; a CHANGELOG line; no data change |
| **C. Delete 2015 1m** | drop the rows | irreversible; saves only ~1.2 GB (est.: ~34.6k sessions × ~375 bars × ~90 B); not recommended |

**Recommendation: B.** Honest surfaces without losing data. Intraday results never quietly mix
in a year whose feed has 14× the holes, and the data stays there to study.

---

## 2. Actions missing from NSE's list but visible in its prices ("corroborated")

### Facts

| Symbol | Ex-date | Kite step (measured) | NSE official as-traded | NSE action file |
|---|---|---|---|---|
| 360ONE | 2023-03-02 | ×**0.249986** (≈ 3:1 bonus) | prev close ₹1,773.30 → close ₹441.05 | **no entry** |
| SHRIRAMFIN | 2025-01-10 | ×**0.200000** (1:5 split) | prev close ₹2,809.85 → close ₹532.00 | **no entry** |

- Divide the official close by the step and the day's implied return is **−0.5%** for 360ONE and
  **−5.3%** for SHRIRAMFIN. These are ordinary days. Without the step it would read −75% / −81%,
  which is impossible inside a price band.
- NSE's `prev_close` is **never** adjusted in the bhavcopy, even for listed actions (checked on
  CUB's and TRENT's 2026 bonuses), so it gives no evidence either way.
- **Cost of today's rule:** both steps are `suspect`, which makes **1,767 (360ONE) + 1,582
  (SHRIRAMFIN) days** unusable for as-traded 1-minute. Adjusted views are unaffected.
- NMDC's two steps (2020-08-21, 3.25%; 2022-07-12, 4.01%) have no clean ratio and stay `suspect`
  under every option.

### Options

| | Rule | Effect |
|---|---|---|
| **A. Keep `suspect`** (today) | unlisted step ⇒ unusable | 3,349 days stay unusable |
| **B. Automatic `corroborated` class** | an unlisted step is `corroborated` only if **all** of these hold: (1) the measured ratio equals a clean split/bonus ratio p/q (small integers, p,q ≤ 10) within the fit tolerance; (2) NSE's own official close ÷ (previous official close × ratio) − 1 lies inside ±20% (the widest equity price band), while the raw unadjusted return lies outside it; (3) it is the only step in its window. Every `corroborated` step is listed on the Admin review list with its evidence; it is never silent | both cases become usable; NMDC stays `suspect`; a future case is handled the same way and listed |
| **C. Hand-entered actions** | a new `app.manual_actions` table: you enter the action with a source citation; it is audited like a repair | most explicit, but each case needs you |

> As built (DEC-CORROB), condition (3) is made precise: the boundary is **one session** (no day
> between the two windows; the step day's official prev_close equals the older window's last
> official close), the step must have **no** NSE action at all, and the 1m grain must not step.

**Recommendation: B**, with the review-list entry, so a rule decides and you can see every case.
**C** is the fallback if you'd rather no rule ever accepts an action NSE didn't list.
