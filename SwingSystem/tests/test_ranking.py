"""Ranking options (walk-forward EV, ATR tie-break) and budget selection in the portfolio."""

import numpy as np
import pandas as pd
import pytest

from core.portfolio import PortfolioConfig, simulate_portfolio
from core.ranking import order_candidates, walk_forward_ev

D = pd.bdate_range("2024-01-01", periods=12)


def row(strategy, sig, exit_=None, ret=0.0, status="CLOSED", symbol="X", score=60, atr_pct=2.0):
    return {"strategy": strategy, "symbol": symbol, "signal_date": D[sig],
            "exit_date": D[exit_] if exit_ is not None else pd.NaT, "status": status,
            "return_pct": ret, "final_score": score, "atr_pct": atr_pct}


def test_walk_forward_ev_uses_only_trades_exited_before_signal():
    t = pd.DataFrame([row("a", 0, 2, ret=10.0), row("a", 1, 5, ret=-4.0),
                      row("a", 2), row("a", 5), row("a", 6), row("b", 6)])
    ev = walk_forward_ev(t, prior_trades=0)
    assert ev.tolist()[:2] == [0.0, 0.0]          # nothing had exited yet
    assert ev.iloc[2] == 0.0                      # a trade exiting ON the signal day is not counted
    assert ev.iloc[3] == 10.0                     # first trade exited day 2 < day 5; second exits day 5 (not before)
    assert ev.iloc[4] == 3.0                      # both known: (10 - 4) / 2
    assert ev.iloc[5] == 0.0                      # other strategy's history is not used


def test_walk_forward_ev_shrinks_toward_zero():
    t = pd.DataFrame([row("a", 0, 1, ret=10.0), row("a", 3)])
    assert walk_forward_ev(t, prior_trades=4).iloc[1] == pytest.approx(10.0 / 5)


def test_order_by_ev_beats_raw_score():
    t = pd.DataFrame([row("weak", 5, symbol="W", score=90), row("strong", 5, symbol="S", score=55)])
    t["ev"] = [0.05, 0.40]
    assert order_candidates(t, rank="score")["symbol"].tolist() == ["W", "S"]
    assert order_candidates(t, rank="ev")["symbol"].tolist() == ["S", "W"]


def test_atr_tie_break_then_seeded_fallback():
    t = pd.DataFrame([row("a", 5, symbol=s, atr_pct=a) for s, a in [("A", 1.0), ("B", 3.0), ("C", np.nan), ("D", 2.0)]])
    assert order_candidates(t, tie_break="atr")["symbol"].tolist() == ["B", "D", "A", "C"]   # NaN last
    assert order_candidates(t)["symbol"].tolist() == ["A", "B", "C", "D"]
    orders = {tuple(order_candidates(t, tie_seed=k)["symbol"]) for k in range(10)}
    assert len(orders) > 1


def test_invalid_options_rejected():
    t = pd.DataFrame([row("a", 0)])
    with pytest.raises(ValueError):
        order_candidates(t, rank="magic")
    with pytest.raises(ValueError):
        order_candidates(t, tie_break="magic")


# --- budget selection -------------------------------------------------------------------------
def trade(sym, sig=0, entry=1, exit_=3, score=60):
    return {"symbol": sym, "strategy": "s", "signal_date": D[sig], "entry_date": D[entry],
            "exit_date": D[exit_], "status": "CLOSED", "ref_close": 100.0, "initial_stop": 90.0,
            "entry_price": 100.0, "exit_price": 101.0, "exit_reason": "time", "final_score": score,
            "return_pct": 1.0, "atr_pct": 2.0}


def prices(*syms):
    return {s: pd.DataFrame({"Close": [100.0] * len(D)}, index=D) for s in syms}


def cfg(**kw):
    return PortfolioConfig(**{**dict(capital=1_000_000, top_n=3, risk_per_trade=0.01, max_position_pct=0.2,
                                     cost=0.0), **kw})


def test_budget_takes_every_signal_and_splits_risk():
    syms = [f"S{i}" for i in range(6)]
    res = simulate_portfolio(pd.DataFrame([trade(s) for s in syms]), prices(*syms), cfg(select="budget"))
    taken = res.ledger[res.ledger["action"] == "taken"]
    assert len(taken) == 6
    # budget 3% of 1,000,000 = 30,000 over 6 signals = 5,000 risk each / 10 per share = 500 shares
    assert (taken["qty"] == 500).all()


def test_budget_on_a_quiet_day_is_full_risk_like_top_n():
    syms = ["A", "B"]
    t = pd.DataFrame([trade(s) for s in syms])
    b = simulate_portfolio(t, prices(*syms), cfg(select="budget")).ledger["qty"].tolist()
    n = simulate_portfolio(t, prices(*syms), cfg()).ledger["qty"].tolist()
    assert b == n == [1000, 1000]   # 1% of 1,000,000 / 10 per share; budget/2 = 1.5% capped at 1%


def test_explicit_risk_budget_and_top_n_unchanged():
    syms = [f"S{i}" for i in range(4)]
    t = pd.DataFrame([trade(s) for s in syms])
    res = simulate_portfolio(t, prices(*syms), cfg(select="budget", risk_budget=0.02))
    assert (res.ledger["qty"] == 500).all()                      # 2% / 4 = 0.5% each
    res = simulate_portfolio(t, prices(*syms), cfg())
    assert res.ledger["action"].tolist().count("skipped: beyond top 3") == 1


def test_precomputed_ev_survives_slicing():
    early = [trade("A", sig=0, entry=1, exit_=2)]
    late = [trade("B", sig=6, entry=7, exit_=9, score=90), trade("C", sig=6, entry=7, exit_=9, score=55)]
    t = pd.DataFrame(early + late).assign(ev=[0.0, 0.1, 0.5])
    res = simulate_portfolio(t[t["signal_date"] >= D[6]], prices("A", "B", "C"), cfg(top_n=1, rank="ev"))
    assert dict(zip(res.ledger["symbol"], res.ledger["action"]))["C"] == "taken"


# --- equal sizing / select all --------------------------------------------------------------------
def test_equal_sizing_ignores_stop_distance():
    t = pd.DataFrame([trade("A"), {**trade("B"), "initial_stop": 99.0}])   # stops 10% and 1% away
    res = simulate_portfolio(t, prices("A", "B"), cfg(sizing="equal", position_pct=0.05))
    assert res.ledger["qty"].tolist() == [500, 500]                        # 5% of 1,000,000 / 100 each


def test_equal_sizing_respects_max_position_cap():
    res = simulate_portfolio(pd.DataFrame([trade("A")]), prices("A"), cfg(sizing="equal", position_pct=0.5))
    assert res.ledger["qty"].iloc[0] == 2000                                # capped at 20%


def test_select_all_fills_in_rank_order_until_cash_runs_out():
    syms = [f"S{i}" for i in range(8)]
    t = pd.DataFrame([trade(s, score=90 - i) for i, s in enumerate(syms)])
    res = simulate_portfolio(t, prices(*syms), cfg(select="all", sizing="equal", position_pct=0.2))
    acts = dict(zip(res.ledger["symbol"], res.ledger["action"]))
    assert [acts[s] for s in syms] == ["taken"] * 5 + ["skipped: no cash"] * 3
