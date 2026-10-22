"""
Intraday research lab - works on HOURLY (Yahoo, ~3y) or 5-MINUTE (Angel One, 2018+) bars.

Everything is defined by CLOCK TIME, so the same family means the same thing on any bar size:
  - Minutes are counted from 09:15. A bar "ends" at start + bar length.
  - The last tradable bar is the one ending at 15:15 (broker square-off): open trades exit
    at its close. Later bars (15:15-15:30) are never traded; they only set the session close.
  - Signals may occur on any tradable bar except the last (entry = next bar open).
  - One trade per stock per session per config. Long and short.

No-lookahead rules:
  - Daily-level inputs (previous day high/low/close, daily SMA20 trend, Nifty regime) come
    from the PREVIOUS session only.
  - Opening-range values are only used after the range is complete; running highs/lows and
    VWAP use bars up to now.

Control: for every real trade, 3 random sessions of the same stock entering at the SAME
time of day in the SAME direction with the SAME exits. Significance: t-stat over
per-session mean of (trade return - control mean).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

import config
from core import data as core_data
from core import intraday_data as idd
from core.execution import Bars, simulate
from core.plan import ExitPlan
from core.regime import market_context
from lab import data as lab_data
from lab.families import Family

SQUARE_OFF = 360          # minutes after 09:15 -> 15:15
EOD = 9999
BAR_MIN = {"1h": 60, "5m": 5}


# ---------------------------------------------------------------------------
# Feature preparation (per stock)
# ---------------------------------------------------------------------------
def prepare(df: pd.DataFrame, regime_daily: pd.Series, bar_min: int) -> pd.DataFrame:
    P = df[["Open", "High", "Low", "Close", "Volume"]].astype("float64").copy()
    sess = pd.Index(P.index.normalize())
    P["session"] = sess
    P["mins"] = ((P.index.hour - 9) * 60 + P.index.minute - 15).astype("int16")
    end = P["mins"] + bar_min
    tradable = end <= SQUARE_OFF
    last_tr = P["mins"].where(tradable).groupby(sess).transform("max")
    P["session_last"] = tradable & (P["mins"] == last_tr)
    P["signal_ok"] = tradable & ~P["session_last"]
    P.attrs["bar_min"] = bar_min

    g = P.groupby("session", sort=False)
    P["day_open"] = g["Open"].transform("first")
    P["run_high"] = g["High"].cummax()
    P["run_low"] = g["Low"].cummin()
    P["ret_open"] = P["Close"] / P["day_open"] - 1
    tp = (P["High"] + P["Low"] + P["Close"]) / 3
    P["vwap"] = (tp * P["Volume"]).groupby(sess).cumsum() / P["Volume"].groupby(sess).cumsum().replace(0, np.nan)

    daily = g.agg(high=("High", "max"), low=("Low", "min"), close=("Close", "last"))
    daily["sma20"] = daily["close"].rolling(20).mean()
    prev = daily.shift(1)
    P["prev_close"] = prev["close"].reindex(sess).to_numpy()
    P["prev_high"] = prev["high"].reindex(sess).to_numpy()
    P["prev_low"] = prev["low"].reindex(sess).to_numpy()
    P["d_up"] = (prev["close"] > prev["sma20"]).reindex(sess).to_numpy()
    P["gap"] = P["day_open"] / P["prev_close"] - 1
    reg = regime_daily.shift(1)
    P["regime"] = reg.reindex(reg.index.union(daily.index)).ffill().reindex(sess).to_numpy()
    P["exit_signal"] = False
    return P


_OR_CACHE = {}


def opening_range(P: pd.DataFrame, n_min: int):
    """(high, low, close) of the first n_min minutes, broadcast to every bar of the session."""
    key = (id(P), n_min)
    if key not in _OR_CACHE:
        inside = P["mins"] < n_min
        hi = P["High"].where(inside).groupby(P["session"]).transform("max")
        lo = P["Low"].where(inside).groupby(P["session"]).transform("min")
        cl = P["Close"].where(inside).groupby(P["session"]).transform("last")
        _OR_CACHE[key] = (hi, lo, cl)
    return _OR_CACHE[key]


def _at(P, end_min):
    """Bars that END at end_min minutes after 09:15."""
    return (P["mins"] + P.attrs["bar_min"]) == end_min


def _first_in_session(cond: pd.Series, P: pd.DataFrame) -> pd.Series:
    c = (cond & P["signal_ok"]).fillna(False).astype(bool)
    return c & (c.astype(int).groupby(P["session"]).cumsum() == 1)


def _trend_ok(P, trend, d):
    if trend == "any":
        return pd.Series(True, index=P.index)
    return (P["d_up"] == True) if d == 1 else (P["d_up"] == False)   # noqa: E712


# ---------------------------------------------------------------------------
# Entry families -> direction Series (+1 long, -1 short, 0 none)
# ---------------------------------------------------------------------------
def f_orb(P, or_min, dir, max_range, trend):
    d = 1 if dir == "long" else -1
    hi, lo, _ = opening_range(P, or_min)
    rng_ok = (hi / lo - 1) * 100 <= max_range
    brk = (P["Close"] > hi) if d == 1 else (P["Close"] < lo)
    cond = (P["mins"] >= or_min) & brk & rng_ok & _trend_ok(P, trend, d)
    return _first_in_session(cond, P).astype(int) * d


def f_gap(P, at, gap, mode, confirm):
    big = P["gap"].abs() * 100 >= gap
    sgn = np.sign(P["gap"]) * (1 if mode == "go" else -1)
    ok = pd.Series(True, index=P.index)
    if confirm == "bar":   # price at signal time is on the trade's side of the open
        ok = np.sign(P["Close"] - P["day_open"]) == sgn
    sig = _at(P, at) & big & ok & P["signal_ok"]
    return sig.astype(int) * sgn.fillna(0).astype(int)


def f_pdh(P, dir, early):
    d = 1 if dir == "long" else -1
    brk = (P["Close"] > P["prev_high"]) if d == 1 else (P["Close"] < P["prev_low"])
    win = P["mins"] < (120 if early == "first2h" else SQUARE_OFF)
    return _first_in_session(brk & win, P).astype(int) * d


def f_ol_oh(P, or_min, tol, dir):
    hi, lo, _ = opening_range(P, or_min)
    at = _at(P, or_min) & P["signal_ok"]
    if dir == "long":   # open = low of the opening range
        return (at & ((P["day_open"] - lo) / P["day_open"] * 100 <= tol)).astype(int)
    return -(at & ((hi - P["day_open"]) / P["day_open"] * 100 <= tol)).astype(int)


def f_late_mom(P, x, mode, source):
    at = _at(P, SQUARE_OFF - 60) & P["signal_ok"]          # signal 14:15, trade the last hour
    if source == "open_to_now":
        r = P["ret_open"]
    else:
        _, _, cl = opening_range(P, 60)
        r = cl / P["day_open"] - 1                           # first-hour return
    sgn = np.sign(r) * (1 if mode == "mom" else -1)
    return (at & (r.abs() * 100 >= x)).astype(int) * sgn.fillna(0).astype(int)


def f_iday_dip(P, x, dir, trend):
    d = 1 if dir == "long" else -1
    cond = (P["ret_open"] * 100 <= -x) if d == 1 else (P["ret_open"] * 100 >= x)
    return _first_in_session(cond & _trend_ok(P, trend, d), P).astype(int) * d


def f_vwap(P, dir, trend):
    d = 1 if dir == "long" else -1
    above = P["Close"] > P["vwap"]
    above_prev = above.groupby(P["session"]).shift(1)
    cross = (above & (above_prev == False)) if d == 1 else (~above & (above_prev == True))  # noqa: E712
    return _first_in_session(cross & (P["mins"] >= 15) & _trend_ok(P, trend, d), P).astype(int) * d


def make_entries(interval: str) -> list[Family]:
    orw = [60] if interval == "1h" else [15, 30, 60]
    gap_at = [60] if interval == "1h" else [5, 15, 60]
    return [
        Family("orb", {"or_min": orw, "dir": ["long", "short"], "max_range": [1.0, 2.0, 99.0],
                       "trend": ["any", "with"]}, f_orb, "close breaks the opening-range high (long) / low (short)"),
        Family("gap", {"at": gap_at, "gap": [0.5, 1.0, 2.0], "mode": ["go", "fade"], "confirm": ["none", "bar"]},
               f_gap, "trade the opening gap N minutes after the open: go = with the gap, fade = against"),
        Family("pdh", {"dir": ["long", "short"], "early": ["first2h", "any"]}, f_pdh,
               "close breaks previous day high (long) / low (short)"),
        Family("ol_oh", {"or_min": orw, "tol": [0.0, 0.1, 0.25], "dir": ["long", "short"]}, f_ol_oh,
               "open = low (long) / open = high (short) of the opening range"),
        Family("late_mom", {"x": [0.5, 1.0, 2.0], "mode": ["mom", "rev"], "source": ["open_to_now", "first_hour"]},
               f_late_mom, "last-hour trade from the day's move so far (momentum or reversal)"),
        Family("iday_dip", {"x": [1.0, 2.0, 3.0], "dir": ["long", "short"], "trend": ["any", "with"]}, f_iday_dip,
               "buy X% below today's open (long) / short X% above it"),
        Family("vwap", {"dir": ["long", "short"], "trend": ["any", "with"]}, f_vwap, "close crosses VWAP"),
    ]


# ---------------------------------------------------------------------------
# Exits: (P, direction, **params) -> (stop Series, ExitPlan); holds are in MINUTES
# ---------------------------------------------------------------------------
def _stop(P, d, pct):
    if pct is None:
        return pd.Series(0.0 if d == 1 else np.inf, index=P.index)
    return P["Close"] * (1 - d * pct / 100)


def _bars(P, minutes):
    return EOD if minutes == EOD else max(1, minutes // P.attrs["bar_min"])


def make_exits(interval: str) -> list[Family]:
    holds = [60, 120, 180, EOD] if interval == "1h" else [15, 30, 60, 120, EOD]
    return [
        Family("itime", {"minutes": holds},
               lambda P, d, minutes: (_stop(P, d, None), ExitPlan(max_hold=_bars(P, minutes))),
               "no stop/target, exit after N minutes or at 15:15"),
        Family("ipct", {"stop": [0.5, 1.0, 2.0, None], "target": [0.5, 1.0, 2.0, None]},
               lambda P, d, stop, target: (_stop(P, d, stop),
                                           ExitPlan(target_pct=target / 100 if target else None, max_hold=EOD)),
               "stop -X%, target +Y%, else exit at 15:15"),
    ]


def registry(interval: str):
    ent, ext = make_entries(interval), make_exits(interval)
    return ent, ext, {f.name: f for f in ent}, {f.name: f for f in ext}


# ---------------------------------------------------------------------------
# Datasets + engine
# ---------------------------------------------------------------------------
@dataclass
class IDataset:
    name: str
    frames: dict
    start: pd.Timestamp
    end: pd.Timestamp | None


_DS = {}


def dataset(name: str, interval: str) -> IDataset:
    key = (name, interval)
    if key not in _DS:
        others = [s for s in core_data.load_universe() if s not in config.LAB_STOCKS]
        if interval == "1h":
            tr0, tr1, ho = config.INTRADAY_TRAIN_START, config.INTRADAY_TRAIN_END, config.INTRADAY_HOLDOUT_START
        else:
            tr0, tr1, ho = config.INTRADAY5_TRAIN_START, config.INTRADAY5_TRAIN_END, config.INTRADAY5_HOLDOUT_START
        syms, start, end = {
            "train": (config.LAB_STOCKS, tr0, tr1),
            "A": (config.LAB_STOCKS, ho, None),
            "B": (others, tr0, None),
            "B25": (others, ho, None),   # name kept for the CLI; = "other 90 from the holdout start"
        }[name]
        regime = market_context(lab_data.load(config.INDEX_SYMBOL))["regime"]
        frames = {}
        for s in syms:
            try:
                df = idd.load(s, interval, idd.LAB_DIR)
            except FileNotFoundError:
                continue
            df = df[df.index >= pd.Timestamp(start) - pd.Timedelta(days=45)]   # warm-up only
            if end:
                df = df[df.index < pd.Timestamp(end) + pd.Timedelta(days=1)]
            frames[s] = prepare(df, regime, BAR_MIN[interval])
        _DS[key] = IDataset(name, frames, pd.Timestamp(start), pd.Timestamp(end) if end else None)
    return _DS[key]


def run_frame(P, sym, efam, eparams, xfam, xparams, start, end, cost, control, rng):
    """Trades (and time-of-day matched control trades) for ONE prepared stock frame."""
    real, ctrl = [], []
    dirs = efam.fn(P, **eparams).to_numpy()
    sess = P["session"]
    win = (sess >= start) & ((sess <= end) if end is not None else True)
    ok = (win & P["signal_ok"] & P["prev_close"].notna()).to_numpy()
    bars = Bars(P)
    stops = {}
    for d in (1, -1):
        st, plan = xfam.fn(P, d, **xparams)
        stops[d] = (st.to_numpy(), plan)
    mins = P["mins"].to_numpy()
    sess_np = sess.to_numpy()
    traded = set()
    for i in np.flatnonzero((dirs != 0) & ok):
        if sess_np[i] in traded:
            continue
        d = int(dirs[i])
        stop, plan = stops[d]
        t = simulate(bars, int(i), float(stop[i]), 0.0, plan, direction=d, cost=cost, gap_filter=False)
        if t["status"] != "CLOSED":
            continue
        traded.add(sess_np[i])
        real.append({"symbol": sym, "date": sess_np[i], "mins": mins[i], "dir": d,
                     "ret": t["return_pct"], "reason": t["exit_reason"]})
        if control:
            cand = np.flatnonzero(ok & (mins == mins[i]))
            for k in rng.choice(cand, size=min(control, len(cand)), replace=False):
                tc = simulate(bars, int(k), float(stop[k]), 0.0, plan, direction=d, cost=cost, gap_filter=False)
                if tc["status"] == "CLOSED":
                    ctrl.append({"date": sess_np[k], "dir": d, "ret": tc["return_pct"]})
    _OR_CACHE.clear()
    return real, ctrl


def run(entry, exit_, ds: IDataset, interval: str, cost: float, control: int = 3, seed: int = 11):
    _, _, EBN, XBN = registry(interval)
    efam, xfam = EBN[entry[0]], XBN[exit_[0]]
    rng = np.random.default_rng(seed)
    real, ctrl = [], []
    for sym, P in ds.frames.items():
        r, c = run_frame(P, sym, efam, dict(entry[1]), xfam, exit_[1], ds.start, ds.end, cost, control, rng)
        real += r
        ctrl += c
    return pd.DataFrame(real), pd.DataFrame(ctrl)


def summarize(real: pd.DataFrame, ctrl: pd.DataFrame) -> dict:
    out = {"trades": len(real)}
    if real.empty:
        return out
    r = real["ret"]
    stress_extra = 2 * (config.INTRADAY_COST_STRESS - config.INTRADAY_COST_PER_SIDE) * 100
    longs, shorts = real["dir"] == 1, real["dir"] == -1
    out.update({
        "win%": float((r > 0).mean() * 100),
        "avg%": float(r.mean()),
        "avg%_stress": float(r.mean() - stress_extra),
        "trades/day": len(real) / max(real["date"].nunique(), 1),
        "long_n": int(longs.sum()), "long_avg%": float(r[longs].mean()) if longs.any() else np.nan,
        "short_n": int(shorts.sum()), "short_avg%": float(r[shorts].mean()) if shorts.any() else np.nan,
    })
    if len(ctrl):
        cm = ctrl["ret"].mean()
        daily = real.groupby("date")["ret"].mean() - cm
        t = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))) if len(daily) > 2 and daily.std() > 0 else 0.0
        out.update({"ctrl%": float(cm), "edge%": float(r.mean() - cm), "t": float(t),
                    "beat%": float((daily > 0).mean() * 100)})
    return out


def evaluate(entry, exit_, ds_name: str, interval: str = "1h", control: int = 3) -> dict:
    if interval == "5m" and ds_name in ("B", "B25"):   # too big to hold in memory - stream it
        return holdout_batch([(entry, exit_)], interval, [ds_name])[0][ds_name]
    ds = dataset(ds_name, interval)
    real, ctrl = run(entry, exit_, ds, interval, cost=config.INTRADAY_COST_PER_SIDE, control=control)
    return summarize(real, ctrl)


def holdout_batch(configs: list, interval: str, names=("A", "B", "B25"), control: int = 3, seed: int = 11,
                  progress: bool = False) -> list[dict]:
    """
    Evaluate many (entry, exit) configs on the holdouts, loading each stock ONCE and
    releasing it afterwards (memory-safe for 5-minute data). B25 is derived from the B
    run (same stocks, trades from the holdout start).
    """
    _, _, EBN, XBN = registry(interval)
    others = [s for s in core_data.load_universe() if s not in config.LAB_STOCKS]
    tr0, ho = ((config.INTRADAY_TRAIN_START, config.INTRADAY_HOLDOUT_START) if interval == "1h"
               else (config.INTRADAY5_TRAIN_START, config.INTRADAY5_HOLDOUT_START))
    regime = market_context(lab_data.load(config.INDEX_SYMBOL))["regime"]
    acc = [{n: ([], []) for n in ("A", "B")} for _ in configs]
    plan = []
    if "A" in names:
        plan.append(("A", config.LAB_STOCKS, pd.Timestamp(ho)))
    if "B" in names or "B25" in names:
        plan.append(("B", others, pd.Timestamp(tr0) if "B" in names else pd.Timestamp(ho)))
    for ds_name, syms, start in plan:
        for n, s in enumerate(syms, 1):
            try:
                df = idd.load(s, interval, idd.LAB_DIR)
            except FileNotFoundError:
                continue
            P = prepare(df[df.index >= start - pd.Timedelta(days=45)], regime, BAR_MIN[interval])
            del df
            for k, (entry, exit_) in enumerate(configs):
                rng = np.random.default_rng(seed + k)
                r, c = run_frame(P, s, EBN[entry[0]], dict(entry[1]), XBN[exit_[0]], exit_[1],
                                 start, None, config.INTRADAY_COST_PER_SIDE, control, rng)
                acc[k][ds_name][0].extend(r)
                acc[k][ds_name][1].extend(c)
            del P
            if progress and n % 10 == 0:
                print(f"    holdout {ds_name}: {n}/{len(syms)} stocks", flush=True)
    out = []
    for k in range(len(configs)):
        res = {}
        for ds_name in ("A", "B"):
            real, ctrl = pd.DataFrame(acc[k][ds_name][0]), pd.DataFrame(acc[k][ds_name][1])
            if ds_name in names:
                res[ds_name] = summarize(real, ctrl)
            if ds_name == "B" and "B25" in names:
                hr = pd.Timestamp(ho)
                res["B25"] = summarize(real[real["date"] >= hr] if len(real) else real,
                                       ctrl[ctrl["date"] >= hr] if len(ctrl) else ctrl)
        out.append(res)
    return out
