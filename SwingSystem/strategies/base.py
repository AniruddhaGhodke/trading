"""
Strategy plug-in interface.

A strategy only answers: "on which bars is there a setup, how good is it, and
where is the stop?" Execution (entry fill, exits, costs) is handled by
core.execution so that backtests and live tracking behave identically.
"""

from core.plan import ExitPlan  # noqa: F401  (re-exported)

import pandas as pd


class Strategy:
    name: str = "base"
    description: str = ""
    exit_plan: ExitPlan = ExitPlan()
    research_only: bool = False   # True = logged to journal but never shown as a ranked call
    group: str = ""               # strategies with the same idea share a group (no double confluence)

    def prepare(self, df: pd.DataFrame, ctx: pd.DataFrame) -> pd.DataFrame:
        """
        Given daily OHLCV and aligned market context, return df with columns:
          signal       bool   setup complete at this bar's close (long entry next open)
          score        float  0-100 quality of the setup
          stop         float  absolute stop price
          atr          float  ATR at the bar (for gap filter)
          exit_signal  bool   discretionary exit trigger (optional; default False)
          reason       str    short human-readable description (only needed on signal bars)
        Must be strictly backward-looking.
        """
        raise NotImplementedError
