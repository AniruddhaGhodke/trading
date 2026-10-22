"""
Indicator functions. All are strictly backward-looking (rolling / ewm / positive
shifts only), so a value on day T never uses data from after T.
"""

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLCV into weeks ending Friday (bar labelled by week END)."""
    return df.resample("W-FRI").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()


def weekly_to_daily(weekly: pd.Series, daily_index: pd.DatetimeIndex) -> pd.Series:
    """
    Map a weekly series onto daily bars WITHOUT lookahead.

    Weekly bars are labelled by their week-ending Friday, so forward-filling
    means Mon-Thu see the PREVIOUS completed week, and Friday (after close)
    sees its own completed week. An in-progress week is labelled with a future
    Friday and is therefore never visible early.

    (The old macd_mtf_v2_strategy.py used yfinance weekly bars labelled by
    Monday, which leaked that week's Friday close into Mon-Thu decisions.)
    """
    combined = weekly.reindex(weekly.index.union(daily_index)).ffill()
    return combined.reindex(daily_index)
