"""Exit plan shared by strategies, simulator and lab."""

from dataclasses import dataclass


@dataclass
class ExitPlan:
    target_r: float | None = None      # take profit at N x initial risk (None = no fixed target)
    target_pct: float | None = None    # OR take profit at entry x (1 + pct), e.g. 0.05 = +5%
    breakeven_r: float | None = None   # move stop to entry once close >= entry + N x risk
    max_hold: int = 60                 # time stop, in trading days
    # Strategies may also emit an 'exit_signal' column -> exit at next open
