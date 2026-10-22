"""
Trend pullback (RSI-2 style, after Larry Connors' short-term mean reversion work):
buy short, sharp dips inside an established uptrend.
  - Close > SMA200 and SMA200 rising (vs 20 days ago)
  - RSI(2) < 10  (2-3 days of selling)
Exit: close back above SMA5 -> next open; 2.5 ATR disaster stop; 10-day time stop.
"""

import numpy as np
import pandas as pd

from core.indicators import atr, rsi, sma
from strategies.base import ExitPlan, Strategy


class TrendPullback(Strategy):
    name = "trend_pullback"
    description = "RSI(2) oversold dip in a rising 200-day uptrend"
    exit_plan = ExitPlan(target_r=None, breakeven_r=None, max_hold=10)

    def prepare(self, df: pd.DataFrame, ctx: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c = df["Close"]
        df["atr"] = atr(df)
        s5, s200 = sma(c, 5), sma(c, 200)
        r2 = rsi(c, 2)
        trend = (c / s200 - 1)

        df["signal"] = (c > s200) & (s200 > s200.shift(20)) & (r2 < 10) & df["atr"].notna()
        df["score"] = (50 + (10 - r2) * 3 + (trend * 100).clip(0, 20)).clip(0, 100)
        df["stop"] = c - 2.5 * df["atr"]
        df["exit_signal"] = c > s5
        df["reason"] = "RSI2 " + r2.round(1).astype(str) + ", " + (trend * 100).round(0).astype(str) + "% above SMA200"
        return df
