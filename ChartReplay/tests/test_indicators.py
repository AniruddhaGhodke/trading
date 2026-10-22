"""Every indicator must be causal: the value at bar i, computed on full history, equals the value
computed with all bars after i removed. Catches centred windows, bfill, negative shifts, etc."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_source as ds
import indicators as ind


@pytest.fixture
def sample(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    days = pd.bdate_range("2024-01-01", periods=12)
    t = pd.DatetimeIndex([d + pd.Timedelta(minutes=555 + 5 * i) for d in days for i in range(75)])
    close = 100 + rng.normal(0, 0.3, len(t)).cumsum()
    df = pd.DataFrame({"Datetime": t, "Open": close + rng.normal(0, 0.1, len(t)), "Close": close,
                       "Volume": rng.integers(100, 1000, len(t))})
    df["High"] = df[["Open", "Close"]].max(axis=1) + 0.2
    df["Low"] = df[["Open", "Close"]].min(axis=1) - 0.2
    df.to_csv(tmp_path / "TEST.csv", index=False)
    monkeypatch.setattr(ds, "SOURCES", [tmp_path])
    for f in (ds.load, ds.load_interval, ind.compute_full):
        f.cache_clear()
    yield
    for f in (ds.load, ds.load_interval, ind.compute_full):
        f.cache_clear()


@pytest.mark.parametrize("key", list(ind.REGISTRY))
@pytest.mark.parametrize("interval", ["5m", "1h"])
def test_causal(sample, key, interval):
    params = tuple(float(d) for _, d in ind.REGISTRY[key].params)
    if key == "sma":
        params = (50.0,)
    df = ds.load_interval("TEST", interval)
    full = pd.DataFrame(ind.REGISTRY[key].fn(df, *params), index=df.index)
    for cut in (len(df) // 3, len(df) // 2, len(df) - 7):
        part = pd.DataFrame(ind.REGISTRY[key].fn(df.iloc[:cut], *params), index=df.index[:cut])
        pd.testing.assert_frame_equal(part, full.iloc[:cut], check_exact=False, rtol=1e-9)


def test_window_alignment_and_warmup(sample):
    items = ind.parse("ema:20,rsi:14,macd:12:26:9,vwap,vol,bb:20:2")
    w = ds.window("TEST", "2024-01-03 12:00", before=40, after=10)
    out = ind.for_window("TEST", "5m", items, w["lo"], w["hi"])
    for e in out:
        for line in e["lines"]:
            assert len(line["values"]) == len(w["bars"])
    full = ind.compute_full("TEST", "5m", "ema", (20.0,))
    assert full["ema"].iloc[:19].isna().all() and full["ema"].iloc[19:].notna().all()
    ema = out[0]["lines"][0]["values"]
    assert ema[w["cursor"]] == round(full["ema"].iloc[w["lo"] + w["cursor"]], 4)


def test_vwap_resets_daily_and_skipped_on_daily(sample):
    v = ind.compute_full("TEST", "5m", "vwap", ())["vwap"]
    df = ds.load_interval("TEST", "5m")
    first = df.index[df.index.time == pd.Timestamp("09:15").time()]
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    assert np.allclose(v.loc[first], tp.loc[first])
    w = ds.window("TEST", "2024-01-10", "1D")
    out = ind.for_window("TEST", "1D", ind.parse("vwap"), w["lo"], w["hi"])
    assert out[0]["skipped"]


@pytest.mark.parametrize("bad", ["foo:1", "ema:0", "ema:2.5", "ema:abc", "ema:1:2", "rsi:5000"])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        ind.parse(bad)


def test_parse_defaults():
    assert ind.parse("macd,bb:10") == [("macd", (12, 26, 9)), ("bb", (10.0, 2))]
