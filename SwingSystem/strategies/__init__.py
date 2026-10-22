"""Strategy registry - add new strategies here to include them in scan/backtest/track."""

from strategies.macd_mtf import MacdMtf
from strategies.momentum_breakout import MomentumBreakout
from strategies.trend_pullback import TrendPullback
from strategies.lab_promoted import PROMOTED

ALL_STRATEGIES = [MacdMtf(), MomentumBreakout(), TrendPullback(), *PROMOTED]
REGISTRY = {s.name: s for s in ALL_STRATEGIES}
