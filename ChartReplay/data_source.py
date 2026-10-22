"""Candle data for the replay tool.

Sources (first match wins):
  5-minute (base for every interval >= 5m):
    1. ChartReplay/csv/<SYMBOL>.csv          - manual imports (any OHLC CSV, see _normalize)
    2. SwingSystem/data/angel/5m/<SYMBOL>.csv - Angel One SmartAPI 5-minute backfill (2018+)
  1-minute (optional, per symbol - used for the 1m chart and as "ticks" for forming candles):
    1. ChartReplay/csv/1m/<SYMBOL>.csv
    2. SwingSystem/data/angel/1m/<SYMBOL>.csv
  A symbol with only 1-minute data gets its 5-minute base resampled from it.

All times are naive IST wall-clock. Bars are resampled on request (`interval`), so
timeframe switching is a UI change only.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
_ANGEL = ROOT.parent / "SwingSystem" / "data" / "angel"
SOURCES = [ROOT / "csv", _ANGEL / "5m"]
SOURCES_1M = [ROOT / "csv" / "1m", _ANGEL / "1m"]

# pandas resample rules. Intraday bins anchor at 09:15 (NSE open) so 15m/1h bars line up with the session.
INTERVALS = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min", "1D": "1D"}
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1D": 1440}
TICKS = ["auto", "1m", "5m", "15m", "30m", "1h"]   # sub-bar sizes for forming candles; auto = finest available
_COLS = ["Open", "High", "Low", "Close", "Volume"]
_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}


def _find(dirs: list[Path], symbol: str) -> Path | None:
    return next((d / f"{symbol}.csv" for d in dirs if (d / f"{symbol}.csv").is_file()), None)


def list_symbols() -> list[str]:
    names = set()
    for d in SOURCES + SOURCES_1M:
        if d.is_dir():
            names.update(p.stem for p in d.glob("*.csv"))
    return sorted(names)


def has_1m(symbol: str) -> bool:
    import options
    if options.parse(symbol):
        return _find(SOURCES_1M, options.SPOT) is not None or options.real_raw(symbol) is not None
    return _find(SOURCES_1M, symbol) is not None


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Accept common CSV layouts: Datetime/Date/Time/timestamp + open/high/low/close[/volume], any case."""
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})
    tcol = next((c for c in ("datetime", "timestamp", "date", "time") if c in df.columns), None)
    if tcol is None:
        raise ValueError(f"no datetime column in {list(df.columns)}")
    if tcol == "date" and "time" in df.columns:
        idx = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
    else:
        idx = pd.to_datetime(df[tcol])
    if getattr(idx.dt, "tz", None) is not None:  # tz-aware -> IST wall-clock
        idx = idx.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    out = pd.DataFrame({c: pd.to_numeric(df[c.lower()], errors="coerce") for c in _COLS if c.lower() in df.columns})
    if "Volume" not in out:
        out["Volume"] = 0.0
    out.index = pd.DatetimeIndex(idx, name="Datetime")
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    return out[~out.index.duplicated(keep="last")].sort_index()


def _step(df: pd.DataFrame) -> pd.Timedelta:
    return df.index.to_series().diff().median() if len(df) > 1 else pd.Timedelta("5min")


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """OHLCV resample + `End` (last source bar's start + source step). Intraday bins anchor at 09:15."""
    step = _step(df)
    src = df[_COLS].assign(_t=df.index)
    agg = {**_AGG, "_t": "last"}
    if rule == "1D":
        out = src.resample("1D").agg(agg)
    else:
        out = src.resample(rule, origin="start_day", offset="9h15min").agg(agg)
    out = out.dropna(subset=["Open"])
    out["End"] = pd.to_datetime(out.pop("_t")) + step
    return out


@lru_cache(maxsize=16)
def _read(path: str) -> pd.DataFrame:
    """Parsed CSV file (history). Today's live bars are added on top by load(), so this is never re-read."""
    return _normalize(pd.read_csv(path))


def _with_live(symbol: str, df: pd.DataFrame) -> pd.DataFrame:
    """Append today's completed live 1m bars (Nifty / VIX, while live.py is polling)."""
    import live
    ov = live.overlay(symbol)
    if ov is None or not len(ov):
        return df
    df = pd.concat([df, ov[_COLS]])
    return df[~df.index.duplicated(keep="last")].sort_index()


@lru_cache(maxsize=16)
def load(symbol: str, res: str = "5m") -> pd.DataFrame:
    """Raw base bars: res="5m" (base for all intervals >= 5m) or res="1m"."""
    import options
    if options.parse(symbol):
        return options.load(symbol, res)
    if res == "1m":
        p = _find(SOURCES_1M, symbol)
        if p is None:
            import live
            ov = live.overlay(symbol)
            if ov is not None and len(ov):        # no history file yet, but today's live bars
                return ov[_COLS].copy()
            raise KeyError(f"no 1-minute data for {symbol!r}")
        return _with_live(symbol, _read(str(p)))
    p = _find(SOURCES, symbol)
    if p is not None:
        df = _read(str(p))
        if has_1m(symbol):  # 1m data newer than the 5m file (e.g. only 1m was topped up) extends the 5m base
            one = load(symbol, "1m")
            one = one[one.index >= df.index[-1].normalize()]      # only the days after the 5m file (fast in live)
            ext = _resample(one, "5min")[_COLS]
            ext = ext[ext.index > df.index[-1]]
            if len(ext):
                df = pd.concat([df, ext])
        return df
    if has_1m(symbol):
        return _resample(load(symbol, "1m"), "5min")[_COLS]
    raise KeyError(f"unknown symbol {symbol!r}")


def invalidate(new_day: bool = False):
    """New live bars arrived: drop derived caches (file reads in _read stay cached). The expiry calendar only
    changes with the day."""
    import indicators
    import options
    for f in (load, load_interval, indicators.compute_full, options.real_raw):
        f.cache_clear()
    if new_day:
        options._calendar.cache_clear()
        options._trading_days.cache_clear()


@lru_cache(maxsize=32)
def load_interval(symbol: str, interval: str = "5m") -> pd.DataFrame:
    """OHLCV at `interval`, plus `End` = the time the bar closed.

    Bars are labelled by their open time; `End` is what decides whether a bar is visible at a replay time.
    """
    df = load(symbol, "1m" if interval == "1m" else "5m")
    if interval in ("1m", "5m"):
        out = df.copy()
        out["End"] = out.index + _step(df)
        return out
    return _resample(df, INTERVALS[interval])


def sub_bars(symbol: str, tick: str, t0: pd.Timestamp, t1: pd.Timestamp) -> tuple[pd.DataFrame, str]:
    """The `tick`-sized bars that make up the bar [t0, t1) - used to replay a candle forming.

    tick="auto" uses 1-minute data when the symbol has it for that period, else 5-minute. 1m is used only
    if it covers every 5m sub-bar of the candle (Angel One's 1m feed lacks 15:15-15:28 on some days); otherwise
    it falls back to 5m, so the last tick always matches the complete candle. Returns (bars, tick used).
    """
    if tick == "auto":
        tick = "1m" if has_1m(symbol) else "5m"
    if tick == "1m":
        if has_1m(symbol):
            d = load_interval(symbol, "1m")
            seg = d[(d.index >= t0) & (d["End"] <= t1)]
            d5 = load_interval(symbol, "5m")
            need = d5[(d5.index >= t0) & (d5["End"] <= t1)].index
            if len(seg) and need.isin(seg.index.floor("5min")).all() and len(seg) >= 5 * len(need):
                return seg, "1m"
        tick = "5m"
    d = load_interval(symbol, tick)
    return d[(d.index >= t0) & (d["End"] <= t1)], tick


def window(symbol: str, start: str | pd.Timestamp, interval: str = "5m",
           before: int = 300, after: int = 2000) -> dict:
    """Bars around `start` for a replay session.

    The start bar is the last bar that has CLOSED by `start` (End <= start), so no visible candle contains
    any price from after `start`. Clamped to the data range.
    Returns bars [start_idx-before, start_idx+after], `ends` (bar close times) and `cursor` = index of the
    start bar in that list. The client must only ever show bars[:cursor+1].
    """
    df = load_interval(symbol, interval)
    if df.empty:
        raise ValueError(f"no data for {symbol}")
    pos = int(df["End"].searchsorted(pd.Timestamp(start), side="right")) - 1
    pos = min(max(pos, 0), len(df) - 1)
    lo, hi = max(0, pos - before), min(len(df), pos + after + 1)
    part = df.iloc[lo:hi]
    # Naive IST times sent as if UTC epoch seconds -> the chart's axis shows IST wall-clock.
    t = part.index.values.astype("datetime64[s]").astype("int64").tolist()
    ends = part["End"].values.astype("datetime64[s]").astype("int64").tolist()
    bars = [
        {"time": ti, "open": o, "high": h, "low": l, "close": c, "volume": v}
        for ti, o, h, l, c, v in zip(t, *(part[c].round(4).tolist() for c in _COLS))
    ]
    return {
        "symbol": symbol,
        "interval": interval,
        "cursor": pos - lo,
        "start": str(df.index[pos]),
        "first": str(df.index[0]),
        "last": str(df.index[-1]),
        "bars": bars,
        "ends": ends,
        "lo": lo,  # window = rows [lo, hi) of load_interval(symbol, interval); used to align indicators
        "hi": hi,
    }


def forming(symbol: str, interval: str, bar_time: str | pd.Timestamp, tick: str = "auto") -> dict:
    """The successive states of one candle as its sub-bars arrive (for live-style replay).

    partial[k] = open of sub 0, max high / min low / sum volume of subs 0..k, close of sub k.
    The last partial equals the complete bar (up to data-vendor differences between resolutions).
    """
    df = load_interval(symbol, interval)
    t0 = pd.Timestamp(bar_time)
    i = int(df.index.searchsorted(t0))
    if i >= len(df) or df.index[i] != t0:
        raise ValueError(f"no {interval} bar at {t0}")
    subs, used = sub_bars(symbol, tick, t0, df["End"].iloc[i])
    if MINUTES[used] >= MINUTES[interval]:
        subs = subs.iloc[0:0]
    part = pd.DataFrame({
        "Open": subs["Open"].iloc[0] if len(subs) else [],
        "High": subs["High"].cummax(), "Low": subs["Low"].cummin(),
        "Close": subs["Close"], "Volume": subs["Volume"].cumsum(),
    }, index=subs.index)
    ends = subs["End"].values.astype("datetime64[s]").astype("int64").tolist()
    partial = [
        {"open": o, "high": h, "low": l, "close": c, "volume": v, "end": e}
        for o, h, l, c, v, e in zip(*(part[c].round(4).tolist() for c in _COLS), ends)
    ]
    return {"i": i, "time": int(t0.value // 10**9), "tick": used, "partial": partial, "_partials": part}
