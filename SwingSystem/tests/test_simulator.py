"""core.execution.simulate on hand-made bars: every fill/exit rule the README promises."""

import math

import pandas as pd
import pytest

import config
from core.execution import Bars, simulate
from core.plan import ExitPlan

BASE = [100, 101, 99, 100]   # signal bar: close 100; tests use stop 95, ATR 5


def bars(rows, exit_sig=None, **extra):
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"],
                      index=pd.date_range("2024-01-01", periods=len(rows)))
    if exit_sig is not None:
        df["exit_signal"] = exit_sig
    for k, v in extra.items():
        df[k] = v
    return Bars(df)


@pytest.fixture(autouse=True)
def no_costs(monkeypatch):
    monkeypatch.setattr(config, "COST_PER_SIDE", 0.0)


def sim(rows, plan=None, stop=95.0, atr=5.0, **kw):
    return simulate(bars(rows, **{k: v for k, v in kw.items() if k != "direction"}), 0, stop, atr,
                    plan or ExitPlan(max_hold=10), direction=kw.get("direction", 1))


def test_gap_through_stop_exits_at_open():
    t = sim([BASE, [100, 102, 99, 101], [90, 92, 88, 91]])
    assert (t["status"], t["exit_reason"], t["exit_price"]) == ("CLOSED", "stop_gap", 90)


def test_stop_and_target_in_same_bar_assumes_stop():
    t = sim([BASE, [100, 111, 94, 105]], ExitPlan(target_pct=0.10, max_hold=10))
    assert (t["exit_reason"], t["exit_price"]) == ("stop", 95)


def test_target_on_entry_bar():
    t = sim([BASE, [100, 111, 99, 105]], ExitPlan(target_pct=0.10, max_hold=10))
    assert t["exit_reason"] == "target" and math.isclose(t["exit_price"], 110)


def test_target_r_multiple():
    t = sim([BASE, [100, 101, 99, 100], [100, 111, 99, 108]], ExitPlan(target_r=2.0, max_hold=10))
    assert t["exit_reason"] == "target" and t["exit_price"] == 110   # entry 100 + 2 x risk 5


def test_target_gap_exits_at_open():
    t = sim([BASE, [100, 101, 99, 100], [112, 115, 111, 114]], ExitPlan(target_pct=0.10, max_hold=10))
    assert (t["exit_reason"], t["exit_price"]) == ("target_gap", 112)


def test_gap_up_more_than_one_atr_is_skipped():
    t = sim([BASE, [106, 107, 105, 106]])
    assert (t["status"], t["exit_reason"]) == ("SKIPPED", "gap_up_chase")


def test_gap_up_of_exactly_one_atr_is_taken():
    t = sim([BASE, [105, 107, 104, 106]], ExitPlan(max_hold=1))
    assert t["status"] == "CLOSED" and t["entry_price"] == 105


def test_open_at_or_below_stop_is_skipped():
    t = sim([BASE, [95, 96, 93, 95]])
    assert (t["status"], t["exit_reason"]) == ("SKIPPED", "gap_below_stop")


def test_breakeven_moves_stop_to_entry():
    t = sim([BASE, [100, 106, 99.5, 105.5], [104, 104, 99.5, 101]], ExitPlan(breakeven_r=1.0, max_hold=10))
    assert (t["exit_reason"], t["exit_price"], t["bars_held"]) == ("stop", 100, 2)


def test_exit_signal_exits_at_next_open():
    t = sim([BASE, [100, 102, 99, 101], [102, 103, 101, 102], [103, 104, 102, 103]],
            exit_sig=[False, True, False, False])
    assert (t["exit_reason"], t["exit_price"], t["bars_held"]) == ("exit_rule", 102, 2)


def test_time_stop_counts_entry_bar():
    t = sim([BASE] + [[100, 101, 99, 100]] * 5, ExitPlan(max_hold=3))
    assert (t["exit_reason"], t["bars_held"], t["exit_i"]) == ("time", 3, 3)


def test_open_at_end_of_data():
    t = sim([BASE, [100, 101, 99, 100], [100, 101, 99, 100]])
    assert (t["status"], t["bars_held"], t["exit_due"]) == ("OPEN", 2, None)


def test_pending_without_next_bar():
    assert sim([BASE])["status"] == "PENDING"


def test_rule_exit_on_last_bar_is_flagged_due():
    t = sim([BASE, [100, 102, 99, 101], [101, 103, 100, 102]], exit_sig=[False, False, True])
    assert (t["status"], t["exit_due"]) == ("OPEN", "rule exit at next open")


def test_time_exit_next_bar_is_flagged_due():
    t = sim([BASE, [100, 101, 99, 100], [100, 101, 99, 100]], ExitPlan(max_hold=3))
    assert (t["status"], t["exit_due"]) == ("OPEN", "time exit at next close")


def test_short_target_and_stop():
    t = sim([BASE, [100, 101, 95, 96], [96, 97, 89, 90]], ExitPlan(target_pct=0.10, max_hold=10),
            stop=105, direction=-1)
    assert (t["exit_reason"], t["exit_price"]) == ("target", 90)
    t = sim([BASE, [100, 106, 99, 104]], stop=105, direction=-1)
    assert (t["exit_reason"], t["exit_price"]) == ("stop", 105)


def test_intraday_session_end_exit():
    t = sim([BASE, [100, 101, 99, 100.5], [101, 102, 100, 101]], session=[1, 1, 1],
            session_last=[False, True, False])
    assert (t["exit_reason"], t["exit_price"]) == ("eod", 100.5)


def test_intraday_no_entry_in_next_session():
    t = sim([BASE, [100, 101, 99, 100.5]], session=[1, 2], session_last=[True, True])
    assert (t["status"], t["exit_reason"]) == ("SKIPPED", "next_session")


def test_costs_on_both_sides(monkeypatch):
    monkeypatch.setattr(config, "COST_PER_SIDE", 0.0015)
    t = sim([BASE, [100, 101, 99, 100], [100, 101, 99, 110]], ExitPlan(max_hold=2))
    assert math.isclose(t["return_pct"], (110 * 0.9985 / (100 * 1.0015) - 1) * 100)
    assert math.isclose(t["r_multiple"], t["return_pct"] / 100 * 100 / 5)
