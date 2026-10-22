"""
Universe backtest for a strategy. Uses core.execution.simulate_trade, exactly
the same fill/exit logic the live tracker uses.

One position per symbol per strategy at a time; no portfolio capital limit
(this measures the per-trade edge, not a portfolio equity curve).
"""

import numpy as np
import pandas as pd

from core.execution import Bars, simulate_trade
from core.regime import align_context
from core.stats import edge_verdict, edge_vs_control, format_row, header, summarize
from core.plan import ExitPlan
from strategies.base import Strategy


def backtest_strategy(strategy: Strategy, prices: dict, ctx: pd.DataFrame,
                      start: str | None = None, random_seed: int | None = None,
                      control_holds: np.ndarray | None = None) -> pd.DataFrame:
    """
    random_seed set => CONTROL run: the same number of entries per symbol are
    placed on random bars, using the strategy's initial stop, but exiting on a
    time stop whose length is sampled from the REAL trades' holding periods
    (control_holds). This isolates entry timing: if the strategy can't beat
    random entries held just as long, its entry logic has no edge.
    """
    rng = np.random.default_rng(random_seed) if random_seed is not None else None
    rows = []
    for symbol, raw in prices.items():
        if len(raw) < 260:
            continue
        actx = align_context(ctx, raw.index)
        df = strategy.prepare(raw, actx)
        sig_idx = np.flatnonzero(df["signal"].to_numpy())
        # 1 year warm-up (and optional start date), identical for real and control
        in_window = (np.arange(len(df)) >= 252) & (df.index >= pd.Timestamp(start or "1900-01-01"))
        sig_idx = sig_idx[in_window[sig_idx]]
        if rng is not None and len(sig_idx):
            eligible = np.flatnonzero(in_window & df["atr"].notna().to_numpy()
                                      & (df["stop"] < df["Close"]).to_numpy())
            sig_idx = np.sort(rng.choice(eligible, size=min(len(sig_idx), len(eligible)), replace=False))
            df["exit_signal"] = False

        bars = Bars(df)
        busy_until = -1
        for i in sig_idx:
            if i <= busy_until:
                continue
            plan = strategy.exit_plan
            if rng is not None:
                plan = ExitPlan(max_hold=int(rng.choice(control_holds)))
            t = simulate_trade(df, i, plan, bars)
            if t["status"] == "CLOSED":
                busy_until = df.index.get_loc(t["exit_date"])
            elif t["status"] == "OPEN":
                busy_until = len(df)
            rows.append({"symbol": symbol, "strategy": strategy.name,
                         "score": float(df["score"].iloc[i]),
                         "regime": actx["regime"].iloc[i],
                         **t})
    return pd.DataFrame(rows)


def _edge_line(real: pd.DataFrame, ctrl: pd.DataFrame, min_edge: float | None = None) -> str:
    e = edge_vs_control(real, ctrl)
    verdict, fails = edge_verdict(e, real["return_pct"].mean(), min_edge)
    return (f"{'EDGE over control':<22} {e['edge']:>+16.2f}% per trade | monthly t={e['t']:.2f}, "
            f"beats control in {e['beat']:.0f}% of {e['months']} months -> {verdict}"
            + (f" ({'; '.join(fails)})" if fails else ""))


def print_report(trades: pd.DataFrame, name: str, control: pd.DataFrame | None = None,
                 min_edge: float | None = None):
    closed = trades[trades["status"] == "CLOSED"] if len(trades) else trades
    skipped = (trades["status"] == "SKIPPED").sum() if len(trades) else 0
    print(f"\n{'=' * 78}\n{name}\n{'=' * 78}")
    if len(closed) == 0:
        print("No closed trades.")
        return
    ctrl = control[control["status"] == "CLOSED"] if control is not None and len(control) else None
    print(header())
    print(format_row("ALL", summarize(closed)))
    if ctrl is not None:
        print(format_row("CONTROL (random entry)", summarize(ctrl)))
        print(_edge_line(closed, ctrl, min_edge))
    print(f"(skipped at entry due to gaps: {skipped})")

    print("\n" + header("By year") + ("   Control  Edge" if ctrl is not None else ""))
    for y, g in closed.groupby(closed["signal_date"].dt.year):
        line = format_row(str(y), summarize(g))
        if ctrl is not None:
            cg = ctrl[ctrl["signal_date"].dt.year == y]
            if len(cg):
                cr = cg["return_pct"].mean()
                line += f" {cr:>+8.2f}% {g['return_pct'].mean() - cr:>+5.2f}"
        print(line)

    print("\n" + header("By market regime"))
    for reg, g in closed.groupby("regime"):
        print(format_row(reg, summarize(g)))

    print("\n" + header("By score"))
    buckets = pd.cut(closed["score"], [-1, 50, 70, 101], labels=["<=50", "50-70", ">70"])
    for b, g in closed.groupby(buckets, observed=True):
        print(format_row(str(b), summarize(g)))

    print("\nExit reasons: " + ", ".join(f"{k} {v}" for k, v in closed["exit_reason"].value_counts().items()))
