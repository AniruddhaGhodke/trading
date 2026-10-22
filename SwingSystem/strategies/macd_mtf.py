"""
MACD Multi-Timeframe (port of SwingTrading/macd_mtf_v2_strategy.py, "relaxed" mode).

Changes vs the original:
  - Weekly data is resampled from daily and mapped without lookahead
    (the original leaked each week's Friday close into Mon-Thu signals).
  - Vectorised; entry is next-day open (original filled at signal close).
  - Added a 60-day time stop.
Signal logic, quality scoring, stop, 2R target and 1R breakeven are unchanged.
"""

import numpy as np
import pandas as pd

from core.indicators import atr, macd, to_weekly, weekly_to_daily
from strategies.base import ExitPlan, Strategy


class MacdMtf(Strategy):
    name = "macd_mtf"
    description = "Daily MACD trend-cross / bullish divergence, weekly MACD as bias"
    exit_plan = ExitPlan(target_r=2.0, breakeven_r=1.0, max_hold=60)
    research_only = True  # no edge vs random-entry control (2026-10-07 backtest)

    def __init__(self, require_weekly: bool = False):
        self.require_weekly = require_weekly

    def prepare(self, df: pd.DataFrame, ctx: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        m, s, h = macd(df["Close"])
        df["atr"] = atr(df)

        wk = to_weekly(df)
        wm, _, wh = macd(wk["Close"])
        w_bias = weekly_to_daily(wm > 0, df.index).fillna(False).astype(bool)
        w_strong = weekly_to_daily(wm.abs() / wm.rolling(20).std() > 1.0, df.index).fillna(False).astype(bool)
        w_mom = weekly_to_daily(wh > wh.shift(1), df.index).fillna(False).astype(bool)

        cross_up = (m > s) & (m.shift(1) <= s.shift(1))
        hist_flip = (h > 0) & (h.shift(1) <= 0)
        far = m.abs() > 0.5 * m.rolling(50).std()
        s1 = cross_up & (m > 0) & far
        s2 = (df["Low"] < df["Low"].shift(10)) & (m > m.shift(10)) & (h > h.shift(1)) & ~s1

        q1 = 2 + w_bias + (w_bias & w_strong) + (w_bias & w_mom) + hist_flip
        q2 = 2 + w_bias + hist_flip
        quality = np.where(s1, q1, np.where(s2, q2, 0))

        signal = s1 | s2
        if self.require_weekly:
            signal &= w_bias

        df["signal"] = signal & df["atr"].notna() & (np.arange(len(df)) >= 60)
        df["score"] = quality / 6 * 100
        prior_low = df["Low"].shift(1).rolling(20).min()
        df["stop"] = np.minimum(prior_low * 0.998, df["Close"] - 2 * df["atr"])
        df["exit_signal"] = False
        df["reason"] = np.where(s1, "S1 trend cross", np.where(s2, "S2 bull divergence", ""))
        df["reason"] = df["reason"] + np.where(w_bias, " | weekly bullish", " | weekly NOT aligned")
        return df
