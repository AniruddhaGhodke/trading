"""
Trade simulator shared by the backtester, the live outcome tracker AND the
research lab. There is exactly one implementation (`simulate`), working on
plain arrays for speed; `simulate_trade` is a DataFrame convenience wrapper.

Rules (long only):
  - Signal at close of bar i. Entry at OPEN of bar i+1.
  - Skip if i+1 opens at/below the stop or gaps up > MAX_ENTRY_GAP_ATR x ATR.
  - Each bar: gap below stop -> exit at open; else low <= stop -> exit at stop.
    If stop and target are both touched in one bar, assume the STOP hit first.
  - Breakeven / exit_signal / time stop evaluated at the close.
    exit_signal exits at the next open; time stop exits at that close.
  - Costs: config.COST_PER_SIDE on entry and exit.
"""

import pandas as pd

import config
from core.plan import ExitPlan


class Bars:
    """
    Plain-list view of a prepared DataFrame (list indexing is ~50x faster than .iloc).

    Intraday data adds two optional columns:
      session      - session id (e.g. the date); a trade never enters in a different session
      session_last - True on the last TRADABLE bar of a session; open trades exit at its close
    """

    def __init__(self, df: pd.DataFrame):
        self.index = df.index
        self.o = df["Open"].tolist()
        self.h = df["High"].tolist()
        self.l = df["Low"].tolist()
        self.c = df["Close"].tolist()
        self.exit_sig = df["exit_signal"].astype(bool).tolist() if "exit_signal" in df else [False] * len(df)
        self.session = df["session"].tolist() if "session" in df else None
        self.session_last = df["session_last"].astype(bool).tolist() if "session_last" in df else None
        self.n = len(df)


def simulate(b: Bars, i: int, stop: float, atr_i: float, plan: ExitPlan,
             direction: int = 1, cost: float | None = None, gap_filter: bool = True) -> dict:
    """
    Core simulator. direction = 1 long / -1 short. Returns a trade dict with a 'status'
    and bar indices (entry_i / exit_i). For a short, `stop` is ABOVE the entry
    (use float('inf') for no stop); for a long, below (0.0 = no stop).
    """
    d = direction
    ref = b.c[i]
    base = {"signal_i": i, "ref_close": ref, "initial_stop": stop, "direction": d}

    j = i + 1
    if j >= b.n:
        return {**base, "status": "PENDING"}
    if b.session is not None and b.session[j] != b.session[i]:
        return {**base, "status": "SKIPPED", "exit_reason": "next_session", "entry_i": j}

    entry = b.o[j]
    if d * (entry - stop) <= 0:
        reason = "gap_below_stop" if d == 1 else "gap_above_stop"
        return {**base, "status": "SKIPPED", "exit_reason": reason, "entry_i": j}
    if gap_filter and d * (entry - ref) > config.MAX_ENTRY_GAP_ATR * atr_i:
        return {**base, "status": "SKIPPED", "exit_reason": "gap_up_chase", "entry_i": j}

    risk = d * (entry - stop)
    if plan.target_pct:
        target = entry * (1 + d * plan.target_pct)
    elif plan.target_r:
        target = entry + d * plan.target_r * risk
    else:
        target = None

    exit_price = exit_reason = None
    pending_rule_exit = False
    k = j
    while k < b.n:
        o, h, l, c = b.o[k], b.h[k], b.l[k], b.c[k]
        worst, best = (l, h) if d == 1 else (h, l)   # adverse / favourable extreme of the bar

        if pending_rule_exit:
            exit_price, exit_reason = o, "exit_rule"
            break
        if k > j and d * (o - stop) <= 0:
            exit_price, exit_reason = o, "stop_gap"
            break
        if d * (worst - stop) <= 0:
            exit_price, exit_reason = stop, "stop"
            break
        if target is not None:
            if k > j and d * (o - target) >= 0:
                exit_price, exit_reason = o, "target_gap"
                break
            if d * (best - target) >= 0:
                exit_price, exit_reason = target, "target"
                break

        if b.session_last is not None and b.session_last[k]:
            exit_price, exit_reason = c, "eod"
            break
        if k - j + 1 >= plan.max_hold:
            exit_price, exit_reason = c, "time"
            break
        if plan.breakeven_r and d * (c - entry) >= plan.breakeven_r * risk:
            stop = max(stop, entry) if d == 1 else min(stop, entry)
        if b.exit_sig[k]:
            pending_rule_exit = True
        k += 1

    trade = {**base, "entry_i": j, "entry_price": entry, "target": target, "risk": risk}
    if exit_price is None:
        last = b.c[-1]
        # An exit decided at the last close happens next session - say so, or the holder never hears it
        due = ("rule exit at next open" if pending_rule_exit else
               "time exit at next close" if b.n - j + 1 >= plan.max_hold else None)
        return {**trade, "status": "OPEN", "current_stop": stop, "mark_price": last,
                "bars_held": b.n - j, "exit_due": due, **_pnl(entry, last, risk, d, cost)}
    return {**trade, "status": "CLOSED", "exit_i": k, "exit_price": exit_price,
            "exit_reason": exit_reason, "bars_held": k - j + 1, **_pnl(entry, exit_price, risk, d, cost)}


def with_dates(t: dict, index: pd.DatetimeIndex) -> dict:
    """Convert bar indices in a trade dict to dates."""
    out = {k: v for k, v in t.items() if not k.endswith("_i")}
    out["signal_date"] = index[t["signal_i"]]
    if "entry_i" in t:
        out["entry_date"] = index[t["entry_i"]]
    if "exit_i" in t:
        out["exit_date"] = index[t["exit_i"]]
    return out


def simulate_trade(df: pd.DataFrame, i: int, plan: ExitPlan, bars: Bars | None = None) -> dict:
    """DataFrame wrapper (df = output of Strategy.prepare). Pass `bars` to reuse across calls."""
    b = bars or Bars(df)
    t = simulate(b, i, float(df["stop"].iloc[i]), float(df["atr"].iloc[i]), plan)
    return with_dates(t, df.index)


def _pnl(entry: float, exit_: float, risk: float, d: int = 1, cost: float | None = None) -> dict:
    c = config.COST_PER_SIDE if cost is None else cost
    if d == 1:
        net_ret = (exit_ * (1 - c)) / (entry * (1 + c)) - 1
    else:   # short: sell at entry, buy back at exit
        net_ret = (entry * (1 - c) - exit_ * (1 + c)) / entry
    return {"return_pct": net_ret * 100, "r_multiple": net_ret * entry / risk if risk not in (0, float("inf")) else 0.0}
