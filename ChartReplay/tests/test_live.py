"""Live mode, offline: today's overlay + the quote minute -> /api/live, quote freshness, and the history-file guard."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import data_source as ds
import live
import server
from test_options import CACHES, market  # noqa: F401  (pytest fixture)

DAY = pd.Timestamp("2024-01-17")          # the "live" day: after the fixture's history (ends 2024-01-16)


def _minutes(n, start=DAY + pd.Timedelta("9h15min"), px=21800.0):
    t = pd.date_range(start, periods=n, freq="1min")
    return pd.DataFrame({"Open": px, "High": px + 2, "Low": px - 2, "Close": px + 1, "Volume": 0.0},
                        index=pd.DatetimeIndex(t, name="Datetime"))


@pytest.fixture
def today(market, monkeypatch):
    clock = {"t": DAY + pd.Timedelta("9h27min30s")}
    monkeypatch.setattr(live, "now", lambda: clock["t"])
    monkeypatch.setattr(live, "OVERLAY", {"IDX_NSEI": _minutes(12)})          # 09:15 .. 09:26 complete
    monkeypatch.setattr(live, "FORMING", {"IDX_NSEI": {"time": DAY + pd.Timedelta("9h27min"), "open": 21810.0,
                                                       "high": 21830.0, "low": 21790.0, "close": 21820.0,
                                                       "at": clock["t"]}})
    ds.invalidate(new_day=True)
    yield clock
    ds.invalidate(new_day=True)


def test_completion_rule_and_forming_bar(today):
    r = server.api_live({"symbol": "IDX_NSEI", "interval": "5m"})
    # 09:15 and 09:20 bins are complete at 09:27:30; the 09:25 bin (09:25, 09:26 + live 09:27) is forming.
    assert r["last_complete"] == int((DAY + pd.Timedelta("9h20min")).value // 10**9)
    f = r["forming"]
    assert f["time"] == int((DAY + pd.Timedelta("9h25min")).value // 10**9)
    assert f["open"] == 21800 and f["high"] == 21830 and f["low"] == 21790 and f["close"] == 21820
    assert r["quote"] is True


def test_stale_quote_is_ignored(today):
    today["t"] += pd.Timedelta(seconds=live.QUOTE_MAX_AGE + 5)
    assert live.ltp("IDX_NSEI") is None
    r = server.api_live({"symbol": "IDX_NSEI", "interval": "5m"})
    assert r["quote"] is False and r["forming"]["close"] == 21801        # completed minutes only
    # and a replay clock before the quote never sees it
    assert live.quote("IDX_NSEI", DAY + pd.Timedelta("9h20min")) is None


def test_live_rejects_other_symbols(today):
    with pytest.raises(server.ApiError):
        server.api_live({"symbol": "RELIANCE.NS", "interval": "5m"})


def test_persist_only_settled_minutes_and_no_gaps(today, tmp_path, monkeypatch):
    monkeypatch.setattr(live, "SWING", tmp_path / "swing")
    p = tmp_path / "swing" / "data" / "angel" / "1m" / "IDX_NSEI.csv"
    p.parent.mkdir(parents=True)
    _minutes(3, DAY - pd.Timedelta(days=1) + pd.Timedelta("15h27min")).to_csv(p)   # file up to yesterday 15:29
    th = live._Live()
    th._persist("IDX_NSEI", live.OVERLAY["IDX_NSEI"])
    saved = pd.read_csv(p, index_col=0, parse_dates=True)
    assert saved.index[-1] == DAY + pd.Timedelta("9h24min")       # 09:25, 09:26 are < 2 min old at 09:27:30
    # A file that is behind by more than a few days is left to the recorder (no gap is created).
    q = tmp_path / "swing" / "data" / "angel" / "1m" / "IDX_NSEI.csv"
    _minutes(3, DAY - pd.Timedelta(days=9) + pd.Timedelta("15h27min")).to_csv(q)
    th2 = live._Live()
    th2._persist("IDX_NSEI", live.OVERLAY["IDX_NSEI"])
    assert pd.read_csv(q, index_col=0, parse_dates=True).index[-1].date() == (DAY - pd.Timedelta(days=9)).date()


def test_quote_minutes_fill_the_gap_until_candles_arrive(today, monkeypatch):
    m = DAY + pd.Timedelta("9h27min")
    monkeypatch.setattr(live, "QBARS", {"IDX_NSEI": {
        DAY + pd.Timedelta("9h26min"): {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0},      # already a candle
        m: {"Open": 21810.0, "High": 21830.0, "Low": 21790.0, "Close": 21820.0}}})                # not yet fetched
    ov = live.overlay("IDX_NSEI")
    assert ov.index[-1] == m and ov.loc[m, "Close"] == 21820
    assert ov.loc[DAY + pd.Timedelta("9h26min"), "Close"] == 21801                                 # exchange candle wins
