"""
Intraday data: download, clean, accumulate, freeze.

yfinance limits: 5m/15m ~60 days, 1h ~730 days. The collector (`collect`) appends
each run's bars to data/intraday/<interval>/<symbol>.csv and never overwrites
existing timestamps, so 5-minute history grows every day it runs.

Cleaning:
  - timestamps converted to IST, tz-naive
  - only weekday bars starting 09:15 <= t < 15:30 (drops Diwali Muhurat evening sessions)
  - sessions with an abnormal number of bars (half days, data gaps) are dropped
"""

import json
import shutil
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

import config
from core import data as core_data

INTRADAY_DIR = config.DATA_DIR / "intraday"
PERIODS = {"5m": "60d", "15m": "60d", "1h": "730d"}


def _path(root, interval: str, symbol: str):
    return root / interval / f"{symbol.replace('^', 'IDX_')}.csv"


def clean(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    t = df.index
    mins = t.hour * 60 + t.minute
    df = df[(t.dayofweek < 5) & (mins >= 9 * 60 + 15) & (mins < 15 * 60 + 30)]
    counts = df.groupby(df.index.date).size()
    if len(counts):
        normal = counts.mode().iloc[0]
        good = counts[counts >= normal * 0.9].index
        df = df[pd.Index(df.index.date).isin(good)]
    return df


def download(symbols: list[str], interval: str) -> dict[str, pd.DataFrame]:
    raw = yf.download(symbols, period=PERIODS[interval], interval=interval, auto_adjust=True,
                      group_by="ticker", progress=False, threads=True)
    out = {}
    for s in symbols:
        try:
            df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        idx = df.index.tz_convert("Asia/Kolkata") if df.index.tz is not None else df.index
        df.index = idx.tz_localize(None)
        df = clean(df, interval)
        if len(df):
            out[s] = df
    return out


def collect(intervals=("5m", "1h"), verbose=True):
    """Append newly available bars to the local intraday store (never overwrites)."""
    symbols = core_data.load_universe() + [config.INDEX_SYMBOL]
    for iv in intervals:
        fresh = download(symbols, iv)
        added = 0
        for s, df in fresh.items():
            p = _path(INTRADAY_DIR, iv, s)
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                old = pd.read_csv(p, index_col=0, parse_dates=True)
                new = df[~df.index.isin(old.index)]
                # only append completed sessions newer than what we have
                new = new[new.index > old.index[-1]]
                merged = pd.concat([old, new])
                added += len(new)
            else:
                merged, added = df, added + len(df)
            merged.to_csv(p)
        if verbose:
            days = None
            ref = _path(INTRADAY_DIR, iv, "RELIANCE.NS")
            if ref.exists():
                days = pd.read_csv(ref, index_col=0, parse_dates=True).index.normalize().nunique()
            print(f"Intraday {iv}: {len(fresh)} symbols, {added} new bars stored"
                  + (f" | history now {days} sessions" if days else ""))


def load(symbol: str, interval: str, root=None) -> pd.DataFrame:
    return pd.read_csv(_path(root or INTRADAY_DIR, interval, symbol), index_col=0, parse_dates=True)


# ---------------------------------------------------------------------------
# Frozen snapshot for the intraday lab
# ---------------------------------------------------------------------------
LAB_DIR = config.DATA_DIR / "lab_intraday"


def freeze(intervals=("1h", "5m")):
    collect(intervals)
    if LAB_DIR.exists():
        shutil.rmtree(LAB_DIR)
    info = {}
    for iv in intervals:
        (LAB_DIR / iv).mkdir(parents=True)
        for src in (INTRADAY_DIR / iv).glob("*.csv"):
            intra = pd.read_csv(src, index_col=0, parse_dates=True)
            intra = core_data.fix_corporate_jumps(intra, intra.index.normalize())
            intra.to_csv(LAB_DIR / iv / src.name)
        ref = load("RELIANCE.NS", iv, LAB_DIR)
        info[iv] = {"first": str(ref.index[0]), "last": str(ref.index[-1]),
                    "sessions": int(ref.index.normalize().nunique()),
                    "symbols": len(list((LAB_DIR / iv).glob("*.csv")))}
    (LAB_DIR / "manifest.json").write_text(json.dumps(
        {"frozen_at": datetime.now().isoformat(timespec="seconds"), **info}, indent=1))
    print(f"Froze intraday snapshot: {info}")


def fix_with_daily_reference(intra: pd.DataFrame, daily: pd.DataFrame, tol: float = 0.03) -> pd.DataFrame:
    """
    Broker candles are raw. Compare each day's close-to-close change with the ADJUSTED daily
    change (Yahoo): if they disagree by more than `tol`, the difference is a corporate action
    (split, bonus, demerger, big dividend) - rescale all earlier bars by that ratio. Catches
    e.g. the POWERGRID 1:3 bonus (-25%, below the 30% rule) while leaving real crashes alone.
    Closes are compared (not opens): Yahoo's daily Open is unreliable on volatile days.
    """
    sess = pd.Index(intra.index.normalize())
    last_close = intra["Close"].groupby(sess).last()
    raw_ret = last_close / last_close.shift(1)
    # align the reference to the SAME session calendar first (Yahoo and the broker can differ by
    # a day - e.g. the Saturday Budget session - which would otherwise look like a corporate action)
    refc = daily["Close"].reindex(last_close.index)
    ref_ret = refc / refc.shift(1)
    ratio = (raw_ret / ref_ret).dropna()
    jumps = ratio[(np.log(ratio)).abs() > np.log(1 + tol)]
    out = intra.copy()
    for day, r in jumps.items():
        before = sess < day
        for col in ("Open", "High", "Low", "Close"):
            out.loc[before, col] = out.loc[before, col] * r
        out.loc[before, "Volume"] = out.loc[before, "Volume"] / r
    out.attrs["corp_actions"] = {str(k.date()): round(float(v), 4) for k, v in jumps.items()}
    return out


def freeze_angel(interval: str = "5m"):
    """
    Snapshot Angel One history (data/angel/<interval>) into data/lab_intraday/<interval>,
    cleaned (session hours, abnormal days) and corrected for unadjusted corporate actions.
    Broker candles are raw prices, so fix_corporate_jumps is essential here.
    """
    src_dir = config.DATA_DIR / "angel" / interval
    files = sorted(src_dir.glob("*.csv"))
    if not files:
        raise SystemExit(f"No Angel One data in {src_dir}. Run the backfill first.")
    dst = LAB_DIR / interval
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    actions = {}
    for f in files:
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        df = clean(df, interval)
        ref = config.CACHE_DIR / f.name            # Yahoo adjusted daily, same file naming
        if ref.exists():
            df = fix_with_daily_reference(df, pd.read_csv(ref, index_col=0, parse_dates=True))
            if df.attrs.get("corp_actions"):
                actions[f.stem] = df.attrs["corp_actions"]
        df = core_data.fix_corporate_jumps(df, df.index.normalize())
        df.to_csv(dst / f.name)
    (LAB_DIR / f"corporate_actions_{interval}.json").write_text(json.dumps(actions, indent=1))
    print(f"Adjusted {sum(len(v) for v in actions.values())} corporate actions/large dividends in {len(actions)} stocks")
    ref = load("RELIANCE.NS", interval, LAB_DIR) if (dst / "RELIANCE.NS.csv").exists() else None
    info = {"source": "angel", "symbols": len(files),
            "first": str(ref.index[0]) if ref is not None else None,
            "last": str(ref.index[-1]) if ref is not None else None,
            "sessions": int(ref.index.normalize().nunique()) if ref is not None else None}
    (LAB_DIR / f"manifest_{interval}.json").write_text(json.dumps(
        {"frozen_at": datetime.now().isoformat(timespec="seconds"), **info}, indent=1))
    print(f"Froze Angel One {interval}: {info}")
