"""
Forward test == backtest.

1. Every call in (a copy of) the real journal still signals on its signal day, and
   re-simulating it reproduces the stored outcome.
2. End to end on a fresh journal: `scan` on a past day, `track` some days later, and the
   journal must equal backtest_strategy's trades for that signal day.
"""

import argparse
import sqlite3

import numpy as np
import pandas as pd
import pytest

import config
from conftest import needs_cache, needs_journal, read_prices
from core import journal
from core.backtest import backtest_strategy
from core.execution import simulate_trade
from core.regime import align_context, market_context
from strategies import REGISTRY


@needs_journal
def test_journal_calls_replay(index_df):
    calls = pd.read_sql("SELECT * FROM calls", sqlite3.connect(config.JOURNAL_DB))
    if calls.empty:
        pytest.skip("journal is empty")
    ctx = market_context(index_df)
    no_signal, mismatch, checked = [], [], 0
    for _, c in calls.iterrows():
        st = REGISTRY.get(c["strategy"])
        if st is None:
            continue
        raw = read_prices(c["symbol"])
        df = st.prepare(raw, align_context(ctx, raw.index))
        sd = pd.Timestamp(c["signal_date"])
        if sd not in df.index:
            continue
        i = df.index.get_loc(sd)
        if not df["signal"].iloc[i]:
            no_signal.append((c["signal_date"], c["symbol"], c["strategy"]))
        if c["status"] == "PENDING":
            continue
        df = df.copy()
        df.iloc[i, df.columns.get_loc("stop")] = c["stop"] * df["Close"].iloc[i] / c["ref_close"]
        if c["status"] == "OPEN":   # replay up to the bar the journal was last updated with
            df = df.iloc[: df.index.get_loc(pd.Timestamp(c["entry_date"])) + int(c["bars_held"])]
        t = simulate_trade(df, i, st.exit_plan)
        checked += 1
        same = t["status"] == c["status"] and (c["status"] == "SKIPPED"
                                               or np.isclose(t["return_pct"], c["return_pct"], atol=1e-6))
        if not same:
            mismatch.append((c["id"], c["symbol"], c["strategy"], c["status"], t["status"],
                             c["return_pct"], t.get("return_pct")))
    assert not no_signal, f"logged calls that no longer signal: {no_signal[:5]}"
    assert not mismatch, f"{len(mismatch)}/{checked} outcomes differ: {mismatch[:5]}"


UNIVERSE = ["RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS", "ITC.NS", "LT.NS",
            "SBIN.NS", "AXISBANK.NS", "TATASTEEL.NS", "ADANIENT.NS", "MARUTI.NS", "SUNPHARMA.NS",
            "BAJFINANCE.NS", "TITAN.NS", "HINDALCO.NS", "ONGC.NS", "WIPRO.NS", "M&M.NS", "DLF.NS",
            "JINDALSTEL.NS", "VEDL.NS", "TATAPOWER.NS", "BEL.NS", "GRASIM.NS"]
SCAN_DAYS = ["2025-04-07", "2024-06-04", "2025-01-27", "2023-03-15", "2022-06-17"]
HOLD_BARS = 20


@needs_cache
def test_scan_then_track_equals_backtest(index_df, tmp_path, monkeypatch, capsys):
    import main
    full = {s: read_prices(s) for s in UNIVERSE if (config.CACHE_DIR / f"{s}.csv").exists()}
    monkeypatch.setattr(config, "JOURNAL_DB", tmp_path / "journal.db")
    args = argparse.Namespace(refresh=False)

    def world(day):
        p = {s: df[df.index <= day] for s, df in full.items()}
        return p, market_context(index_df[index_df.index <= day])

    for day in map(pd.Timestamp, SCAN_DAYS):
        monkeypatch.setattr(main, "load_all", lambda refresh, d=day: world(d))
        main.cmd_scan(args)
        main.cmd_scan(args)   # re-running the same day must not log anything twice
        con = journal.connect()
        logged = journal.all_calls(con)
        if len(logged):
            break
    else:
        pytest.skip("no signals on any test day")
    assert not logged.duplicated(["signal_date", "symbol", "strategy"]).any()
    assert set(logged["signal_date"]) == {day}

    later = full["RELIANCE.NS"].index[full["RELIANCE.NS"].index > day][HOLD_BARS - 1]
    monkeypatch.setattr(main, "load_all", lambda refresh: world(later))
    main.cmd_track(args)
    got = journal.all_calls(con).set_index(["symbol", "strategy"])
    con.close()

    prices, ctx = world(later)
    for name in got.index.get_level_values("strategy").unique():
        bt = backtest_strategy(REGISTRY[name], prices, ctx, start=str(day.date()))
        bt = bt[bt["signal_date"] == day].set_index("symbol")
        mine = got.xs(name, level="strategy")
        assert set(mine.index) == set(bt.index), name
        for sym, j in mine.iterrows():
            b = bt.loc[sym]
            assert j["status"] == b["status"], (name, sym)
            if b["status"] != "SKIPPED":
                assert np.isclose(j["return_pct"], b["return_pct"], atol=1e-9), (name, sym)
                assert np.isclose(j["entry_price"], b["entry_price"]), (name, sym)
    capsys.readouterr()


def test_has_active_ignores_same_day_rescan(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOURNAL_DB", tmp_path / "j.db")
    con = journal.connect()
    call = {"signal_date": "2026-01-05", "symbol": "X.NS", "strategy": "s", "rank": 1, "score": 50,
            "final_score": 50, "regime": "BULL", "ref_close": 100, "stop": 90, "reason": ""}
    assert journal.insert_call(con, call)
    assert not journal.insert_call(con, call)                          # dup
    assert not journal.has_active(con, "X.NS", "s", before="2026-01-05")   # same day = re-scan
    assert journal.has_active(con, "X.NS", "s", before="2026-01-06")       # next day = already held
