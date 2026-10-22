"""
Causality: a strategy's output on day T must not change when later bars are added.
Compares prepare(data up to T) with prepare(full data) at T, for every registered strategy.
"""

import zlib

import numpy as np
import pandas as pd
import pytest

from conftest import needs_cache, read_prices
from core import review
from core.indicators import weekly_to_daily
from core.regime import align_context, market_context
from strategies import ALL_STRATEGIES

SYMBOLS = ["RELIANCE.NS", "ADANIENT.NS", "TATASTEEL.NS", "VEDL.NS", "HDFCBANK.NS"]
COLS = ["signal", "score", "stop", "atr", "exit_signal"]
N_DATES = 12


def same(x, y) -> bool:
    if pd.isna(x) and pd.isna(y):
        return True
    if isinstance(x, (bool, np.bool_)) or isinstance(y, (bool, np.bool_)):
        return bool(x) == bool(y)
    return bool(np.isclose(x, y, rtol=1e-6))


def lookahead_errors(strategies, raw, index_df, n_dates=N_DATES, seed=0, features=True) -> list:
    full_ctx = align_context(market_context(index_df), raw.index)
    full = {s.name: s.prepare(raw, full_ctx) for s in strategies}
    rng = np.random.default_rng(seed)
    bad = []
    for T in sorted(rng.choice(np.arange(300, len(raw) - 1), n_dates, replace=False)):
        cut = raw.iloc[: T + 1]
        ctx = align_context(market_context(index_df[index_df.index <= cut.index[-1]]), cut.index)
        for s in strategies:
            p, f = s.prepare(cut, ctx).iloc[-1], full[s.name].iloc[T]
            bad += [(s.name, c, str(cut.index[-1].date())) for c in COLS if not same(p[c], f[c])]
        if features and review.signal_features(cut, T, ctx, None) != review.signal_features(raw, T, full_ctx, None):
            bad.append(("features", "", str(cut.index[-1].date())))
    return bad


@needs_cache
@pytest.mark.parametrize("symbol", SYMBOLS)
def test_truncated_equals_full(symbol, index_df):
    bad = lookahead_errors(ALL_STRATEGIES, read_prices(symbol), index_df, seed=zlib.crc32(symbol.encode()))
    assert not bad, f"lookahead in {bad[:5]}"


@needs_cache
def test_checker_catches_a_leak(index_df):
    """Sanity check of the test itself: a strategy peeking one bar ahead must be flagged."""
    from strategies.trend_pullback import TrendPullback

    class Leaky(TrendPullback):
        name = "leaky"

        def prepare(self, df, ctx):
            out = super().prepare(df, ctx)
            out["signal"] = df["Close"].shift(-1) > df["Close"]    # tomorrow's close
            return out
    assert lookahead_errors([Leaky()], read_prices("RELIANCE.NS"), index_df, n_dates=5, features=False)


def test_weekly_mapping_has_no_lookahead():
    """Mon-Thu see the PREVIOUS week's value; Friday sees its own completed week."""
    weekly = pd.Series([1.0, 2.0], index=pd.to_datetime(["2024-01-05", "2024-01-12"]))   # Fridays
    daily = pd.date_range("2024-01-08", "2024-01-12", freq="B")                           # Mon..Fri
    assert weekly_to_daily(weekly, daily).tolist() == [1.0, 1.0, 1.0, 1.0, 2.0]
