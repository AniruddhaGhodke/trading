"""
Data layer: universe loading + daily OHLCV download with a local CSV cache.

yfinance is the source for now; swapping to a broker API later only means
replacing `_download` - everything else consumes plain DataFrames.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

import config

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_CLOSE_IST = (15, 45)  # bars before this time today are still forming


def load_universe() -> list[str]:
    """Nifty 100 symbols as yfinance tickers (official NSE list, data/nifty100.csv)."""
    df = pd.read_csv(config.UNIVERSE_FILE)
    return [f"{s.strip()}.NS" for s in df["Symbol"]]


def load_sectors() -> dict[str, str]:
    """yfinance ticker -> NSE industry (from the same Nifty 100 file)."""
    df = pd.read_csv(config.UNIVERSE_FILE)
    return {f"{s.strip()}.NS": ind for s, ind in zip(df["Symbol"], df["Industry"])}


def _cache_path(symbol: str):
    return config.CACHE_DIR / f"{symbol.replace('^', 'IDX_')}.csv"


def _cache_is_fresh(symbol: str) -> bool:
    p = _cache_path(symbol)
    if not p.exists():
        return False
    fetched = datetime.fromtimestamp(p.stat().st_mtime, IST)
    now = datetime.now(IST)
    # Fresh if fetched today after market close, or fetched today and market not closed yet
    if fetched.date() != now.date():
        return False
    after_close = (fetched.hour, fetched.minute) >= MARKET_CLOSE_IST
    market_closed_now = (now.hour, now.minute) >= MARKET_CLOSE_IST
    return after_close or not market_closed_now


def _download(symbols: list[str]) -> dict[str, pd.DataFrame]:
    # Explicit end (exclusive) = tomorrow IST. yfinance's default end is "now" in UTC, which
    # drops today's completed bar when run between midnight and 05:30 IST.
    end = (datetime.now(IST) + timedelta(days=1)).strftime("%Y-%m-%d")
    raw = yf.download(
        symbols, start=config.HISTORY_START, end=end, interval="1d",
        auto_adjust=True, group_by="ticker", progress=False, threads=True,
    )
    out = {}
    for s in symbols:
        try:
            df = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        # yfinance emits flat zero-volume placeholder bars on some NSE holidays - not real sessions
        df = df[~((df["Volume"] == 0) & (df["High"] == df["Low"]))]
        if len(df) > 0:
            df.index = pd.to_datetime(df.index).tz_localize(None)
            out[s] = fix_corporate_jumps(df)
    _patch_from_angel(out)       # broker: exact latest daily bars when Yahoo is late/broken
    _patch_from_intraday(out)    # fallback without broker credentials
    return out


def _patch_from_angel(out: dict[str, pd.DataFrame]):
    """
    History stays Yahoo (adjusted for splits/bonuses, same as the backtests). Only sessions that
    Yahoo is MISSING at the end (late or empty latest bar) are filled with Angel One daily candles.
    Makes no broker requests when Yahoo is complete. Needs SwingSystem/.env.
    """
    if not out or not (config.ROOT / ".env").exists():
        return
    try:
        import logging
        logging.disable(logging.CRITICAL)
        from core.angel_data import Angel
        now = datetime.now(IST)
        a = None
        # last completed session according to the broker (handles holidays)
        def last_session(api):
            n = api.candles("^NSEI", "1d", datetime.now() - timedelta(days=10))
            n = n[(n.index.date < now.date()) | ((now.hour, now.minute) >= MARKET_CLOSE_IST)]
            return n.index[-1] if len(n) else None
        latest_yahoo = max(df.index[-1] for df in out.values())
        # Yahoo is behind for everyone if today's session closed but no symbol has today's bar
        all_behind = (now.weekday() < 5 and (now.hour, now.minute) >= MARKET_CLOSE_IST
                      and latest_yahoo.date() < now.date())
        need = {s: df.index[-1] for s, df in out.items() if all_behind or df.index[-1] < latest_yahoo}
        if not need:
            return
        a = Angel()
        last = last_session(a)
        if last is None:
            return
        need = {s: d for s, d in need.items() if d < last}
        for s, d in need.items():
            try:
                new = a.candles(s, "1d", (d + timedelta(days=1)).to_pydatetime())
            except Exception:
                continue
            new.index = new.index.normalize()
            new = new[(new.index > d) & (new.index <= last)]
            if len(new):
                out[s] = pd.concat([out[s], new[["Open", "High", "Low", "Close", "Volume"]]])
        if need:
            print(f"Filled latest daily bars from Angel One for {len(need)} symbols (Yahoo was behind)")
    except BaseException as e:   # never let the broker break the daily run
        print(f"(Angel One daily patch skipped: {e})")


def _patch_from_intraday(out: dict[str, pd.DataFrame]):
    """
    Yahoo sometimes serves the most recent daily bar with NaN prices for hours
    after the close, while hourly bars are complete. Rebuild any completed
    session newer than the last valid daily bar from hourly data.
    """
    if not out:
        return
    syms = list(out)
    try:
        hr = yf.download(syms, period="5d", interval="1h", auto_adjust=True,
                         group_by="ticker", progress=False, threads=True)
    except Exception:
        return
    now = datetime.now(IST)
    for s in syms:
        try:
            h = (hr[s] if isinstance(hr.columns, pd.MultiIndex) else hr).dropna(subset=["Close"])
        except KeyError:
            continue
        if h.empty:
            continue
        idx = h.index.tz_convert(IST) if h.index.tz is not None else h.index.tz_localize("UTC").tz_convert(IST)
        h = h.set_axis(idx)
        daily = h.groupby(h.index.date).agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        daily.index = pd.to_datetime(daily.index)
        last_valid = out[s].index[-1]
        new = daily[daily.index > last_valid]
        # only completed sessions (forming bars are handled by _drop_forming_bar anyway)
        new = new[(new.index.date < now.date()) | ((now.hour, now.minute) >= MARKET_CLOSE_IST)]
        if len(new):
            out[s] = pd.concat([out[s], new])


CORP_ACTION_GAP = 0.30   # overnight gaps this large in Nifty 100 stocks are splits/demergers, not trading


def fix_corporate_jumps(df: pd.DataFrame, session: pd.Index | None = None) -> pd.DataFrame:
    """
    Yahoo leaves some splits/demergers unadjusted (VEDL demerger 2026-04-30 shows -65%,
    TRENT 2026-01-01 -33%). Treat any overnight gap >= CORP_ACTION_GAP as a corporate
    action and scale all EARLIER bars by the gap ratio, so the series is continuous.
    `session` groups intraday bars into days (None = one bar per day).
    """
    if df.empty:
        return df
    sess = pd.Index(session if session is not None else df.index)
    first_open = df["Open"].groupby(sess).first()
    last_close = df["Close"].groupby(sess).last()
    gap = first_open / last_close.shift(1)
    jumps = gap[(gap - 1).abs() >= CORP_ACTION_GAP]
    if jumps.empty:
        return df
    out = df.copy()
    for day, ratio in jumps.items():
        before = sess < day
        for col in ("Open", "High", "Low", "Close"):
            out.loc[before, col] = out.loc[before, col] * ratio
        out.loc[before, "Volume"] = out.loc[before, "Volume"] / ratio
    return out


def _drop_forming_bar(df: pd.DataFrame) -> pd.DataFrame:
    """Drop today's bar if the market hasn't closed - signals must use completed bars."""
    now = datetime.now(IST)
    if len(df) and df.index[-1].date() == now.date() and (now.hour, now.minute) < MARKET_CLOSE_IST:
        return df.iloc[:-1]
    return df


def load_prices(symbols: list[str], refresh: bool = False, verbose: bool = True) -> dict[str, pd.DataFrame]:
    """Return {symbol: daily OHLCV}, downloading only stale/missing symbols."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stale = [s for s in symbols if refresh or not _cache_is_fresh(s)]

    if stale:
        if verbose:
            print(f"Downloading {len(stale)} symbols from yfinance...")
        fresh = _download(stale)
        for s, df in fresh.items():
            df.to_csv(_cache_path(s))
        missing = sorted(set(stale) - set(fresh))
        if missing and verbose:
            print(f"  WARNING: no data for {len(missing)} symbols: {', '.join(missing)}")

    data = {}
    for s in symbols:
        p = _cache_path(s)
        if p.exists():
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            data[s] = _drop_forming_bar(df)
    return data


def load_index(refresh: bool = False) -> pd.DataFrame:
    return load_prices([config.INDEX_SYMBOL], refresh=refresh, verbose=False)[config.INDEX_SYMBOL]
