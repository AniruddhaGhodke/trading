"""Nifty options for the replay: expiry calendar, lot sizes, Black-Scholes, and option contracts as chart symbols.

A contract is a symbol `NIFTY_<YYYYMMDD expiry>_<strike><CE|PE>`, e.g. NIFTY_20261019_22600CE, and loads like any
other symbol (data_source.load), so replay, forming candles, indicators and drawings work on premiums unchanged.

Sources (per contract):
  REAL   - 1-minute candles + OI recorded from Angel One (SwingSystem/core/angel_options.py) - only exists for
           contracts recorded while they were listed (Angel One drops expired contracts).
  SYNTH  - Black-Scholes on the Nifty 1m/5m candles with India VIX as volatility. Approximate: no skew (OTM puts
           too cheap), VIX is a 30-day vol (expiry-day premiums off), no spread, no OI.

NO-LOOKAHEAD: a synthetic bar uses only the spot bar itself and the VIX close of the last VIX bar that closed by
the price's own time (bar open for Open, bar close for High/Low/Close).
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

import numpy as np
import pandas as pd

import data_source as ds

SPOT, VIX = "IDX_NSEI", "IDX_INDIAVIX"
OPT_DIR = ds._ANGEL / "options"
R, Q = 0.065, 0.012          # risk-free rate, Nifty dividend yield (annual, continuous)
STEP = 50                    # strike interval
LIFE_DAYS = 63               # synthetic contracts start this long before expiry
EXPIRY_TIME = pd.Timedelta(hours=15, minutes=30)
SESSION = (pd.Timedelta(hours=9, minutes=15), pd.Timedelta(hours=15, minutes=29))   # 1m bar opens kept
_SYM = re.compile(r"^NIFTY_(\d{8})_(\d+)(CE|PE)$")

# Calendar (NSE): Nifty weekly options launched 11-Feb-2019 (Thursday expiry; monthly-only before). From
# 1-Sep-2025 weekly and monthly expiries are on Tuesday (NSE/FAOP/68747). A holiday moves expiry to the previous
# trading day. Real expiries from the recorded contract list override the computed ones.
WEEKLY_FROM = pd.Timestamp("2019-02-11")
TUESDAY_FROM = pd.Timestamp("2025-09-01")
# Lot size by contract expiry (NSE circulars). Change-over months are approximate: during a change old contracts
# kept their old lot until expiry. Recorded contracts use their real lot size.
LOTS = [(pd.Timestamp("2021-07-29"), 75), (pd.Timestamp("2024-05-02"), 50), (pd.Timestamp("2025-01-01"), 25),
        (pd.Timestamp("2026-01-06"), 75)]
LOT_NOW = 65


# ---------------------------------------------------------------- symbols
def parse(symbol: str):
    """(expiry Timestamp, strike, 'CE'|'PE') for an option symbol, else None."""
    m = _SYM.match(symbol or "")
    return (pd.Timestamp(m.group(1)), float(m.group(2)), m.group(3)) if m else None


def make(expiry, strike: float, typ: str) -> str:
    return f"NIFTY_{pd.Timestamp(expiry):%Y%m%d}_{int(strike)}{typ}"


def real_path(expiry, strike: float, typ: str):
    return OPT_DIR / "1m" / "NIFTY" / f"{pd.Timestamp(expiry).date()}" / f"{int(strike)}{typ}.csv"


# ---------------------------------------------------------------- Black-Scholes (on the forward)
_erf = np.vectorize(math.erf, otypes=[float])


def _ncdf(x):
    return 0.5 * (1.0 + _erf(np.asarray(x, float) / math.sqrt(2.0)))


def _npdf(x):
    return np.exp(-0.5 * np.asarray(x, float) ** 2) / math.sqrt(2 * math.pi)


def _d(S, K, T, sig):
    sd = sig * np.sqrt(T)
    d1 = (np.log(S / K) + (R - Q) * T + 0.5 * sd * sd) / sd
    return d1, d1 - sd


def years(t, expiry) -> np.ndarray:
    """Time to expiry (15:30 on the expiry day) in years, calendar time."""
    t = pd.DatetimeIndex(np.atleast_1d(pd.to_datetime(t)))
    return ((pd.Timestamp(expiry) + EXPIRY_TIME - t).total_seconds().to_numpy() / (365 * 86400.0))


def price(S, K, T, sig, typ: str):
    """Black-Scholes price; at or after expiry (T <= 0) the intrinsic value."""
    S, K, T, sig = np.broadcast_arrays(*(np.asarray(a, float) for a in (S, K, T, sig)))
    intr = np.maximum(S - K, 0) if typ == "CE" else np.maximum(K - S, 0)
    Tc = np.maximum(T, 1 / (365 * 1440))           # 1-minute floor keeps the formula finite on expiry day
    d1, d2 = _d(S, K, Tc, sig)
    dq, dr = np.exp(-Q * Tc), np.exp(-R * Tc)
    v = S * dq * _ncdf(d1) - K * dr * _ncdf(d2) if typ == "CE" else K * dr * _ncdf(-d2) - S * dq * _ncdf(-d1)
    return np.where(T <= 0, intr, np.maximum(v, 0.0))


def greeks(S, K, T, sig, typ: str) -> dict:
    """delta, gamma (per point), theta (₹ per calendar day), vega (₹ per 1 volatility point)."""
    S, K, T, sig = np.broadcast_arrays(*(np.asarray(a, float) for a in (S, K, T, sig)))
    Tc = np.maximum(T, 1 / (365 * 1440))
    d1, d2 = _d(S, K, Tc, sig)
    dq, dr, pdf = np.exp(-Q * Tc), np.exp(-R * Tc), _npdf(d1)
    common = -S * dq * pdf * sig / (2 * np.sqrt(Tc))
    if typ == "CE":
        delta = dq * _ncdf(d1)
        theta = common - R * K * dr * _ncdf(d2) + Q * S * dq * _ncdf(d1)
    else:
        delta = dq * (_ncdf(d1) - 1)
        theta = common + R * K * dr * _ncdf(-d2) - Q * S * dq * _ncdf(-d1)
    return {"delta": delta, "gamma": dq * pdf / (S * sig * np.sqrt(Tc)), "theta": theta / 365,
            "vega": S * dq * pdf * np.sqrt(Tc) / 100}


def implied_vol(p, S, K, T, typ: str):
    """Volatility that reproduces premium p (bisection, 1%..300%); NaN if p is outside that range."""
    p, S, K, T = np.broadcast_arrays(*(np.asarray(a, float) for a in (p, S, K, T)))
    lo, hi = np.full(p.shape, 0.01), np.full(p.shape, 3.0)
    for _ in range(60):
        mid = (lo + hi) / 2
        above = price(S, K, T, mid, typ) > p
        hi, lo = np.where(above, mid, hi), np.where(above, lo, mid)
    iv = (lo + hi) / 2
    ok = (price(S, K, T, 0.01, typ) <= p) & (p <= price(S, K, T, 3.0, typ)) & (T > 0)
    return np.where(ok, iv, np.nan)


def tick(x):
    """Round to the 0.05 option tick (minimum 0.05)."""
    return np.maximum(np.round(np.asarray(x, float) * 20) / 20, 0.05)


# ---------------------------------------------------------------- calendar and lot size
def _contracts() -> pd.DataFrame:
    p = OPT_DIR / "contracts.csv"
    if not p.is_file():
        return pd.DataFrame(columns=["token", "symbol", "expiry", "strike", "type", "lot"])
    return pd.read_csv(p, dtype={"token": str}, parse_dates=["expiry"])


@lru_cache(maxsize=1)
def _trading_days() -> pd.DatetimeIndex:
    return pd.DatetimeIndex(ds.load(SPOT, "5m").index.normalize().unique())


def expiry_calendar() -> pd.DataFrame:
    """All Nifty expiries: index = expiry date, columns monthly (bool), real (bool: recorded contracts), lot."""
    return _calendar()


@lru_cache(maxsize=1)
def _calendar() -> pd.DataFrame:
    days = _trading_days()
    first, last = days[0], days[-1]
    out = {}
    for d in pd.date_range(first, last + pd.Timedelta(days=70)):
        if d.weekday() != (1 if d >= TUESDAY_FROM else 3):
            continue
        monthly = (d + pd.Timedelta(days=7)).month != d.month
        if d < WEEKLY_FROM and not monthly:
            continue
        e = d
        if e <= last:
            while e not in days and e > first:     # holiday -> previous trading day
                e -= pd.Timedelta(days=1)
        out[e] = {"monthly": monthly, "real": False, "lot": lot_size(e)}
    c = _contracts()
    if len(c):
        for e, g in c.groupby("expiry"):
            wk = e.isocalendar()[:2]
            for k in [k for k in out if k.isocalendar()[:2] == wk]:
                monthly = out.pop(k)["monthly"]
                break
            else:
                monthly = (e + pd.Timedelta(days=7)).month != e.month
            out[e] = {"monthly": monthly, "real": True, "lot": int(g["lot"].iloc[0])}
    return pd.DataFrame.from_dict(out, orient="index").sort_index()


def lot_size(expiry) -> int:
    e = pd.Timestamp(expiry)
    return next((lot for until, lot in LOTS if e < until), LOT_NOW)


def expiries_after(t, n: int = 4) -> list[dict]:
    """The next n expiries still trading at time t."""
    t = pd.Timestamp(t)
    cal = expiry_calendar()
    nxt = cal[cal.index + EXPIRY_TIME > t].head(n)
    return [{"expiry": str(e.date()), "monthly": bool(r.monthly), "real": bool(r.real), "lot": int(r.lot),
             "days": round((e + EXPIRY_TIME - t).total_seconds() / 86400, 2)} for e, r in nxt.iterrows()]


# ---------------------------------------------------------------- price at a time (no lookahead)
def _closes_by_end(symbol: str) -> pd.Series:
    """Close of each bar indexed by the bar's END time - .asof(t) = last close known at t. 1m where it exists."""
    try:
        df = ds.load(symbol, "1m")
        step = pd.Timedelta("1min")
    except KeyError:
        df = ds.load(symbol, "5m")
        step = pd.Timedelta("5min")
    s = df["Close"].copy()
    s.index = df.index + step
    return s


def _asof(s: pd.Series, t) -> np.ndarray:
    """Values of s at times t (last value with index <= t); NaN before the first."""
    t = pd.DatetimeIndex(np.atleast_1d(pd.to_datetime(t)))
    pos = s.index.searchsorted(t, side="right") - 1
    v = s.to_numpy()[np.maximum(pos, 0)].astype(float)
    return np.where(pos >= 0, v, np.nan)


def vix_at(t) -> np.ndarray:
    try:
        s = _closes_by_end(VIX)
    except KeyError:
        raise KeyError("no India VIX data - run: cd SwingSystem && python -c \"from core.angel_data import backfill; "
                       "backfill('1m', symbols=['^INDIAVIX'])\"")
    return _asof(s, t)


def spot_at(t) -> np.ndarray:
    return _asof(_closes_by_end(SPOT), t)


# ---------------------------------------------------------------- contract bars
def _synth(bars: pd.DataFrame, step: pd.Timedelta, K: float, typ: str, expiry) -> pd.DataFrame:
    """Premium OHLC from spot OHLC. Premium is monotonic in spot within a bar, so High/Low come from the spot
    High/Low (swapped for puts), priced at the bar's close time."""
    t0, t1 = bars.index, bars.index + step
    sig0, sig1 = vix_at(t0) / 100, vix_at(t1) / 100
    T0, T1 = years(t0, expiry), years(t1, expiry)
    o = price(bars["Open"], K, T0, sig0, typ)
    c = price(bars["Close"], K, T1, sig1, typ)
    a = price(bars["High"], K, T1, sig1, typ)
    b = price(bars["Low"], K, T1, sig1, typ)
    hi, lo = (a, b) if typ == "CE" else (b, a)
    out = pd.DataFrame({"Open": tick(o), "High": tick(np.maximum.reduce([hi, o, c])),
                        "Low": tick(np.minimum.reduce([lo, o, c])), "Close": tick(c), "Volume": 0.0}, index=bars.index)
    return out[np.isfinite(sig0) & np.isfinite(sig1)]


def _in_session(idx: pd.DatetimeIndex) -> np.ndarray:
    tod = idx - idx.normalize()
    return (tod >= SESSION[0]) & (tod <= SESSION[1])


@lru_cache(maxsize=64)
def _real_file(path_str: str) -> pd.DataFrame:
    df = pd.read_csv(path_str, index_col=0, parse_dates=True)
    df.index.name = "Datetime"
    return df


@lru_cache(maxsize=256)
def real_raw(symbol: str) -> pd.DataFrame | None:
    """Recorded 1m candles (+OI) of a contract incl. today's live bars, session-clipped; None if never recorded."""
    import live
    e, K, typ = parse(symbol)
    p = real_path(e, K, typ)
    parts = [_real_file(str(p))] if p.is_file() else []
    ov = live.overlay(symbol)
    if ov is not None and len(ov):
        parts.append(ov)
    if not parts:
        return None
    df = pd.concat(parts)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[_in_session(df.index) & (df.index < e + EXPIRY_TIME)]
    if "OI" not in df:
        df["OI"] = np.nan
    return df


def source(symbol: str) -> str:
    return "REAL" if real_raw(symbol) is not None else "SYNTH"


def load(symbol: str, res: str) -> pd.DataFrame:
    """OHLCV bars of an option contract at res '1m' or '5m' (base resolutions of data_source)."""
    e, K, typ = parse(symbol)
    end = e + EXPIRY_TIME
    real = real_raw(symbol)
    if real is not None:
        # Fill minutes without trades (flat bar at the last price, volume 0) on the Nifty minute grid.
        grid = ds.load(SPOT, "1m").index
        grid = grid[(grid >= real.index[0]) & (grid < end)].union(real.index)
        df = real.reindex(grid)
        df["Close"] = df["Close"].ffill()
        for c in ("Open", "High", "Low"):
            df[c] = df[c].fillna(df["Close"])
        df["Volume"] = df["Volume"].fillna(0.0)
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        return df if res == "1m" else _five(df)
    start = e - pd.Timedelta(days=LIFE_DAYS)

    def spot(r):
        d = ds.load(SPOT, r)
        return d[(d.index >= start) & (d.index < end)]

    s1 = _synth(spot("1m"), pd.Timedelta("1min"), K, typ, e) if ds.has_1m(SPOT) else None
    if res == "1m":
        if s1 is None:
            raise KeyError(f"no 1-minute data for {symbol!r}")
        return s1
    s5 = _synth(spot("5m"), pd.Timedelta("5min"), K, typ, e)
    if s1 is not None and len(s1):
        # Where the 1m spot covers a whole 5m bar, build that bar from the 1m premiums so the forming candle
        # (1m ticks) ends exactly at the completed bar.
        f = _five(s1, need=5)
        s5.loc[f.index.intersection(s5.index)] = f.loc[f.index.intersection(s5.index)]
    return s5


def _five(df1: pd.DataFrame, need: int = 1) -> pd.DataFrame:
    """1m -> 5m bars anchored at 09:15; with need=5 only complete bins are returned."""
    g = df1.resample("5min", origin="start_day", offset="9h15min")
    out = g.agg(ds._AGG)
    out["_n"] = g["Close"].count()
    out = out[out["_n"] >= need].drop(columns="_n")
    return out.dropna(subset=["Open"])


# ---------------------------------------------------------------- chain
def chain(t, expiry, n: int = 10) -> dict:
    """Option chain at time t (only data known at t): spot, VIX, and for strikes ATM +- n: model premium and
    greeks (VIX as vol) and, for recorded contracts, the last traded price, its time, OI and implied vol."""
    import live
    t, e = pd.Timestamp(t), pd.Timestamp(expiry)
    S = live.ltp(SPOT, t) or float(spot_at(t)[0])
    if not np.isfinite(S):
        raise ValueError(f"no Nifty price at {t}")
    try:
        sig = float(vix_at(t)[0]) / 100
    except KeyError:
        sig = float("nan")              # no VIX yet: real contracts still show (with their own IV), model blank
    T = float(years(t, e)[0])
    fin = lambda v, d: round(float(v), d) if np.isfinite(v) else None
    atm = round(S / STEP) * STEP
    strikes = np.arange(atm - n * STEP, atm + (n + 1) * STEP, STEP, dtype=float)
    rows = []
    for typ in ("CE", "PE"):
        model = price(S, strikes, T, sig, typ)
        g = greeks(S, strikes, T, sig, typ)
        for k, K in enumerate(strikes):
            sym = make(e, K, typ)
            r = {"symbol": sym, "type": typ, "strike": K, "model": fin(model[k], 2),
                 "delta": fin(g["delta"][k], 3), "theta": fin(g["theta"][k], 2),
                 "gamma": fin(g["gamma"][k], 5), "vega": fin(g["vega"][k], 2),
                 "ltp": None, "iv": None, "oi": None, "last_trade": None, "source": "SYNTH"}
            raw = real_raw(sym)
            if raw is not None:
                done = raw[raw.index + pd.Timedelta("1min") <= t]
                if len(done):
                    last = done.iloc[-1]
                    traded = done[done["Volume"] > 0]
                    iv = float(implied_vol(last["Close"], S, K, T, typ))
                    oi = done["OI"].ffill().iloc[-1]
                    q = live.ltp(sym, t)
                    if q is not None:
                        last = last.copy()
                        last["Close"] = q
                        iv = float(implied_vol(q, S, K, T, typ))
                    r.update(source="REAL", ltp=float(last["Close"]),
                             last_trade=int(traded.index[-1].value // 10**9) if len(traded) else None,
                             oi=None if pd.isna(oi) else float(oi),
                             iv=None if not np.isfinite(iv) else round(iv * 100, 2))
                    if np.isfinite(iv):
                        g2 = greeks(S, K, T, iv, typ)
                        r.update({k2: round(float(v), 5 if k2 == "gamma" else 3 if k2 == "delta" else 2)
                                  for k2, v in g2.items()})
            rows.append(r)
    cal = expiry_calendar()
    lot = int(cal.loc[e, "lot"]) if e in cal.index else lot_size(e)
    return {"time": int(t.value // 10**9), "spot": round(S, 2), "vix": fin(sig * 100, 2), "atm": atm,
            "expiry": str(e.date()), "days": round(T * 365, 3), "lot": lot, "rows": rows}
