"""Market snapshot for assist.py (the live trading checklist), at time t, using only data known at t.

Served live by server.py at /api/assist (live data from live.py + one shared Angel One session), and called
in-process by assist.py replay with live_mode=False (history files only; no bid/ask, no account).
"""
from __future__ import annotations

import re

import pandas as pd

import data_source
import options

SEED_BARS = 60          # previous-session minutes sent along so EMAs are warm at 09:15
STRIKES_EACH_SIDE = 1   # ATM +- this many strikes, CE and PE
_ANGEL_SYM = re.compile(r"^NIFTY(\d{2})([A-Z]{3})(\d{2})(\d+)(CE|PE)$")


def angel_to_symbol(tradingsymbol: str) -> str | None:
    """'NIFTY19OCT2623050CE' -> 'NIFTY_20261019_23050CE' (None for anything that is not a Nifty option)."""
    m = _ANGEL_SYM.match(tradingsymbol or "")
    if not m:
        return None
    d = pd.to_datetime(f"{m[1]}{m[2]}{m[3]}", format="%d%b%y")
    return options.make(d, float(m[4]), m[5])


def held_symbols(acct: dict) -> list[str]:
    """Nifty options with an open quantity, from the fills (positions can lag) and the position book."""
    net: dict[str, int] = {}
    for x in acct.get("trades") or []:
        s = angel_to_symbol(x.get("tradingsymbol"))
        if s:
            q = int(float(x.get("fillsize") or 0))
            net[s] = net.get(s, 0) + (q if x.get("transactiontype") == "BUY" else -q)
    for p in acct.get("positions") or []:
        s = angel_to_symbol(p.get("tradingsymbol"))
        if s and int(float(p.get("netqty") or 0)) != 0:
            net[s] = net.get(s, 0) or 1
    return [s for s, q in net.items() if q != 0]


def _rows(df: pd.DataFrame, cols) -> list:
    return [[str(i)] + [round(float(v), 2) for v in r] for i, r in zip(df.index, df[cols].itertuples(index=False))]


def _completed(df: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    return df.iloc[: int(df.index.searchsorted(t - pd.Timedelta("1min"), side="right"))]


def nifty_bars(t: pd.Timestamp, spot: pd.DataFrame | None = None) -> pd.DataFrame:
    """Completed Nifty 1m bars of t's session, preceded by the last SEED_BARS of the previous session."""
    df = _completed(spot if spot is not None else data_source.load(options.SPOT, "1m"), t)
    day0 = int(df.index.searchsorted(t.normalize()))
    return df.iloc[max(0, day0 - SEED_BARS):]


def pick_expiry(t: pd.Timestamp, expiry: str | None = None) -> dict:
    """Nearest expiry, except on expiry day itself the next one (0-DTE premiums behave very differently);
    pass expiry=YYYY-MM-DD to override."""
    exps = options.expiries_after(t, 2)
    if expiry:
        hit = [e for e in exps if e["expiry"] == expiry]
        return {**(hit[0] if hit else {"expiry": expiry}), "same_day_skipped": False}
    if exps and pd.Timestamp(exps[0]["expiry"]) == t.normalize() and len(exps) > 1:
        return {**exps[1], "same_day_skipped": True}
    return {**exps[0], "same_day_skipped": False}


def snapshot(t, expiry: str | None = None, live_mode: bool = True, acct: dict | None = None) -> dict:
    """acct: trade book for replay/sim (live mode reads it from the shared Angel One session)."""
    import live
    t = pd.Timestamp(t)
    nb = nifty_bars(t)
    today = nb[nb.index >= t.normalize()]
    prev = nb[nb.index < t.normalize()]
    out = {"now": str(t), "market": bool(live.STATE.get("market")) if live_mode else True,
           "live_error": live.STATE.get("error") if live_mode else None,
           "nifty": _rows(nb, ["Open", "High", "Low", "Close"]),
           "prev_close": float(prev["Close"].iloc[-1]) if len(prev) else None, "prev_close_src": "last 1m bar",
           "vix": None, "expiry": None, "options": {}, "account": None}
    try:
        vx = _completed(data_source.load(options.VIX, "1m"), t)
        out["vix"] = (live.ltp(options.VIX, t) if live_mode else None) or (float(vx["Close"].iloc[-1]) if len(vx) else None)
    except KeyError:
        pass
    S = (live.ltp(options.SPOT, t) if live_mode else None) or (float(nb["Close"].iloc[-1]) if len(nb) else None)
    if S is None:
        return out
    e = pick_expiry(t, expiry)
    out["expiry"] = e
    atm = round(S / options.STEP) * options.STEP
    syms = [options.make(e["expiry"], atm + k * options.STEP, typ)
            for k in range(-STRIKES_EACH_SIDE, STRIKES_EACH_SIDE + 1) for typ in ("CE", "PE")]
    acct = live.account() if live_mode else acct
    if acct is not None:
        out["account"] = acct
        for s in held_symbols(acct):
            if s not in syms:
                syms.append(s)
    if live_mode:
        for s in syms:
            live.watch(s)
        q = live.depth([options.SPOT] + syms)
        if q.get(options.SPOT, {}).get("prev_close"):
            out["prev_close"], out["prev_close_src"] = float(q[options.SPOT]["prev_close"]), "exchange"
    else:
        q = {}
    for s in syms:
        exp_, K, typ = options.parse(s)
        raw = options.real_raw(s)
        bars = _completed(raw, t) if raw is not None else None
        bars = bars[bars.index >= t.normalize()] if bars is not None else None
        ltp = (live.ltp(s, t) if live_mode else None) or (float(bars["Close"].iloc[-1]) if bars is not None and len(bars) else None)
        out["options"][s] = {"type": typ, "strike": K, "expiry": str(exp_.date()), "ltp": ltp,
                             "bid": q.get(s, {}).get("bid"), "ask": q.get(s, {}).get("ask"),
                             "bars": _rows(bars, ["Open", "High", "Low", "Close", "Volume"]) if bars is not None else []}
    return out
