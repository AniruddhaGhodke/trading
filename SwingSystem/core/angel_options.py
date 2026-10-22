"""
Nifty option recorder - Angel One SmartAPI, DATA ONLY.

Angel One serves history only for contracts that are still listed: once a contract expires its token
disappears from the scrip master and its candles can no longer be fetched. So this records, every day,
1-minute candles + 1-minute open interest for the nearest NIFTY weekly/monthly expiries, from each
contract's listing (first run) or its last stored bar (later runs).

Layout (data/angel/options/):
  contracts.csv                              - every contract ever seen (token, expiry, strike, type, lot size)
  1m/NIFTY/<YYYY-MM-DD expiry>/<strike><CE|PE>.csv
        Datetime, Open, High, Low, Close, Volume, OI     (raw: includes the 15:30 bar / post-close trades;
                                                          readers clip to the session)
Run:  python -c "from core.angel_options import record; record()"
"""

import json
import time
from datetime import datetime, timedelta

import pandas as pd

from core.angel_data import ANGEL_DIR, INTERVALS, Angel

OPT_DIR = ANGEL_DIR / "options"
STEP = 50                     # Nifty strike interval
LISTING_DAYS = 100            # first run: fetch from (expiry - 100 days); empty chunks cost one request each


def contracts(master: list | None = None) -> pd.DataFrame:
    """Listed NIFTY index options from the scrip master: token, symbol, expiry, strike, type, lot."""
    if master is None:
        master = json.loads((ANGEL_DIR / "scrip_master.json").read_text())
    rows = [{"token": x["token"], "symbol": x["symbol"], "expiry": pd.to_datetime(x["expiry"], format="%d%b%Y"),
             "strike": float(x["strike"]) / 100, "type": x["symbol"][-2:], "lot": int(x["lotsize"])}
            for x in master if x.get("exch_seg") == "NFO" and x.get("name") == "NIFTY"
            and x.get("instrumenttype") == "OPTIDX"]
    return pd.DataFrame(rows)


def path_for(expiry, strike: float, typ: str):
    return OPT_DIR / "1m" / "NIFTY" / f"{pd.Timestamp(expiry).date()}" / f"{int(strike)}{typ}.csv"


def _save_contracts(c: pd.DataFrame):
    """Merge today's listing into contracts.csv - the only record of a contract once it has expired."""
    OPT_DIR.mkdir(parents=True, exist_ok=True)
    p = OPT_DIR / "contracts.csv"
    old = pd.read_csv(p, dtype={"token": str}, parse_dates=["expiry"]) if p.exists() else None
    out = pd.concat([old, c]) if old is not None else c
    # Keyed by symbol (expiry + strike + type): exchange tokens of expired contracts can be reused.
    out = out.drop_duplicates("symbol", keep="last").sort_values(["expiry", "strike", "type"])
    out.to_csv(p, index=False, date_format="%Y-%m-%d")


def _fetch(a: Angel, token: str, start: datetime, end: datetime) -> pd.DataFrame:
    """1m candles + OI for one contract, chunked like Angel.candles (20 days per request)."""
    step = timedelta(days=INTERVALS["1m"][1])
    rows, ois, cur = [], [], start
    while cur < end:
        nxt = min(cur + step, end)
        got = a._candles_once(token, "1m", cur, nxt, exchange="NFO")
        rows += got
        if got:     # no trades in the chunk -> no OI worth asking for
            ois += a._candles_once(token, "1m", cur, nxt, exchange="NFO", oi=True)
        cur = nxt + timedelta(minutes=1)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df.pop("ts")).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df = df[~df.index.duplicated()].astype(float)
    if ois:
        oi = pd.DataFrame(ois)
        oi.index = pd.to_datetime(oi.pop("time")).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        df["OI"] = oi[~oi.index.duplicated()]["oi"].reindex(df.index)
    else:
        df["OI"] = float("nan")
    return df.sort_index()


def record(n_expiries: int = 3, width: int = 40, quiet: bool = False):
    """Record the nearest `n_expiries` expiries, strikes within +-`width` steps of spot, CE and PE.

    Resumable and incremental: each contract continues from its last stored bar. A failing contract is
    logged and skipped, never aborting the run.
    """
    a = Angel()           # also refreshes scrip_master.json for today
    allc = contracts()
    _save_contracts(allc)
    now = datetime.now()
    # After the close on expiry day the scrip master no longer lists that expiry, but its tokens still serve
    # today's candles: add it back from contracts.csv so the expiry session is recorded.
    saved = pd.read_csv(OPT_DIR / "contracts.csv", dtype={"token": str}, parse_dates=["expiry"])
    allc = pd.concat([allc, saved[saved["expiry"] == pd.Timestamp(now).normalize()]]).drop_duplicates("symbol")
    q = a.quotes(["^NSEI"])
    if len(q):
        spot = float(q["ltp"].iloc[0])
    else:
        spot = float(pd.read_csv(ANGEL_DIR / "1m" / "IDX_NSEI.csv").iloc[-1]["Close"])
    exps = sorted(e for e in allc["expiry"].unique() if e + timedelta(hours=15, minutes=30) >= pd.Timestamp(now).normalize())
    exps = exps[:n_expiries]
    pick = allc[allc["expiry"].isin(exps) & ((allc["strike"] - spot).abs() <= width * STEP)]
    pick = pick.sort_values(["expiry", "strike", "type"])
    if not quiet:
        print(f"Nifty options: spot {spot:.0f}, expiries {[str(e.date()) for e in exps]}, {len(pick)} contracts")
    t0, n_new, errors = time.time(), 0, 0
    for k, c in enumerate(pick.itertuples(), 1):
        p = path_for(c.expiry, c.strike, c.type)
        old = pd.read_csv(p, index_col=0, parse_dates=True) if p.exists() else None
        frm = (old.index[-1] + timedelta(minutes=1)) if old is not None and len(old) \
            else (c.expiry - timedelta(days=LISTING_DAYS)).to_pydatetime().replace(hour=9, minute=15)
        try:
            new = _fetch(a, c.token, frm, min(now, c.expiry.to_pydatetime().replace(hour=23, minute=59)))
        except Exception as e:
            errors += 1
            print(f"  {c.symbol}: ERROR {str(e)[:80]}", flush=True)
            continue
        if new.empty and old is None:
            continue
        df = pd.concat([old, new]) if old is not None else new
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df.index.name = "Datetime"
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p)
        n_new += len(new)
        if not quiet and k % 25 == 0:
            print(f"  [{k}/{len(pick)}] {c.symbol} ... {time.time() - t0:.0f}s", flush=True)
    print(f"Nifty options: +{n_new} bars over {len(pick)} contracts ({errors} errors) in {time.time() - t0:.0f}s")
