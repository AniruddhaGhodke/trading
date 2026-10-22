"""
Entry and exit families with ordered parameter grids.

Grids are ORDERED so the lab can test neighbouring settings (e.g. RSI 25/30/35)
for stability. Add a family by appending to ENTRIES / EXITS.
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from core import indicators as ti
from core.plan import ExitPlan


class Ind:
    """Per-stock indicator cache so hundreds of configs don't recompute the same series."""

    def __init__(self, df: pd.DataFrame, regime: pd.Series | None = None):
        self.df = df
        self.c = df["Close"]
        self.regime = regime          # Nifty regime aligned to df (BULL/NEUTRAL/BEAR), optional
        self._cache = {}

    def _get(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def rsi(self, n): return self._get(("rsi", n), lambda: ti.rsi(self.c, n))
    def sma(self, n): return self._get(("sma", n), lambda: ti.sma(self.c, n))
    def ema(self, n): return self._get(("ema", n), lambda: ti.ema(self.c, n))
    def atr(self): return self._get("atr", lambda: ti.atr(self.df))
    def std(self, n): return self._get(("std", n), lambda: self.c.rolling(n).std())
    def prior_high(self, n): return self._get(("ph", n), lambda: self.df["High"].shift(1).rolling(n).max())
    def max_close(self, n): return self._get(("mc", n), lambda: self.c.rolling(n).max())
    def vol_ratio(self): return self._get("vr", lambda: self.df["Volume"] / self.df["Volume"].shift(1).rolling(50).mean())
    def down_streak(self):
        def f():
            down = (self.c < self.c.shift(1)).astype(int)
            return down.groupby((down == 0).cumsum()).cumsum()
        return self._get("ds", f)

    def min_close(self, n): return self._get(("mnc", n), lambda: self.c.rolling(n).min())

    def stoch(self, n):
        def f():
            lo, hi = self.df["Low"].rolling(n).min(), self.df["High"].rolling(n).max()
            return (self.c - lo) / (hi - lo).replace(0, np.nan) * 100
        return self._get(("stoch", n), f)

    def cci(self, n=20):
        def f():
            tp = (self.df["High"] + self.df["Low"] + self.c) / 3
            mad = tp.rolling(n).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
            return (tp - tp.rolling(n).mean()) / (0.015 * mad)
        return self._get(("cci", n), f)

    def connors_rsi(self):
        def f():
            d = np.sign(self.c.diff()).fillna(0)
            streak = d.groupby((d != d.shift()).cumsum()).cumsum()
            prank = self.c.pct_change().rolling(100).apply(lambda x: (x[:-1] < x[-1]).mean() * 100, raw=True)
            return (self.rsi(3) + ti.rsi(streak, 2) + prank) / 3
        return self._get("crsi", f)

    def bandwidth(self): return self._get("bw", lambda: 4 * self.std(20) / self.sma(20))

    def regime_mask(self, kind: str) -> pd.Series:
        """Market-regime filter: bull / bear / neutral / not_neutral / not_bull / not_bear."""
        if self.regime is None:
            raise ValueError("regime filter needs market context")
        r = self.regime
        return {"bull": r == "BULL", "bear": r == "BEAR", "neutral": r == "NEUTRAL",
                "not_neutral": r != "NEUTRAL", "not_bull": r != "BULL", "not_bear": r != "BEAR"}[kind]

    def trend(self, kind: str) -> pd.Series:
        if kind == "none":
            return pd.Series(True, index=self.c.index)
        return self.c > self.sma(int(kind[3:]))   # 'sma200' / 'sma50'


# ---------------------------------------------------------------------------
# Entries: signal at bar close, entry next open
# ---------------------------------------------------------------------------
@dataclass
class Family:
    name: str
    grid: dict            # param -> ordered list of values
    fn: Callable          # (ind, **params) -> bool Series  (entries)
    desc: str = ""


TREND = ["none", "sma50", "sma200"]


def candle(I: Ind, pattern: str) -> pd.Series:
    """Classic bullish candlestick patterns, completed at today's close (backward-looking only)."""
    o, h, l, c = I.df["Open"], I.df["High"], I.df["Low"], I.c
    o1, h1, l1, c1 = o.shift(1), h.shift(1), l.shift(1), c.shift(1)
    o2, h2, l2, c2 = o.shift(2), h.shift(2), l.shift(2), c.shift(2)
    rng, body = h - l, c - o
    rng1, rng2 = h1 - l1, h2 - l2
    if pattern == "hammer":
        lower, upper = np.minimum(o, c) - l, h - np.maximum(o, c)
        return (rng > 0) & (lower >= 2 * body.abs()) & (upper <= 0.3 * rng)
    if pattern == "engulfing":
        return (c1 < o1) & (c > o) & (o <= c1) & (c >= o1)
    if pattern == "piercing":
        return (c1 < o1) & ((o1 - c1) > 0.5 * rng1) & (o < c1) & (c > (o1 + c1) / 2) & (c < o1)
    if pattern == "morning_star":
        return ((c2 < o2) & ((o2 - c2) > 0.6 * rng2) & ((c1 - o1).abs() < 0.3 * rng1)
                & (np.maximum(o1, c1) < c2) & (c > o) & (c > (o2 + c2) / 2))
    if pattern == "three_soldiers":
        def near_high(cc, hh, ll):
            return (hh - cc) <= 0.3 * (hh - ll)
        return ((c > o) & (c1 > o1) & (c2 > o2) & (c > c1) & (c1 > c2)
                & near_high(c, h, l) & near_high(c1, h1, l1) & near_high(c2, h2, l2))
    if pattern == "outside_rev":
        return (l < l1) & (c > h1)
    if pattern == "doji":
        return (rng > 0) & (body.abs() <= 0.1 * rng)
    if pattern == "nr7_break":
        return (rng1 <= rng.shift(1).rolling(7).min()) & (c > h1)
    if pattern == "inside_break":
        return (h1 < h2) & (l1 > l2) & (c > h1)
    raise ValueError(pattern)


CANDLES = ["hammer", "engulfing", "piercing", "morning_star", "three_soldiers",
           "outside_rev", "doji", "nr7_break", "inside_break"]


def _context(I: Ind, kind: str) -> pd.Series:
    if kind == "down5d":   # pattern appears after a pullback (prior 5 days down >= 3%)
        return I.c.shift(1) < I.c.shift(6) * 0.97
    return I.trend(kind)

ENTRIES = [
    Family("rsi_dip", {"n": [2, 3, 5, 14], "level": [5, 10, 20, 30], "trend": TREND},
           lambda I, n, level, trend: (I.rsi(n) < level) & I.trend(trend),
           "RSI(n) below level"),
    Family("down_days", {"days": [2, 3, 4, 5], "trend": TREND},
           lambda I, days, trend: (I.down_streak() >= days) & I.trend(trend),
           "N consecutive lower closes"),
    Family("bb_lower", {"n": [10, 20], "k": [1.5, 2.0, 2.5], "trend": TREND},
           lambda I, n, k, trend: (I.c < I.sma(n) - k * I.std(n)) & I.trend(trend),
           "close below lower Bollinger band"),
    Family("drop_from_high", {"lookback": [5, 10, 20], "drop": [5, 8, 12, 15], "trend": TREND},
           lambda I, lookback, drop, trend: (I.c <= I.max_close(lookback) * (1 - drop / 100)) & I.trend(trend),
           "close X% below its N-day high close"),
    Family("ibs_low", {"ibs": [0.1, 0.2, 0.3], "trend": TREND},
           lambda I, ibs, trend: ((I.c - I.df["Low"]) / (I.df["High"] - I.df["Low"]).replace(0, np.nan) < ibs)
           & (I.c < I.c.shift(1)) & I.trend(trend),
           "down day closing near the low (internal bar strength)"),
    Family("gap_down", {"gap": [1.5, 2.5, 4.0], "trend": TREND},
           lambda I, gap, trend: (I.df["Open"] <= I.c.shift(1) * (1 - gap / 100)) & I.trend(trend),
           "opened X% below prior close"),
    Family("breakout", {"lookback": [10, 20, 55, 100, 250], "vol": [0, 1.5, 2.0], "trend": ["none", "sma200"]},
           lambda I, lookback, vol, trend: (I.c > I.prior_high(lookback)) & (I.vol_ratio() >= vol) & I.trend(trend),
           "close above prior N-day high, optional volume surge"),
    Family("sma_cross_up", {"n": [10, 20, 50], "trend": ["none", "sma200"]},
           lambda I, n, trend: (I.c > I.sma(n)) & (I.c.shift(1) <= I.sma(n).shift(1)) & I.trend(trend),
           "close crosses above SMA(n)"),
    Family("ema_cross", {"fast": [5, 10, 20], "slow": [30, 50, 100]},
           lambda I, fast, slow: (I.ema(fast) > I.ema(slow)) & (I.ema(fast).shift(1) <= I.ema(slow).shift(1)),
           "fast EMA crosses above slow EMA"),
    # --- added 2026-10-08: candlesticks + popular indicator strategies + long-term dips ---
    Family("candle", {"pattern": CANDLES, "context": ["none", "down5d", "sma200"]},
           lambda I, pattern, context: candle(I, pattern) & _context(I, context),
           "bullish candlestick pattern, optionally after a pullback / in uptrend"),
    Family("double7", {"n": [5, 7, 10], "trend": TREND},
           lambda I, n, trend: (I.c <= I.min_close(n)) & I.trend(trend),
           "close at N-day lowest close (Connors Double 7s)"),
    Family("stoch_low", {"n": [5, 14], "level": [10, 20], "trend": TREND},
           lambda I, n, level, trend: (I.stoch(n) < level) & I.trend(trend),
           "stochastic %K below level"),
    Family("cci_low", {"level": [-100, -150, -200], "trend": TREND},
           lambda I, level, trend: (I.cci() < level) & I.trend(trend),
           "CCI(20) below level"),
    Family("connors_rsi", {"level": [5, 10, 15, 20], "trend": TREND},
           lambda I, level, trend: (I.connors_rsi() < level) & I.trend(trend),
           "Connors RSI (RSI3 + streak RSI + percent rank) below level"),
    Family("squeeze_break", {"lookback": [60, 120], "trend": ["none", "sma200"]},
           lambda I, lookback, trend: ((I.bandwidth().rolling(5).min() <= I.bandwidth().rolling(lookback).min())
                                       & (I.c > I.sma(20) + 2 * I.std(20)) & I.trend(trend)),
           "Bollinger squeeze (bandwidth at N-day low) then close above upper band"),
    Family("below_sma", {"n": [20, 50], "dist": [5, 10, 15], "trend": ["none", "sma200"]},
           lambda I, n, dist, trend: (I.c < I.sma(n) * (1 - dist / 100)) & I.trend(trend),
           "close X% below SMA(n)"),
    Family("deep_drop", {"lookback": [126, 252], "drop": [20, 30, 40]},
           lambda I, lookback, drop: I.c <= I.max_close(lookback) * (1 - drop / 100),
           "close X% below its 6/12-month high (long-term dip buying)"),
]

# ---------------------------------------------------------------------------
# Exits: (ind, **params) -> (stop Series, exit_signal Series, ExitPlan)
# ---------------------------------------------------------------------------
def _stop(I, kind, value):
    if value is None:
        return pd.Series(0.0, index=I.c.index)          # no stop
    if kind == "pct":
        return I.c * (1 - value / 100)
    return I.c - value * I.atr()


def _false(I):
    return pd.Series(False, index=I.c.index)


RULES = {
    "close_gt_sma5": lambda I: I.c > I.sma(5),
    "rsi2_gt_70": lambda I: I.rsi(2) > 70,
    "rsi14_gt_50": lambda I: I.rsi(14) > 50,
    "close_lt_sma20": lambda I: I.c < I.sma(20),        # trend-following trail
    "close_lt_sma50": lambda I: I.c < I.sma(50),        # slower trend trail
    "close_at_7d_high": lambda I: I.c >= I.max_close(7),  # Double 7s exit
}

EXITS = [
    Family("time", {"hold": [3, 5, 10, 20, 60, 120, 250]},
           lambda I, hold: (_stop(I, "pct", None), _false(I), ExitPlan(max_hold=hold)),
           "no stop/target, exit after hold days (used to screen entries)"),
    Family("fixed_pct", {"target": [3, 5, 8, 12], "stop": [3, 5, 8, None], "hold": [3, 5, 10, 20]},
           lambda I, target, stop, hold: (_stop(I, "pct", stop), _false(I),
                                          ExitPlan(target_pct=target / 100, max_hold=hold)),
           "take +target%, stop -stop%, else exit after hold days"),
    Family("atr_r", {"stop_atr": [1.5, 2.0, 3.0], "target_r": [1.0, 2.0, 3.0], "hold": [10, 20, 40]},
           lambda I, stop_atr, target_r, hold: (_stop(I, "atr", stop_atr), _false(I),
                                                ExitPlan(target_r=target_r, max_hold=hold)),
           "stop k x ATR, target R x risk"),
    Family("rule", {"rule": list(RULES), "stop_atr": [2.0, 3.0, None], "hold": [5, 10, 20, 60, 120]},
           lambda I, rule, stop_atr, hold: (_stop(I, "atr", stop_atr), RULES[rule](I),
                                            ExitPlan(max_hold=hold)),
           "exit next open when rule fires"),
]

ENTRY_BY_NAME = {f.name: f for f in ENTRIES}
EXIT_BY_NAME = {f.name: f for f in EXITS}


def entry_signal(fam: Family, I: Ind, params: dict) -> pd.Series:
    """Entry signal incl. the optional generic 'regime' filter (e.g. regime=bear)."""
    p = dict(params)
    reg = p.pop("regime", None)
    sig = fam.fn(I, **p).fillna(False).astype(bool)
    return sig & I.regime_mask(reg) if reg else sig


def expand(family: Family) -> list[dict]:
    """All parameter combinations of a family."""
    keys = list(family.grid)
    combos = [{}]
    for k in keys:
        combos = [{**c, k: v} for c in combos for v in family.grid[k]]
    return combos


def neighbours(family: Family, params: dict) -> list[dict]:
    """Configs differing by one step in one ORDERED numeric parameter."""
    out = []
    for k, grid_values in family.grid.items():
        values = [v for v in grid_values if v is not None]
        if params[k] is None or params[k] not in values or not all(isinstance(v, (int, float)) for v in values):
            continue  # categorical (trend, rule) or 'no stop' - no natural neighbour
        i = values.index(params[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(values):
                out.append({**params, k: values[j]})
    return out


def spec(name: str, params: dict) -> str:
    return name + "(" + ",".join(f"{k}={v}" for k, v in params.items()) + ")"


def parse_spec(s: str, registry: dict | None = None) -> tuple[str, dict]:
    """'rsi_dip(n=2,level=10,trend=sma200)' -> ('rsi_dip', {...}) with grid-typed values."""
    name, _, rest = s.strip().partition("(")
    fam = registry.get(name) if registry is not None else (ENTRY_BY_NAME.get(name) or EXIT_BY_NAME.get(name))
    if fam is None:
        raise SystemExit(f"Unknown family '{name}'")
    params = {}
    for kv in filter(None, rest.rstrip(")").split(",")):
        k, v = kv.split("=")
        k, v = k.strip(), v.strip()
        if k == "regime":                     # generic entry filter, not part of any grid
            params[k] = v
            continue
        grid = [x for x in fam.grid[k] if x is not None]
        if v == "None":
            params[k] = None
        elif all(isinstance(x, (int, float)) for x in grid):
            f = float(v)
            params[k] = int(f) if all(isinstance(x, int) for x in grid) and f.is_integer() else f
        else:
            params[k] = v
        if params[k] not in fam.grid[k]:
            print(f"  note: {name} {k}={params[k]} is off-grid (no neighbour test possible)")
    return name, params
