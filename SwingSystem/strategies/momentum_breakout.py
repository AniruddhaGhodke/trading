"""
Momentum breakout: strong stocks breaking out to new highs on volume.

Based on the well-documented momentum / 52-week-high effect:
  - Close breaks above the prior 20-day high
  - Within 5% of the 52-week high
  - Uptrend: close > SMA50 > SMA200
  - Volume >= 1.5x its 50-day average
  - 6-month return beats the Nifty (relative strength)
Exit: initial stop 2 ATR, trail with close below SMA20, 60-day time stop.
"""

import numpy as np
import pandas as pd

from core.indicators import atr, sma
from strategies.base import ExitPlan, Strategy


class MomentumBreakout(Strategy):
    name = "momentum_breakout"
    description = "20d breakout near 52w high, uptrend, volume, beats Nifty"
    exit_plan = ExitPlan(target_r=None, breakeven_r=None, max_hold=60)
    research_only = True  # failed matched-hold random-entry control (2026-10-07 backtest)

    def prepare(self, df: pd.DataFrame, ctx: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c = df["Close"]
        df["atr"] = atr(df)
        s20, s50, s200 = sma(c, 20), sma(c, 50), sma(c, 200)
        hi20 = df["High"].shift(1).rolling(20).max()
        hi252 = df["High"].rolling(252).max()
        vol_ratio = df["Volume"] / df["Volume"].shift(1).rolling(50).mean()
        rs = c.pct_change(126) - ctx["idx_ret_126"]

        df["signal"] = (
            (c > hi20) & (c >= 0.95 * hi252) & (c > s50) & (s50 > s200)
            & (vol_ratio >= 1.5) & (rs > 0) & df["atr"].notna()
        )
        df["score"] = (50 + (rs * 100).clip(0, 30) + ((vol_ratio - 1.5) * 10).clip(0, 20)).clip(0, 100)
        df["stop"] = c - 2 * df["atr"]
        df["exit_signal"] = c < s20
        df["reason"] = (
            "20d breakout, vol " + vol_ratio.round(1).astype(str) + "x, RS +"
            + (rs * 100).round(0).astype(str) + "% vs Nifty"
        )
        return df
