"""Portfolio simulation on hand-made trades: top-N, held stocks, cash limits, accounting, drawdown."""

import numpy as np
import pandas as pd
import pytest

from core.portfolio import PortfolioConfig, drawdown, simulate_portfolio, summary
from core.ranking import final_scores

D = pd.bdate_range("2024-01-01", periods=10)


def prices(**closes):
    """symbol=close (flat) or a list of closes per day."""
    return {s: pd.DataFrame({"Close": c if isinstance(c, list) else [c] * len(D)}, index=D)
            for s, c in closes.items()}


def trade(sym, sig, entry, exit_, score=60, entry_px=100.0, exit_px=110.0, stop=90.0,
          reason="time", status="CLOSED", strategy="s"):
    return {"symbol": sym, "strategy": strategy, "signal_date": D[sig], "entry_date": D[entry],
            "exit_date": D[exit_] if exit_ is not None else pd.NaT, "status": status,
            "ref_close": 100.0, "initial_stop": stop, "entry_price": entry_px, "exit_price": exit_px,
            "exit_reason": reason, "final_score": score, "return_pct": (exit_px / entry_px - 1) * 100}


def cfg(**kw):
    base = dict(capital=100_000, top_n=3, risk_per_trade=0.01, max_position_pct=0.20, cost=0.0)
    return PortfolioConfig(**{**base, **kw})


def actions(res):
    return dict(zip(res.ledger["symbol"], res.ledger["action"]))


def test_takes_only_top_n_by_score():
    t = pd.DataFrame([trade("A", 0, 1, 3, score=70), trade("B", 0, 1, 3, score=60), trade("C", 0, 1, 3, score=50)])
    res = simulate_portfolio(t, prices(A=100, B=100, C=100), cfg(top_n=2))
    assert actions(res) == {"A": "taken", "B": "taken", "C": "skipped: beyond top 2"}


def test_size_by_risk_and_position_cap():
    # risk 1% of 100k = 1000 / (100-90) = 100 shares = 10k (10%) < 20% cap
    res = simulate_portfolio(pd.DataFrame([trade("A", 0, 1, 3)]), prices(A=100), cfg())
    assert res.ledger["qty"].iloc[0] == 100
    # tight stop: 1000 / 1 = 1000 shares = 100k, capped at 20% = 200 shares
    res = simulate_portfolio(pd.DataFrame([trade("A", 0, 1, 3, stop=99)]), prices(A=100), cfg())
    assert res.ledger["qty"].iloc[0] == 200


def test_accounting_final_equity_equals_capital_plus_pnl():
    t = pd.DataFrame([trade("A", 0, 1, 3, exit_px=120), trade("B", 1, 2, 5, exit_px=95, score=70)])
    res = simulate_portfolio(t, prices(A=100, B=100), cfg(cost=0.0015))
    eq = res.equity["equity"]
    assert np.isclose(eq.iloc[-1], 100_000 + res.ledger["pnl"].sum())
    assert np.isclose(eq.iloc[-1], res.equity["cash"].iloc[-1])           # everything closed
    a = res.ledger.set_index("symbol")
    assert np.isclose(a.loc["A", "pnl_pct"], (120 * 0.9985 / (100 * 1.0015) - 1) * 100)


def test_marks_open_positions_to_market():
    t = pd.DataFrame([trade("A", 0, 1, None, status="OPEN", exit_px=np.nan, reason=None)])
    closes = [100, 100, 100, 105, 110, 110, 110, 110, 110, 120]
    res = simulate_portfolio(t, prices(A=closes), cfg())
    assert np.isclose(res.equity["equity"].iloc[-1], 100_000 + 100 * (120 - 100))
    assert res.ledger["action"].iloc[0] == "taken (open)"


def test_skips_stock_already_held():
    t = pd.DataFrame([trade("A", 0, 1, 6), trade("A", 2, 3, 6, strategy="other")])
    res = simulate_portfolio(t, prices(A=100), cfg())
    assert list(res.ledger["action"]) == ["taken", "skipped: already held"]


def test_one_row_per_stock_per_day():
    t = pd.DataFrame([trade("A", 0, 1, 3, score=70), trade("A", 0, 1, 3, score=60, strategy="other")])
    res = simulate_portfolio(t, prices(A=100), cfg())
    assert list(res.ledger["action"]) == ["taken", "dup: same stock, lower score"]


def test_buys_what_cash_allows_then_skips():
    big = cfg(capital=1_000, risk_per_trade=1.0, max_position_pct=1.0, cost=0.001)
    t = pd.DataFrame([trade("A", 0, 1, 5, score=70), trade("B", 0, 1, 5, score=60)])
    res = simulate_portfolio(t, prices(A=100, B=100), big)
    a = res.ledger.set_index("symbol")
    assert a.loc["A", "qty"] == 9                      # 10 wanted, 1000 / (100 x 1.001) -> 9
    assert a.loc["B", "action"] == "skipped: no cash"


@pytest.mark.parametrize("reason,b_action", [("exit_rule", "taken"), ("time", "skipped: no cash")])
def test_open_exits_free_cash_for_same_day_entries(reason, b_action):
    big = cfg(capital=1_000, risk_per_trade=1.0, max_position_pct=1.0)
    t = pd.DataFrame([trade("A", 0, 1, 3, reason=reason), trade("B", 2, 3, 6)])
    res = simulate_portfolio(t, prices(A=100, B=100), big)
    assert actions(res)["B"] == b_action


def test_gap_skip_uses_the_slot():
    t = pd.DataFrame([trade("A", 0, 1, 3, score=70, status="SKIPPED", reason="gap_up_chase"),
                      trade("B", 0, 1, 3, score=60)])
    res = simulate_portfolio(t, prices(A=100, B=100), cfg(top_n=1))
    assert actions(res) == {"A": "skipped at open (gap_up_chase)", "B": "skipped: beyond top 1"}


def test_drawdown_and_summary():
    eq = pd.Series([100, 120, 90, 130, 117], index=D[:5], dtype=float)
    assert np.allclose(drawdown(eq), [0, 0, -0.25, 0, -0.1])
    t = pd.DataFrame([trade("A", 0, 1, 3, exit_px=80)])
    s = summary(simulate_portfolio(t, prices(A=[100, 100, 90, 80, 80, 80, 80, 80, 80, 80]), cfg()))
    assert s["max_dd%"] < 0 and s["trades"] == 1 and s["win%"] == 0


def test_final_scores_confluence_counts_distinct_groups():
    c = pd.DataFrame({"symbol": ["A", "A", "A", "B"], "group": ["g1", "g1", "g2", "g1"],
                      "research": [False, False, False, False], "score": [60, 55, 50, 60],
                      "signal_date": [D[0]] * 4})
    assert final_scores(c, by=["signal_date"]).tolist() == [70, 65, 60, 60]


def test_tie_break_alphabetical_by_default_random_with_seed():
    t = pd.DataFrame([trade(s, 0, 1, 3, score=60) for s in "DCBA"])
    res = simulate_portfolio(t, prices(A=100, B=100, C=100, D=100), cfg(top_n=1))
    assert actions(res)["A"] == "taken"
    picks = {next(s for s, a in actions(simulate_portfolio(t, prices(A=100, B=100, C=100, D=100),
                                                           cfg(top_n=1, tie_seed=k))).items() if a == "taken")
             for k in range(20)}
    assert len(picks) > 1
