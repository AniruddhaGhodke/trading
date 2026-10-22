import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import data_source as ds


@pytest.fixture
def sample(tmp_path, monkeypatch):
    t = pd.date_range("2024-01-01 09:15", periods=75, freq="5min")  # one session, 09:15-15:25
    t = t.append(pd.date_range("2024-01-02 09:15", periods=75, freq="5min"))
    df = pd.DataFrame({"date": t, "open": range(150), "high": range(1, 151), "low": range(150),
                       "close": range(150), "volume": 1})
    df.to_csv(tmp_path / "TEST.csv", index=False)
    monkeypatch.setattr(ds, "SOURCES", [tmp_path])
    ds.load.cache_clear(); ds.load_interval.cache_clear()
    yield
    ds.load.cache_clear(); ds.load_interval.cache_clear()


def _epoch(s):
    return pd.Timestamp(s).value // 10**9


@pytest.mark.parametrize("interval,start,expect", [
    ("5m", "2024-01-01 10:02", "2024-01-01 09:55:00"),   # 10:00 bar closes 10:05 -> not yet visible
    ("5m", "2024-01-01 10:05", "2024-01-01 10:00:00"),
    ("1h", "2024-01-01 13:00", "2024-01-01 11:15:00"),   # 12:15 bar closes 13:15
    ("1D", "2024-01-02 13:00", "2024-01-01 00:00:00"),   # today's daily bar not closed yet
    ("1D", "2024-01-02 15:30", "2024-01-02 00:00:00"),
])
def test_only_closed_bars_visible(sample, interval, start, expect):
    w = ds.window("TEST", start, interval, before=5, after=10)
    assert w["start"] == expect
    assert all(e <= _epoch(start) for e in w["ends"][: w["cursor"] + 1])
    if w["cursor"] + 1 < len(w["ends"]):
        assert w["ends"][w["cursor"] + 1] > _epoch(start)


def test_session_open_start_shows_previous_close(sample):
    assert ds.window("TEST", "2024-01-02 09:15")["start"] == "2024-01-01 15:25:00"


def test_after_hours_start_snaps_to_last_bar_of_day(sample):
    assert ds.window("TEST", "2024-01-01 20:00")["start"] == "2024-01-01 15:25:00"


def test_clamped_to_data_range(sample):
    w = ds.window("TEST", "2020-01-01", before=50)
    assert w["cursor"] == 0
    w = ds.window("TEST", "2030-01-01", after=50)
    assert w["cursor"] == len(w["bars"]) - 1


def test_resample_aligns_to_session(sample):
    h = ds.load_interval("TEST", "1h")
    assert h.index[0] == pd.Timestamp("2024-01-01 09:15")
    assert h.iloc[0]["High"] == 12 and h.iloc[0]["Volume"] == 12
    assert h["End"].iloc[-1] == pd.Timestamp("2024-01-02 15:30")  # short 15:15 bin closes at 15:30
    d = ds.load_interval("TEST", "1D")
    assert len(d) == 2 and d.iloc[0]["Open"] == 0 and d.iloc[0]["Close"] == 74
    assert d["End"].iloc[0] == pd.Timestamp("2024-01-01 15:30")
