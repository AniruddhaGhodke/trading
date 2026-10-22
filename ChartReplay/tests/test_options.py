"""Options: Black-Scholes sanity, expiry calendar, and no-lookahead / forming consistency of contract bars."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_source as ds
import indicators as ind
import options as op

CACHES = lambda: (ds.load, ds.load_interval, ds._read, ind.compute_full, op._calendar, op._trading_days, op._real_file,
                  op.real_raw)


# ---------------------------------------------------------------- pricing
def test_put_call_parity():
    S, K, T, sig = 22600.0, np.array([22000.0, 22600.0, 23200.0]), 5 / 365, 0.14
    c, p = op.price(S, K, T, sig, "CE"), op.price(S, K, T, sig, "PE")
    assert np.allclose(c - p, S * np.exp(-op.Q * T) - K * np.exp(-op.R * T), atol=1e-6)


def test_intrinsic_at_expiry_and_monotonic_decay():
    assert op.price(22650.0, 22600.0, 0.0, 0.14, "CE") == pytest.approx(50.0)
    assert op.price(22650.0, 22600.0, -1e-6, 0.14, "PE") == pytest.approx(0.0)
    T = np.array([7, 3, 1, 0.1]) / 365
    v = op.price(22600.0, 22600.0, T, 0.14, "CE")
    assert (np.diff(v) < 0).all()                         # theta: ATM premium shrinks towards expiry


def test_implied_vol_roundtrip_and_greek_signs():
    S, K, T = 22600.0, np.array([22300.0, 22600.0, 22900.0]), 6 / 365
    for typ in ("CE", "PE"):
        p = op.price(S, K, T, 0.17, typ)
        assert np.allclose(op.implied_vol(p, S, K, T, typ), 0.17, atol=1e-4)
        g = op.greeks(S, K, T, 0.17, typ)
        assert ((g["delta"] > 0) if typ == "CE" else (g["delta"] < 0)).all()
        assert (g["theta"] < 0).all() and (g["gamma"] > 0).all() and (g["vega"] > 0).all()
    # delta matches a finite difference of the price
    d = (op.price(S + 1, K, T, 0.17, "CE") - op.price(S - 1, K, T, 0.17, "CE")) / 2
    assert np.allclose(d, op.greeks(S, K, T, 0.17, "CE")["delta"], atol=1e-3)


def test_symbols():
    s = op.make("2026-10-19", 22600, "CE")
    assert s == "NIFTY_20261019_22600CE" and op.parse(s) == (pd.Timestamp("2026-10-19"), 22600.0, "CE")
    assert op.parse("IDX_NSEI") is None and op.parse("RELIANCE.NS") is None


# ---------------------------------------------------------------- sample market
def _bars(t, start, vol, seed):
    rng = np.random.default_rng(seed)
    close = start + rng.normal(0, vol, len(t)).cumsum()
    df = pd.DataFrame({"Datetime": t, "Open": close + rng.normal(0, vol / 3, len(t)), "Close": close,
                       "Volume": 0})
    df["High"] = df[["Open", "Close"]].max(axis=1) + vol / 2
    df["Low"] = df[["Open", "Close"]].min(axis=1) - vol / 2
    return df


@pytest.fixture
def market(tmp_path, monkeypatch):
    # Mon 2024-01-01 .. ; Thursday 2024-01-11 is a weekly expiry; 2024-01-04 (Thu) is made a "holiday".
    days = pd.DatetimeIndex([d for d in pd.bdate_range("2024-01-01", periods=12) if d != pd.Timestamp("2024-01-04")])
    t1 = pd.DatetimeIndex([d + pd.Timedelta(minutes=555 + i) for d in days for i in range(375)])
    one = tmp_path / "1m"; one.mkdir()
    spot1 = _bars(t1, 21700, 4, 0)
    spot1.to_csv(one / "IDX_NSEI.csv", index=False)
    _bars(t1, 14, 0.02, 1).to_csv(one / "IDX_INDIAVIX.csv", index=False)
    # 5m file = resample of the 1m (a vendor 5m file), with a hole in the 1m on 2024-01-10 15:15-15:28
    s = spot1.set_index("Datetime")
    s5 = s.resample("5min", origin="start_day", offset="9h15min").agg(ds._AGG).dropna(subset=["Open"])
    s5.reset_index().to_csv(tmp_path / "IDX_NSEI.csv", index=False)
    last = pd.Timestamp("2024-01-10")
    spot1[~((spot1["Datetime"] >= last + pd.Timedelta("15h15min")) & (spot1["Datetime"] < last + pd.Timedelta("15h29min")))] \
        .to_csv(one / "IDX_NSEI.csv", index=False)
    monkeypatch.setattr(ds, "SOURCES", [tmp_path])
    monkeypatch.setattr(ds, "SOURCES_1M", [one])
    monkeypatch.setattr(op, "OPT_DIR", tmp_path / "options")
    for f in CACHES():
        f.cache_clear()
    yield days
    for f in CACHES():
        f.cache_clear()


def test_calendar_shifts_holiday_expiry(market):
    cal = op.expiry_calendar()
    assert pd.Timestamp("2024-01-03") in cal.index and pd.Timestamp("2024-01-04") not in cal.index   # Thu holiday -> Wed
    assert pd.Timestamp("2024-01-11") in cal.index and cal.loc["2024-01-11", "lot"] == 50


SYM = "NIFTY_20240111_21700CE"


@pytest.mark.parametrize("sym", [SYM, "NIFTY_20240111_21650PE"])
@pytest.mark.parametrize("interval,t", [("5m", "2024-01-09 10:15"), ("15m", "2024-01-10 14:00"), ("1h", "2024-01-08 11:15")])
def test_option_forming_ends_at_completed_bar(market, sym, interval, t):
    f = ds.forming(sym, interval, t, "1m")
    assert f["tick"] == "1m" and len(f["partial"]) == ds.MINUTES[interval]
    bar = ds.load_interval(sym, interval).loc[t]
    last = f["partial"][-1]
    assert [last[k] for k in ("open", "high", "low", "close")] == pytest.approx(
        bar[["Open", "High", "Low", "Close"]].astype(float).round(4).tolist())


def test_option_bars_valid_and_hole_falls_back_to_5m(market):
    df = ds.load_interval(SYM, "5m")
    assert (df["High"] >= df[["Open", "Close"]].max(axis=1)).all() and (df["Low"] <= df[["Open", "Close"]].min(axis=1)).all()
    hole = pd.Timestamp("2024-01-10 15:15")
    assert hole in df.index                                   # 5m premium exists where the 1m spot is missing
    subs, used = ds.sub_bars(SYM, "1m", hole, hole + pd.Timedelta("5min"))
    assert used == "5m"


def test_no_lookahead_in_premiums_and_chain(market, tmp_path):
    t = pd.Timestamp("2024-01-09 11:00")
    before = ds.load(SYM, "1m")
    before = before[before.index + pd.Timedelta("1min") <= t].copy()
    ch = op.chain(t, "2024-01-11", 3)
    # Change everything after t (spot and VIX), reload: nothing at or before t may change.
    for name, scale in (("IDX_NSEI", 1.05), ("IDX_INDIAVIX", 2.0)):
        p = tmp_path / "1m" / f"{name}.csv"
        d = pd.read_csv(p, parse_dates=["Datetime"])
        late = d["Datetime"] >= t
        d.loc[late, ["Open", "High", "Low", "Close"]] *= scale
        d.to_csv(p, index=False)
    for f in CACHES():
        f.cache_clear()
    after = ds.load(SYM, "1m")
    pd.testing.assert_frame_equal(before, after.loc[before.index])
    assert op.chain(t, "2024-01-11", 3) == ch


def test_real_contract_fills_gaps_and_wins_over_synthetic(market):
    p = op.real_path("2024-01-11", 21700, "CE")
    p.parent.mkdir(parents=True)
    pd.DataFrame({"Datetime": ["2024-01-10 09:20", "2024-01-10 09:23", "2024-01-10 15:30"], "Open": [100, 104, 90],
                  "High": [101, 106, 90], "Low": [99, 103, 90], "Close": [100, 105, 90], "Volume": [10, 5, 1],
                  "OI": [1000, 1200, 1300]}).to_csv(p, index=False)
    df = ds.load(SYM, "1m")
    assert op.source(SYM) == "REAL" and df.index[0] == pd.Timestamp("2024-01-10 09:20")
    assert pd.Timestamp("2024-01-10 15:30") not in df.index                   # post-close bar clipped
    gap = df.loc["2024-01-10 09:21"]
    assert gap["Close"] == 100 and gap["Volume"] == 0 and gap["High"] == 100   # flat, no trades
    ch = op.chain(pd.Timestamp("2024-01-10 09:24"), "2024-01-11", 20)
    row = next(r for r in ch["rows"] if r["symbol"] == SYM)
    assert row["source"] == "REAL" and row["ltp"] == 105 and row["oi"] == 1200 and row["iv"] is not None
    assert ch["lot"] == 50
