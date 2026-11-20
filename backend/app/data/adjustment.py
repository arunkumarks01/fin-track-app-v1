"""Measure, don't model: Kite's price adjustment per symbol per day (Phase 1c, DEC-B revised).

Kite's candles arrive already adjusted — more richly than NSE's public action list, and for
part of history by SUBTRACTING dividends rather than scaling. So the adjustment is AFFINE and
nothing is modelled from the action list; it is MEASURED against NSE's official bhavcopy:

  Kite 1d price = a · NSE price + b     per window (a = f_daily, b = b_daily, paisa)
  G  (g_minute) Kite 1m price ÷ Kite 1d price   — 2-of-3 agreement of open/high/low per day
  V  (v_daily)  Kite 1d volume ÷ NSE traded qty — volume has its own factor (share counts)
  VM (v_minute) Kite 1m volume coverage of 1d volume, relative to the symbol's typical coverage
  as-traded = (Kite ÷ G - b) ÷ a

Windows: forced at every NSE ex-date, grown greedily day by day; a day joins only if all its
open/high/low/close points fit the window's line within half the window's MEASURED Kite price
grid, never below ADJ_TOLERANCE_PAISA (±2.5 = half a 5-paisa tick; ±5 where every price sits on
a 10-paisa grid — approved 2026-11-20). Fits are MINIMAX (the worst residual over every point
is what's accepted or rejected — never a least-squares average). A day that breaks the fit
while the next day still fits is a 'blip'; otherwise a new window starts. Adjacent windows
merge when their union still fits (Kite ignored the action) and their 1m grain agrees. Windows
with fewer than ADJ_MIN_WINDOW_DAYS measured days are 'underdetermined' (no as-traded derived).
Every remaining step is attributed to NSE actions:
  explained          the effective ratio matches the parsed split/bonus/stated dividend
  attributed         an action is there but its effect can't be checked (no amount, rights…)
  unexplained_small  no matching action, step ≤ ADJ_UNEXPLAINED_SMALL — badged, data kept
  suspect            no matching action, larger step — both windows become 'suspect'
  official_mismatch  the newest window isn't the identity map (Kite's newest bars ≠ official)
"""

from __future__ import annotations

import bisect
import itertools
import logging
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.clock import Clock
from app.core.config import Settings
from app.data.coverage import listing_dates
from app.data.nse_archive import parse_purpose
from app.data.session_calendar import refresh_session_status

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tol:
    paisa: float
    grain_agree: float
    flat: float
    match: float
    unexplained_small: float
    min_window_days: int = 5

    @classmethod
    def from_settings(cls, s: Settings) -> Tol:
        return cls(
            s.adj_tolerance_paisa,
            s.adj_grain_agree,
            s.adj_flat,
            s.adj_match,
            s.adj_unexplained_small,
            s.adj_min_window_days,
        )


@dataclass(frozen=True)
class Nse:
    open: int
    high: int
    low: int
    close: int
    volume: int
    prev_close: int


@dataclass(frozen=True)
class Minute:
    open: int
    high: int
    low: int
    volume: int


@dataclass(frozen=True)
class DayObs:
    d: date
    open: int  # Kite 1d (stored, adjusted)
    high: int
    low: int
    close: int
    volume: int
    nse: Nse | None = None
    minute: Minute | None = None

    @property
    def f(self) -> float | None:
        return self.close / self.nse.close if self.nse else None


@dataclass(frozen=True)
class Action:
    ids: tuple[int, ...]
    ex_date: date
    kinds: tuple[str, ...]
    purpose: str
    split_from: int | None = None
    split_to: int | None = None
    bonus_new: int | None = None
    bonus_held: int | None = None
    dividend_paisa: int | None = None


@dataclass
class DayResult:
    d: date
    window_no: int
    f_day: float | None
    f_daily: float | None  # a (slope)
    g_minute: float | None
    v_daily: float | None
    v_minute: float | None
    quality: str
    b_daily: float | None = None  # b (offset, paisa)
    tol_paisa: float | None = None  # the fit bound used for this day's window


@dataclass(frozen=True)
class Event:
    effective_date: date
    ratio: float
    g_ratio: float | None
    action_ids: tuple[int, ...]
    classification: str
    detail: str


@dataclass
class Analysis:
    days: list[DayResult]
    events: list[Event]

    @property
    def blips(self) -> list[date]:
        return [r.d for r in self.days if r.quality == "blip"]


EPS = 1e-6
Point = tuple[float, float]  # (NSE price, Kite price), paisa


@dataclass(frozen=True)
class Fit:
    a: float
    b: float
    maxres: float

    def fits(self, pts: Sequence[Point], tol: float) -> bool:
        return all(abs(k - (self.a * n + self.b)) <= tol + EPS for n, k in pts)

    def inverse(self, kite: float) -> float:
        return (kite - self.b) / self.a


def _median(xs: Sequence[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _points(o: DayObs) -> list[Point]:
    n = o.nse
    if n is None:
        return []
    return [(n.open, o.open), (n.high, o.high), (n.low, o.low), (n.close, o.close)]


def _spread(pts: Sequence[Point], a: float) -> tuple[float, float]:
    r = [k - a * n for n, k in pts]
    hi, lo = max(r), min(r)
    return (hi - lo) / 2, (hi + lo) / 2


def minimax_fit(pts: Sequence[Point]) -> Fit:
    """The line minimising the WORST residual over all points (Chebyshev fit). For a fixed
    slope the best offset is the mid-range of residuals, and the resulting worst residual is
    convex in the slope — so a golden-section search finds the optimum exactly enough."""
    xs = [n for n, _ in pts]
    if max(xs) - min(xs) < EPS:  # one price level: slope unidentifiable → pure ratio
        a = statistics.median(k / n for n, k in pts)
        return Fit(a, 0.0, max(abs(k - a * n) for n, k in pts))
    lo, hi = 0.0, 2 * max(k / n for n, k in pts) + 1.0
    g = (5**0.5 - 1) / 2
    x1, x2 = hi - g * (hi - lo), lo + g * (hi - lo)
    f1, f2 = _spread(pts, x1)[0], _spread(pts, x2)[0]
    for _ in range(200):
        if f1 <= f2:
            hi, x2, f2 = x2, x1, f1
            x1 = hi - g * (hi - lo)
            f1 = _spread(pts, x1)[0]
        else:
            lo, x1, f1 = x1, x2, f2
            x2 = lo + g * (hi - lo)
            f2 = _spread(pts, x2)[0]
        if hi - lo < 1e-12:
            break
    a = (lo + hi) / 2
    res, b = _spread(pts, a)
    return Fit(a, b, res)


@dataclass
class _Seg:
    forced: int
    members: list[DayObs] = field(default_factory=list)
    fit: Fit | None = None
    g: float | None = None

    @property
    def pts(self) -> list[Point]:
        return [p for m in self.members for p in _points(m)]

    @property
    def first(self) -> date:
        return self.members[0].d

    @property
    def last(self) -> date:
        return self.members[-1].d


def grain_ratio(o: DayObs, tol: Tol) -> float | None:
    """Kite 1m ÷ Kite 1d price for one day: two of open/high/low ratios must agree (the
    official extreme can print outside the 1m series, so one disagreeing ratio is normal)."""
    m = o.minute
    if m is None or min(o.open, o.high, o.low) <= 0:
        return None
    ro, rh, rl = m.open / o.open, m.high / o.high, m.low / o.low
    for a, b in ((rh, rl), (ro, rh), (ro, rl)):
        if abs(a / b - 1) <= tol.grain_agree:
            return (a + b) / 2
    return None


def _expected(actions: Sequence[Action], prev_close: int | None) -> tuple[float, bool, bool]:
    """(expected ratio, checkable, has_unverifiable) for actions on one boundary."""
    exp, checkable, unverifiable = 1.0, False, False
    for a in actions:
        if "split" in a.kinds and a.split_from and a.split_to:
            exp *= a.split_to / a.split_from
            checkable = True
        if "bonus" in a.kinds and a.bonus_new and a.bonus_held:
            exp *= a.bonus_held / (a.bonus_new + a.bonus_held)
            checkable = True
        if "dividend" in a.kinds:
            if a.dividend_paisa is not None and prev_close:
                exp *= (prev_close - a.dividend_paisa) / prev_close
                checkable = True
            else:
                unverifiable = True
        if "other" in a.kinds:
            unverifiable = True
    return exp, checkable, unverifiable


def _classify(
    r: float, actions: Sequence[Action], prev_close: int | None, tol: Tol, what: str
) -> tuple[str, str]:
    names = "; ".join(sorted({a.purpose for a in actions})) or "no NSE action"
    if not actions:
        mag = abs(r - 1)
        cls = "unexplained_small" if mag <= tol.unexplained_small else "suspect"
        return cls, f"{what} step x{r:.6f} with no NSE action ({mag:.2%})"
    exp, checkable, unverifiable = _expected(actions, prev_close)
    if checkable and abs(r / exp - 1) <= tol.match:
        return "explained", f"{what} step x{r:.6f} matches expected x{exp:.6f} ({names})"
    if unverifiable:
        return "attributed", f"{what} step x{r:.6f} at an action that can't be checked ({names})"
    mag = abs(r / exp - 1)
    cls = "unexplained_small" if mag <= tol.unexplained_small else "suspect"
    return cls, f"{what} step x{r:.6f} vs expected x{exp:.6f} ({names}) — off by {mag:.2%}"


def _grain(days: Sequence[DayObs], tol: Tol) -> float | None:
    return _median([g for o in days if (g := grain_ratio(o, tol)) is not None])


def kite_grid(prices: Sequence[int], min_prices: int) -> int:
    """The price grid Kite's adjusted prices sit on (paisa): 10 only when there are enough
    prices to rule out a 5-paisa series landing on multiples of 10 by chance; else 5 or 1."""
    if len(prices) >= min_prices and all(p % 10 == 0 for p in prices):
        return 10
    if prices and all(p % 5 == 0 for p in prices):
        return 5
    return 1


def _action_options(a: Action, prev_close: int | None) -> list[tuple[str, float, float]]:
    """How Kite may have applied ONE action: (label, multiplier r, subtracted s paisa).
    'not applied' is always an option — Kite sometimes applies a split/bonus but not the
    dividend announced the same day (INFY 2018-09-11)."""
    mult, parts = 1.0, []
    if "split" in a.kinds and a.split_from and a.split_to:
        mult *= a.split_to / a.split_from
        parts.append("split")
    if "bonus" in a.kinds and a.bonus_new and a.bonus_held:
        mult *= a.bonus_held / (a.bonus_new + a.bonus_held)
        parts.append("bonus")
    opts: list[tuple[str, float, float]] = [("not applied", 1.0, 0.0)]
    if parts:
        opts.append(("+".join(parts), mult, 0.0))
    d = a.dividend_paisa if "dividend" in a.kinds else None
    if d and prev_close:
        base = "+".join(parts) + "+" if parts else ""
        opts.append((f"{base}dividend (scaled)", mult * (prev_close - d) / prev_close, 0.0))
        opts.append((f"{base}dividend (subtracted)", mult, float(d)))
    return opts


def _classify_line(
    wa: _Seg,
    wb: _Seg,
    p_lo: float,
    p_hi: float,
    acts: Sequence[Action],
    prev_close: int | None,
    tol: Tol,
) -> tuple[str, str]:
    """Explain the older window's LINE from the newer one: Kite_old(P) = a_new·T(P) + b_new,
    T(P) = r·P - s for some combination of the boundary's actions (a split/bonus scales a and
    leaves b; a subtracted dividend shifts b). Compared over the older window's price range,
    relative to its level — robust to a/b trading off over a narrow range. 'None applied' is
    never an explanation; it only sizes an unexplained step."""
    fa, fb = wa.fit, wb.fit
    if fa is None or fb is None:
        return "unexplained_small", "fit missing"

    def rel_diff(r: float, s: float) -> float:
        worst = 0.0
        for p in (p_lo, p_hi):
            meas = fa.a * p + fa.b
            pred = fb.a * (r * p - s) + fb.b
            worst = max(worst, abs(pred - meas) / meas if meas > 0 else float("inf"))
        return worst

    names = "; ".join(sorted({x.purpose for x in acts})) or "no NSE action"
    line = f"[a {fa.a:.6f} b {fa.b:+.1f}p -> a {fb.a:.6f} b {fb.b:+.1f}p]"
    best_none = rel_diff(1.0, 0.0)
    best: tuple[float, str] | None = None
    for combo in itertools.product(*(_action_options(x, prev_close) for x in acts)):
        if all(label == "not applied" for label, _, _ in combo):
            continue
        r = math.prod(c[1] for c in combo)
        sub = sum(c[2] for c in combo)
        err = rel_diff(r, sub)
        applied = ", ".join(
            f"{x.purpose} as {label}"
            for x, (label, _, _) in zip(acts, combo, strict=True)
            if label != "not applied"
        )
        if best is None or err < best[0]:
            best = (err, applied)
    if best is not None and best[0] <= tol.match:
        return "explained", f"Kite applied {best[1]} (line within {best[0]:.2%}) {line}"
    unverifiable = any(
        "other" in x.kinds or ("dividend" in x.kinds and x.dividend_paisa is None) for x in acts
    )
    if unverifiable:
        return "attributed", f"step at an action that can't be checked ({names}) {line}"
    mag = min(best_none, best[0] if best else float("inf"))
    cls = "unexplained_small" if mag <= tol.unexplained_small else "suspect"
    return cls, f"step not explained by ({names}); off by {mag:.2%} {line}"


def analyse(days: Sequence[DayObs], actions: Sequence[Action], tol: Tol) -> Analysis:
    """Pure: measured affine maps, windows, blips and classified events for ONE equity."""
    days = sorted(days, key=lambda o: o.d)
    ex_dates = sorted({x.ex_date for x in actions})

    def forced(d: date) -> int:
        return bisect.bisect_right(ex_dates, d)

    measured = [o for o in days if o.nse is not None]
    # tolerance per forced window: half its MEASURED Kite price grid, never below the approved
    # floor (ADJ_TOLERANCE_PAISA). A 10-paisa grid needs enough prices to rule out chance.
    prices_by_f: dict[int, list[int]] = {}
    for o in measured:
        prices_by_f.setdefault(forced(o.d), []).extend((o.open, o.high, o.low, o.close))
    tol_f = {
        f: max(tol.paisa, kite_grid(ps, tol.min_window_days * 4) / 2)
        for f, ps in prices_by_f.items()
    }

    def bound(o: DayObs) -> float:
        return tol_f.get(forced(o.d), tol.paisa)

    def ok(fit: Fit, members: Sequence[DayObs]) -> bool:
        """Every point of every member within ITS OWN window's bound."""
        return all(
            abs(k - (fit.a * n + fit.b)) <= bound(m) + EPS for m in members for n, k in _points(m)
        )

    segs: list[_Seg] = []
    blips: set[date] = set()
    for i, o in enumerate(measured):
        f, pts = forced(o.d), _points(o)
        cur = segs[-1] if segs and segs[-1].forced == f else None
        if cur is None or cur.fit is None:
            segs.append(_Seg(f, [o], minimax_fit(pts)))
            continue
        if ok(cur.fit, [o]):
            cur.members.append(o)
            continue
        trial = minimax_fit(cur.pts + pts)
        if ok(trial, [*cur.members, o]):
            cur.members.append(o)
            cur.fit = trial
            continue
        nxt = measured[i + 1] if i + 1 < len(measured) else None
        if nxt is None or forced(nxt.d) != f or ok(cur.fit, [nxt]):
            blips.add(o.d)  # the level doesn't hold (or can't be confirmed): a blip
            continue
        segs.append(_Seg(f, [o], minimax_fit(pts)))

    # merge neighbours whose union still fits and whose 1m grain agrees (no real step)
    windows: list[_Seg] = []
    for s in segs:
        s.g = _grain([o for o in days if s.first <= o.d <= s.last], tol)
        prev = windows[-1] if windows else None
        if prev is not None:
            union = minimax_fit(prev.pts + s.pts)
            grain_ok = prev.g is None or s.g is None or abs(prev.g / s.g - 1) <= tol.grain_agree
            if ok(union, [*prev.members, *s.members]) and grain_ok:
                prev.members.extend(s.members)
                prev.fit = union
                prev.g = _grain([o for o in days if prev.first <= o.d <= prev.last], tol)
                continue
        windows.append(s)

    def underdetermined(w: _Seg) -> bool:
        return len(w.members) < tol.min_window_days

    # events between consecutive determined windows
    events: list[Event] = []
    suspect: set[int] = set()
    valid = [k for k, w in enumerate(windows) if not underdetermined(w)]
    for a_k, b_k in itertools.pairwise(valid):
        wa, wb = windows[a_k], windows[b_k]
        if wa.fit is None or wb.fit is None:
            continue
        prices = [m.nse.close for m in wa.members if m.nse]
        p_bar = statistics.median(prices)
        p_lo, p_hi = (
            min(p.nse.low for p in wa.members if p.nse),
            max(p.nse.high for p in wa.members if p.nse),
        )
        diff = max(abs((wa.fit.a - wb.fit.a) * p + wa.fit.b - wb.fit.b) for p in (p_lo, p_hi))
        ka, kb = wa.fit.a * p_bar + wa.fit.b, wb.fit.a * p_bar + wb.fit.b
        ratio = ka / kb if kb else 1.0
        g_ratio = wa.g / wb.g if wa.g and wb.g else None
        price_step = diff > max(bound(wa.members[0]), bound(wb.members[0])) + EPS
        grain_step = g_ratio is not None and abs(g_ratio - 1) > tol.grain_agree
        if not (price_step or grain_step):
            continue
        acts = [x for x in actions if wa.last < x.ex_date <= wb.first]
        first_b = wb.members[0]
        prev_close = first_b.nse.prev_close if first_b.nse else None
        if price_step:
            cls, detail = _classify_line(wa, wb, p_lo, p_hi, acts, prev_close, tol)
        else:
            cls, detail = _classify(g_ratio or 1.0, acts, None, tol, "1m-grain")
            if cls == "explained":
                cls = "attributed"  # a grain-only step can't be "explained" by a price ratio
        if price_step and grain_step:
            detail += f"; 1m grain also steps x{g_ratio:.6f}"
        if not price_step:  # price steps already describe both lines
            detail += (
                f" [a {wa.fit.a:.6f} b {wa.fit.b:+.1f}p -> a {wb.fit.a:.6f} b {wb.fit.b:+.1f}p]"
            )
        if cls == "suspect":
            suspect.update((a_k, b_k))
        ids = tuple(sorted({i for x in acts for i in x.ids}))
        events.append(Event(wb.first, ratio, g_ratio, ids, cls, detail))

    if valid:
        last = windows[valid[-1]]
        if last.fit is not None and not ok(Fit(1.0, 0.0, 0.0), last.members):
            p_bar = statistics.median(m.nse.close for m in last.members if m.nse)
            events.append(
                Event(
                    last.first,
                    (last.fit.a * p_bar + last.fit.b) / p_bar,
                    None,
                    (),
                    "official_mismatch",
                    f"newest window maps NSE→Kite as a {last.fit.a:.6f} b {last.fit.b:+.1f}p, "
                    "not the identity",
                )
            )

    # per-window volume factors
    # volume ratios only from days where BOTH sides traded (Kite reports 0 on some days)
    all_vm = [
        o.minute.volume / o.volume
        for o in days
        if o.minute and o.minute.volume > 0 and o.volume > 0
    ]
    vm_norm = _median(all_vm)

    results: list[DayResult] = []
    spans = [(w.first, w.last) for w in windows]
    for o in days:
        k = next((j for j, (lo, hi) in enumerate(spans) if lo <= o.d <= hi), None)
        if k is None:
            prior = [j for j, (lo, _) in enumerate(spans) if lo <= o.d]
            results.append(
                DayResult(
                    o.d,
                    prior[-1] if prior else 0,
                    o.f,
                    None,
                    None,
                    None,
                    None,
                    "blip" if o.d in blips else "unmeasurable",
                    None,
                    bound(o),
                )
            )
            continue
        w = windows[k]
        in_range = [x for x in days if w.first <= x.d <= w.last]
        v = _median(
            [
                m.volume / m.nse.volume
                for m in w.members
                if m.nse and m.nse.volume > 0 and m.volume > 0
            ]
        )
        vm = _median(
            [
                x.minute.volume / x.volume
                for x in in_range
                if x.minute and x.minute.volume > 0 and x.volume > 0
            ]
        )
        if k in suspect:
            q = "suspect"
        elif underdetermined(w):
            q = "underdetermined"
        elif o.d in blips:
            q = "blip"
        elif o.nse is not None:
            q = "measured"
        else:
            q = "carried"
        fit = w.fit
        results.append(
            DayResult(
                o.d,
                k,
                o.f,
                fit.a if fit else None,
                w.g,
                v,
                vm / vm_norm if vm is not None and vm_norm else None,
                q,
                fit.b if fit else None,
                bound(o),
            )
        )
    return Analysis(results, events)


def index_days(days: Sequence[date]) -> list[DayResult]:
    """Indices are never adjusted: factors are 1 by definition, quality not_applicable."""
    return [DayResult(d, 0, None, 1.0, 1.0, 1.0, 1.0, "not_applicable", 0.0) for d in days]


# --- persistence -----------------------------------------------------------------------------


def _num(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(format(x, ".12g"))


_DAYS_SQL = """
SELECT (c.ts AT TIME ZONE 'Asia/Kolkata')::date AS d, c.open, c.high, c.low, c.close, c.volume,
       b.open AS n_open, b.high AS n_high, b.low AS n_low, b.close AS n_close,
       b.volume AS n_volume, b.prev_close AS n_prev,
       s.open_first, s.high_max, s.low_min, s.volume_sum
FROM app.ohlcv_candles c
LEFT JOIN app.nse_bhav b
       ON b.symbol = c.symbol AND b.trade_date = (c.ts AT TIME ZONE 'Asia/Kolkata')::date
LEFT JOIN app.instrument_ids i ON i.symbol = c.symbol
LEFT JOIN app.intraday_sessions s
       ON s.sid = i.sid AND s.session_date = (c.ts AT TIME ZONE 'Asia/Kolkata')::date
WHERE c.symbol = $1 AND c.timeframe = '1d'
ORDER BY 1
"""

_BACKFILL_AGG_SQL = """
UPDATE app.intraday_sessions s
SET open_first = a.o, high_max = a.h, low_min = a.l, volume_sum = a.v
FROM (SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d, first(open, ts) AS o,
             max(high) AS h, min(low) AS l, sum(volume) AS v
      FROM app.ohlcv_1m WHERE sid = $1 GROUP BY 1) a
WHERE s.sid = $1 AND s.session_date = a.d AND s.open_first IS NULL
"""


async def load_observations(conn: Any, symbol: str) -> list[DayObs]:
    sid = await conn.fetchval("SELECT sid FROM app.instrument_ids WHERE symbol = $1", symbol)
    if sid is not None and await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM app.intraday_sessions WHERE sid = $1 AND open_first IS NULL)",
        sid,
    ):
        await conn.execute(_BACKFILL_AGG_SQL, sid)  # one-time per symbol (pre-1c sessions)
    out = []
    for r in await conn.fetch(_DAYS_SQL, symbol):
        nse = (
            Nse(r["n_open"], r["n_high"], r["n_low"], r["n_close"], r["n_volume"], r["n_prev"])
            if r["n_close"] is not None
            else None
        )
        minute = (
            Minute(r["open_first"], r["high_max"], r["low_min"], int(r["volume_sum"]))
            if r["open_first"] is not None
            else None
        )
        out.append(
            DayObs(r["d"], r["open"], r["high"], r["low"], r["close"], r["volume"], nse, minute)
        )
    return out


async def load_actions(conn: Any, symbol: str) -> list[Action]:
    rows = await conn.fetch(
        """SELECT array_agg(id ORDER BY id) AS ids, ex_date, purpose, kinds,
                  split_from_paisa, split_to_paisa, bonus_new, bonus_held, dividend_paisa
           FROM app.corporate_actions WHERE symbol = $1
           GROUP BY ex_date, purpose, kinds, split_from_paisa, split_to_paisa, bonus_new,
                    bonus_held, dividend_paisa
           ORDER BY ex_date""",
        symbol,
    )
    return [
        Action(
            tuple(r["ids"]),
            r["ex_date"],
            tuple(r["kinds"]),
            r["purpose"],
            r["split_from_paisa"],
            r["split_to_paisa"],
            r["bonus_new"],
            r["bonus_held"],
            r["dividend_paisa"],
        )
        for r in rows
    ]


async def store_analysis(conn: Any, symbol: str, analysis: Analysis, now: datetime) -> None:
    """Replace one symbol's measured adjustment atomically (caller holds the transaction)."""
    await conn.execute("DELETE FROM app.adjustment_days WHERE symbol = $1", symbol)
    await conn.execute("DELETE FROM app.adjustment_events WHERE symbol = $1", symbol)
    await conn.execute(
        "DELETE FROM app.ohlcv_flags WHERE symbol = $1 AND timeframe = '1d' "
        "AND flag = 'official_mismatch'",
        symbol,
    )
    if analysis.days:
        await conn.copy_records_to_table(
            "adjustment_days",
            schema_name="app",
            columns=[
                "symbol",
                "trade_date",
                "window_no",
                "f_day",
                "f_daily",
                "g_minute",
                "v_daily",
                "v_minute",
                "quality",
                "b_daily",
            ],
            records=[
                (
                    symbol,
                    r.d,
                    r.window_no,
                    _num(r.f_day),
                    _num(r.f_daily),
                    _num(r.g_minute),
                    _num(r.v_daily),
                    _num(r.v_minute),
                    r.quality,
                    _num(r.b_daily),
                )
                for r in analysis.days
            ],
        )
    if analysis.events:
        await conn.executemany(
            """INSERT INTO app.adjustment_events
               (symbol, effective_date, ratio, g_ratio, action_ids, classification, detail)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            [
                (
                    symbol,
                    e.effective_date,
                    _num(e.ratio),
                    _num(e.g_ratio),
                    list(e.action_ids),
                    e.classification,
                    e.detail[:500],
                )
                for e in analysis.events
            ],
        )
    blips = {r.d: r for r in analysis.days if r.quality == "blip"}
    if blips:
        await conn.executemany(
            """INSERT INTO app.ohlcv_flags (symbol, timeframe, ts, flag, detail, flagged_at)
               VALUES ($1, '1d', (($2::date)::timestamp AT TIME ZONE 'Asia/Kolkata'),
                       'official_mismatch', $3, $4)
               ON CONFLICT DO NOTHING""",
            [
                (
                    symbol,
                    d,
                    f"Kite/NSE close x{r.f_day:.6f} off its window's map (a {r.f_daily:.6f})"
                    if r.f_day is not None and r.f_daily is not None
                    else "deviates from its window",
                    now,
                )
                for d, r in blips.items()
            ],
        )


@dataclass
class AnalysisReport:
    started_at: datetime
    symbols: int = 0
    days: int = 0
    events: dict[str, int] = field(default_factory=dict)
    quality: dict[str, int] = field(default_factory=dict)
    pre_listing_bars: int = 0
    sessions_judged: int = 0
    actions_reparsed: int = 0
    days_by_bound: dict[str, int] = field(default_factory=dict)  # fit bound (paisa) → days

    def totals(self) -> dict[str, Any]:
        return {
            "symbols": self.symbols,
            "days": self.days,
            "events": dict(sorted(self.events.items())),
            "quality": dict(sorted(self.quality.items())),
            "pre_listing_bars": self.pre_listing_bars,
            "actions_reparsed": self.actions_reparsed,
            "days_by_bound_paisa": dict(sorted(self.days_by_bound.items())),
            "sessions_judged": self.sessions_judged,
            "inserted": self.days,  # shape shared with the other jobs (Admin page)
            "quarantined": 0,
            "errors": 0,
        }


async def run_adjustment_analysis(
    pool: Any,
    settings: Settings,
    clock: Clock,
    symbols: list[str] | None = None,
    progress: Callable[[AnalysisReport], None] | None = None,
) -> AnalysisReport:
    tol = Tol.from_settings(settings)
    report = AnalysisReport(started_at=clock.now())
    async with pool.acquire() as conn, conn.transaction():
        report.actions_reparsed = await reparse_actions(conn)
    async with pool.acquire() as conn:
        members = await conn.fetch(
            """SELECT symbol, kind FROM app.universe
               WHERE ($1::text[] IS NULL OR symbol = ANY($1::text[])) ORDER BY symbol""",
            symbols,
        )
    for m in members:
        symbol = m["symbol"]
        async with pool.acquire() as conn, conn.transaction():
            if m["kind"] == "index":
                ds = [
                    r["d"]
                    for r in await conn.fetch(
                        """SELECT (ts AT TIME ZONE 'Asia/Kolkata')::date AS d FROM app.ohlcv_candles
                           WHERE symbol = $1 AND timeframe = '1d' ORDER BY 1""",
                        symbol,
                    )
                ]
                analysis = Analysis(index_days(ds), [])
            else:
                analysis = analyse(
                    await load_observations(conn, symbol), await load_actions(conn, symbol), tol
                )
            await store_analysis(conn, symbol, analysis, clock.now())
        report.symbols += 1
        report.days += len(analysis.days)
        for e in analysis.events:
            report.events[e.classification] = report.events.get(e.classification, 0) + 1
        for r in analysis.days:
            report.quality[r.quality] = report.quality.get(r.quality, 0) + 1
            if r.tol_paisa is not None and r.quality != "not_applicable":
                key = f"{r.tol_paisa:g}"
                report.days_by_bound[key] = report.days_by_bound.get(key, 0) + 1
        if progress:
            progress(report)
    async with pool.acquire() as conn, conn.transaction():
        report.pre_listing_bars = await flag_pre_listing(conn, settings, clock.now())
        report.sessions_judged = await refresh_session_status(conn, settings, clock.now())
    log.info("adjustment analysis: %s", report.totals())
    return report


async def reparse_actions(conn: Any) -> int:
    """Re-derive parsed components from each stored PURPOSE with the current parser, so a
    parser fix reaches rows stored earlier (no archive re-download). Returns rows changed."""
    rows = await conn.fetch(
        """SELECT id, purpose, kinds, split_from_paisa, split_to_paisa, bonus_new, bonus_held,
                  dividend_paisa FROM app.corporate_actions"""
    )
    changed = []
    for r in rows:
        p = parse_purpose(r["purpose"])
        new = (
            list(p["kinds"]),  # type: ignore[call-overload]
            p.get("split_from_paisa"),
            p.get("split_to_paisa"),
            p.get("bonus_new"),
            p.get("bonus_held"),
            p.get("dividend_paisa"),
        )
        old = (
            list(r["kinds"]),
            r["split_from_paisa"],
            r["split_to_paisa"],
            r["bonus_new"],
            r["bonus_held"],
            r["dividend_paisa"],
        )
        if new != old:
            changed.append((r["id"], *new))
    if changed:
        await conn.executemany(
            """UPDATE app.corporate_actions SET kinds = $2, split_from_paisa = $3,
                   split_to_paisa = $4, bonus_new = $5, bonus_held = $6, dividend_paisa = $7
               WHERE id = $1""",
            changed,
        )
    return len(changed)


async def flag_pre_listing(conn: Any, settings: Settings, now: datetime) -> int:
    """Flag (never delete) 1d bars before a symbol's verified listing — ghost segments such as
    SBICARD's 20 bars in Jan 2015 against its 2020-03-16 listing."""
    listings = await listing_dates(conn, settings)
    await conn.execute("DELETE FROM app.ohlcv_flags WHERE flag = 'pre_listing'")
    if not listings:
        return 0
    status = await conn.execute(
        """INSERT INTO app.ohlcv_flags (symbol, timeframe, ts, flag, detail, flagged_at)
           SELECT c.symbol, '1d', c.ts, 'pre_listing', 'before verified listing ' || l.listing, $3
           FROM app.ohlcv_candles c
           JOIN unnest($1::text[], $2::date[]) AS l(symbol, listing) ON l.symbol = c.symbol
           WHERE c.timeframe = '1d'
             AND c.ts < (l.listing::timestamp AT TIME ZONE 'Asia/Kolkata')
           ON CONFLICT DO NOTHING""",
        list(listings),
        list(listings.values()),
        now,
    )
    return int(status.split()[-1])
