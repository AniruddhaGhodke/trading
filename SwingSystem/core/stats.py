"""Trade statistics shared by the backtest report and the live scorecard."""

import numpy as np
import pandas as pd


def summarize(trades: pd.DataFrame) -> dict:
    """Per-trade stats on CLOSED trades (net of costs)."""
    t = trades[trades["status"] == "CLOSED"] if "status" in trades else trades
    n = len(t)
    if n == 0:
        return {"trades": 0}
    r, ret = t["r_multiple"], t["return_pct"]
    gains, losses = ret[ret > 0].sum(), -ret[ret < 0].sum()
    return {
        "trades": n,
        "win_rate": (ret > 0).mean() * 100,
        "avg_ret_pct": ret.mean(),
        "avg_r": r.mean(),
        "profit_factor": gains / losses if losses > 0 else np.inf,
        # t-stat of mean return: ~>2 means unlikely to be pure luck (ignores overlap/multiple testing)
        "t_stat": ret.mean() / (ret.std(ddof=1) / np.sqrt(n)) if n > 1 and ret.std() > 0 else 0.0,
        "avg_hold": t["bars_held"].mean(),
    }


def format_row(label: str, s: dict, width: int = 22) -> str:
    if s.get("trades", 0) == 0:
        return f"{label:<{width}} {'0':>6}"
    pf = min(s["profit_factor"], 99.9)
    return (f"{label:<{width}} {s['trades']:>6} {s['win_rate']:>6.1f}% {s['avg_ret_pct']:>+8.2f}% "
            f"{s['avg_r']:>+7.2f} {pf:>6.2f} {s['t_stat']:>6.2f} {s['avg_hold']:>6.1f}")


def header(label: str = "", width: int = 22) -> str:
    return (f"{label:<{width}} {'Trades':>6} {'Win%':>7} {'AvgRet':>9} "
            f"{'AvgR':>7} {'PF':>6} {'t':>6} {'Hold':>6}")


def edge_verdict(e: dict, net_avg: float, min_edge: float | None = None,
                 min_t: float | None = None) -> tuple[str, list[str]]:
    """
    Label from edge_vs_control() output + the strategy's net average return per trade (%).
    EDGE needs all three: monthly t > min_t, per-trade edge >= min_edge, net_avg > 0.
    Returns (label, reasons it is not an EDGE).
    """
    import config
    min_edge = config.MIN_EDGE_PER_TRADE if min_edge is None else min_edge
    min_t = config.EDGE_MIN_T if min_t is None else min_t
    t, edge = e.get("t", 0.0), e.get("edge", np.nan)
    fails = []
    if not t > min_t:
        fails.append(f"monthly t {t:.2f} <= {min_t:g}")
    if not edge >= min_edge:
        fails.append(f"edge {edge:+.2f}%/trade < {min_edge:.2f}% floor")
    if not net_avg > 0:
        fails.append(f"net avg {net_avg:+.2f}%/trade <= 0 after costs")
    if not fails:
        return "EDGE (candidate)", []
    return ("weak" if t > 1.5 and edge > 0 else "NO EDGE vs random"), fails


def edge_vs_control(real: pd.DataFrame, ctrl: pd.DataFrame) -> dict:
    """
    Strategy minus control mean return (closed trades). Significance is measured
    on MONTHLY differences, because trades within a month are correlated.
    """
    real = real[real["status"] == "CLOSED"]
    ctrl = ctrl[ctrl["status"] == "CLOSED"]
    if len(real) == 0 or len(ctrl) == 0:
        return {"edge": np.nan, "t": 0.0, "beat": np.nan, "months": 0}
    rm = real.groupby(real["signal_date"].dt.to_period("M"))["return_pct"].mean()
    cm = ctrl.groupby(ctrl["signal_date"].dt.to_period("M"))["return_pct"].mean()
    d = (rm - cm).dropna()
    t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 and d.std() > 0 else 0.0
    return {"edge": real["return_pct"].mean() - ctrl["return_pct"].mean(), "t": float(t),
            "edge_monthly": float(d.mean()) if len(d) else np.nan,
            "beat": (d > 0).mean() * 100 if len(d) else np.nan, "months": len(d)}
