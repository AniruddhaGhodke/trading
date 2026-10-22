"""Backtest verdict: EDGE needs monthly t, a per-trade edge floor AND positive net return."""

import config
from core.stats import edge_verdict


def e(t, edge):
    return {"t": t, "edge": edge}


def test_all_three_pass():
    assert edge_verdict(e(3.0, 0.25), net_avg=0.4) == ("EDGE (candidate)", [])


def test_significant_but_tiny_edge_is_not_an_edge():   # rsi2_atr_1r: t 3.7, +0.02%/trade
    label, fails = edge_verdict(e(3.7, 0.02), net_avg=0.29)
    assert label == "weak" and len(fails) == 1 and "floor" in fails[0]


def test_losing_after_costs_is_not_an_edge():
    label, fails = edge_verdict(e(4.0, 0.30), net_avg=-0.05)
    assert label == "weak" and "after costs" in fails[0]


def test_low_t_is_not_an_edge():
    assert edge_verdict(e(1.0, 0.5), net_avg=1.0)[0] == "NO EDGE vs random"
    assert edge_verdict(e(-1.3, 0.38), net_avg=1.1)[0] == "NO EDGE vs random"


def test_floor_is_configurable():
    assert edge_verdict(e(3.0, 0.08), net_avg=0.2, min_edge=0.05)[0] == "EDGE (candidate)"
    assert edge_verdict(e(3.0, 0.08), net_avg=0.2)[0] == "weak"
    assert config.MIN_EDGE_PER_TRADE == 0.10


def test_nan_edge_fails():
    assert edge_verdict(e(0.0, float("nan")), net_avg=float("nan"))[0] == "NO EDGE vs random"
