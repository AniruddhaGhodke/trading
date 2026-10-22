"""
Market regime from the Nifty 50 index (simplified from longterm_system.py's
MarketRegimeDetector, whose Strong_Bear branch was unreachable).
"""

import numpy as np
import pandas as pd

from core.indicators import sma


def market_context(index_df: pd.DataFrame) -> pd.DataFrame:
    """Daily regime + index returns, used by strategies (relative strength) and the ranker."""
    close = index_df["Close"]
    s50, s200 = sma(close, 50), sma(close, 200)
    ctx = pd.DataFrame(index=index_df.index)
    ctx["idx_close"] = close
    ctx["idx_ret_126"] = close.pct_change(126)
    ctx["regime"] = np.select(
        [(close > s200) & (s50 > s200), (close < s200) & (s50 < s200)],
        ["BULL", "BEAR"],
        default="NEUTRAL",
    )
    ctx.loc[s200.isna(), "regime"] = "NEUTRAL"
    return ctx


def align_context(ctx: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Align index context to a stock's trading days (ffill, never bfill)."""
    return ctx.reindex(ctx.index.union(index)).ffill().reindex(index)
