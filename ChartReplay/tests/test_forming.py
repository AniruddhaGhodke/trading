"""Forming candles: partial states use only sub-bars up to k, end at the complete bar, and the
indicator value for state k equals the indicator computed on [history before the bar] + [partial k]."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_source as ds
import indicators as ind


def _ohlc(t, seed):
    rng = np.random.default_rng(seed)
    close = 100 + rng.normal(0, 0.3, len(t)).cumsum()
    df = pd.DataFrame({"Datetime": t, "Open": close + rng.normal(0, 0.1, len(t)), "Close": close,
                       "Volume": rng.integers(100, 1000, len(t))})
    df["High"] = df[["Open", "Close"]].max(axis=1) + 0.2
    df["Low"] = df[["Open", "Close"]].min(axis=1) - 0.2
    return df


@pytest.fixture
def sample(tmp_path, monkeypatch):
    days = pd.bdate_range("2024-01-01", periods=12)
    t5 = pd.DatetimeIndex([d + pd.Timedelta(minutes=555 + 5 * i) for d in days for i in range(75)])
    _ohlc(t5, 0).to_csv(tmp_path / "TEST.csv", index=False)
    one = tmp_path / "1m"; one.mkdir()
    t1 = pd.DatetimeIndex([days[-1] + pd.Timedelta(minutes=555 + i) for i in range(375)])  # 1m only on the last day
    _ohlc(t1, 1).to_csv(one / "TEST.csv", index=False)
    monkeypatch.setattr(ds, "SOURCES", [tmp_path])
    monkeypatch.setattr(ds, "SOURCES_1M", [one])
    for f in (ds.load, ds.load_interval, ind.compute_full):
        f.cache_clear()
    yield days
    for f in (ds.load, ds.load_interval, ind.compute_full):
        f.cache_clear()


def test_partials_are_cumulative_and_end_at_bar(sample):
    f = ds.forming("TEST", "1h", "2024-01-03 10:15", "5m")
    p, subs = pd.DataFrame(f["partial"]), ds.load_interval("TEST", "5m")
    subs = subs[(subs.index >= "2024-01-03 10:15") & (subs.index < "2024-01-03 11:15")]
    assert f["tick"] == "5m" and len(p) == 12
    assert np.allclose(p["high"], subs["High"].cummax().round(4)) and np.allclose(p["low"], subs["Low"].cummin().round(4))
    assert (p["open"] == p["open"].iloc[0]).all() and np.allclose(p["close"], subs["Close"].round(4))
    bar = ds.load_interval("TEST", "1h").loc["2024-01-03 10:15"]
    assert np.allclose(p.iloc[-1][["open", "high", "low", "close"]].to_numpy(float),
                       bar[["Open", "High", "Low", "Close"]].astype(float).round(4).to_numpy())
    assert p.iloc[-1]["volume"] == bar["Volume"]


@pytest.mark.parametrize("interval,t", [("1h", "2024-01-10 10:15"), ("15m", "2024-01-09 14:00"), ("1D", "2024-01-11")])
def test_partial_indicators_match_direct_computation(sample, interval, t):
    q = "ema:20,ema:9,sma:30,bb:20:2,vwap,vol,rsi:14,macd"
    items = ind.parse(q)
    f = ds.forming("TEST", interval, t, "5m")
    part = f.pop("_partials")
    got = ind.for_partials("TEST", interval, items, f["i"], part)
    df = ds.load_interval("TEST", interval)
    hist = df.iloc[:f["i"]][ds._COLS]
    for key, params in items:
        spec, eid = ind.REGISTRY[key], ind.entry_id(key, params)
        if spec.intraday_only and interval == "1D":
            assert eid not in got
            continue
        for k in range(len(part)):
            direct = pd.DataFrame(spec.fn(pd.concat([hist, part.iloc[[k]][ds._COLS]]), *params))
            for name, _, _ in spec.lines:
                v, d = got[eid][name][k], float(direct[name].iloc[-1])
                if v is None:
                    assert f["i"] < spec.warmup(*params) or not np.isfinite(d)
                else:
                    assert v == pytest.approx(d, abs=1e-3), (eid, name, k)
        full = ind.compute_full("TEST", interval, key, params)
        for name, _, _ in spec.lines:
            last = got[eid][name][-1]
            if last is not None:
                assert last == pytest.approx(full[name].iloc[f["i"]], abs=1e-3)


def test_auto_tick_uses_1m_where_available(sample):
    last_day = sample[-1]
    f = ds.forming("TEST", "5m", last_day + pd.Timedelta("10h"), "auto")
    assert f["tick"] == "1m" and len(f["partial"]) == 5
    f = ds.forming("TEST", "5m", sample[0] + pd.Timedelta("10h"), "auto")   # no 1m that day -> nothing to form
    assert f["tick"] == "5m" and f["partial"] == []
    f = ds.forming("TEST", "15m", sample[0] + pd.Timedelta("10h"), "1m")    # 1m requested, falls back to 5m
    assert f["tick"] == "5m" and len(f["partial"]) == 3
    assert "TEST" in ds.list_symbols() and ds.has_1m("TEST")
    assert len(ds.load_interval("TEST", "1m")) == 375


def test_forming_rejects_unknown_bar(sample):
    with pytest.raises(ValueError):
        ds.forming("TEST", "1h", "2024-01-03 10:20", "5m")


def test_partial_1m_coverage_falls_back_to_5m(sample, tmp_path):
    last_day = sample[-1]
    d1 = pd.read_csv(ds.SOURCES_1M[0] / "TEST.csv", parse_dates=["Datetime"])
    d1 = d1[~d1["Datetime"].between(last_day + pd.Timedelta("15h15min"), last_day + pd.Timedelta("15h28min"))]
    d1.to_csv(ds.SOURCES_1M[0] / "TEST.csv", index=False)       # mimic Angel's missing 15:15-15:28
    for f in (ds.load, ds.load_interval):
        f.cache_clear()
    f = ds.forming("TEST", "15m", last_day + pd.Timedelta("15h15min"), "auto")
    assert f["tick"] == "5m" and len(f["partial"]) == 3            # 1m incomplete -> 5m ticks
    f = ds.forming("TEST", "15m", last_day + pd.Timedelta("15h"), "auto")
    assert f["tick"] == "1m" and len(f["partial"]) == 15           # fully covered -> 1m ticks
