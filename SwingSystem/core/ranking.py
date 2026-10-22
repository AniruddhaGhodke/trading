"""
Call ranking shared by the live scanner and the portfolio simulation, so both
order a day's signals the same way.

Default ("score") is what the scanner shows: each strategy's own 0-100 score plus a confluence
bonus. Those scores are NOT comparable across strategies (trend_pullback scores 50-117, the lab
strategies a flat 55-60), so the portfolio can opt into:
  rank="ev"        walk-forward expected return of the candidate's strategy (see walk_forward_ev)
  tie_break="atr"  among equal ranks, higher ATR% first (2016-2020 train: the only signal-time
                   feature whose top quintile beat its bottom quintile in all four live strategies)
Remaining ties: seeded random order (tie_seed) or symbol A-Z.
"""

import numpy as np
import pandas as pd

CONFLUENCE_BONUS = 10   # per extra DIFFERENT idea (group) agreeing on a symbol - an untested guess
EV_PRIOR_TRADES = 100   # walk-forward EV shrinks toward 0 as if 100 trades at 0% were already seen
RANK_METHODS = ("score", "ev")
TIE_BREAKS = ("symbol", "atr")


def final_scores(c: pd.DataFrame, by: list[str] | None = None) -> pd.Series:
    """
    score + CONFLUENCE_BONUS x (number of distinct live groups on the symbol - 1).
    c needs columns symbol, group, research, score. `by` = extra keys that define one
    scan day (e.g. ["signal_date"] when c holds many days; the scanner passes one day).
    """
    keys = [*(by or []), "symbol"]
    live = c[~c["research"]]
    n_groups = live.groupby(keys)["group"].transform("nunique").reindex(c.index).fillna(1)
    return c["score"] + CONFLUENCE_BONUS * (n_groups - 1)


def walk_forward_ev(t: pd.DataFrame, prior_trades: int = EV_PRIOR_TRADES) -> pd.Series:
    """
    Per candidate: mean net return (%) of the SAME strategy's closed trades that exited strictly
    before the candidate's signal date, shrunk toward 0: sum / (n + prior_trades). Only outcomes
    known at the signal close are used, so this can rank a backtest without lookahead.
    t needs strategy, signal_date, status, exit_date, return_pct.
    """
    ev = pd.Series(0.0, index=t.index)
    for strat, g in t.groupby("strategy"):
        done = g[g["status"] == "CLOSED"].sort_values("exit_date")
        exits = done["exit_date"].to_numpy(dtype="datetime64[ns]")
        csum = np.concatenate([[0.0], done["return_pct"].cumsum().to_numpy()])
        n = np.searchsorted(exits, g["signal_date"].to_numpy(dtype="datetime64[ns]"), side="left")
        denom = n + prior_trades
        ev.loc[g.index] = np.divide(csum[n], denom, out=np.zeros(len(n)), where=denom > 0)
    return ev


def order_candidates(t: pd.DataFrame, rank: str = "score", tie_break: str = "symbol",
                     tie_seed: int | None = None) -> pd.DataFrame:
    """
    Sort candidates best-first within each signal day. Adds `rank_score`.
    rank: "score" (final_score, as the scanner) or "ev" (walk_forward_ev).
    tie_break: "symbol" (none beyond the final fallback) or "atr" (higher atr_pct first; needs atr_pct).
    Final fallback: seeded random if tie_seed is given, else symbol A-Z.
    """
    if rank not in RANK_METHODS or tie_break not in TIE_BREAKS:
        raise ValueError(f"rank in {RANK_METHODS}, tie_break in {TIE_BREAKS}")
    t = t.copy()
    if rank == "score":
        t["rank_score"] = t["final_score"]
    else:   # precomputed `ev` (candidate_trades) survives slicing the candidates by date
        t["rank_score"] = t["ev"] if "ev" in t else walk_forward_ev(t)
    t["_tie1"] = -t["atr_pct"].fillna(-np.inf) if tie_break == "atr" else 0.0
    t["_tie2"] = (np.random.default_rng(tie_seed).random(len(t)) if tie_seed is not None
                  else t["symbol"].rank(method="dense").to_numpy())
    t = t.sort_values(["rank_score", "_tie1", "_tie2"], ascending=[False, True, True], kind="mergesort")
    return t.drop(columns=["_tie1", "_tie2"])
