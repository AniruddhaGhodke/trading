"""The live plug-ins of promoted lab configs must trade exactly like the lab measured them."""

import pandas as pd
import pytest

import config
from conftest import needs_lab
from core.backtest import backtest_strategy
from core.regime import market_context
from strategies import REGISTRY
from strategies.lab_promoted import PROMOTED


@needs_lab
@pytest.mark.parametrize("name", [s.name for s in PROMOTED if not s.research_only])
def test_lab_equals_live_plugin(name):
    from lab import data as lab_data
    from lab.engine import dataset, run
    from lab.families import parse_spec
    st = REGISTRY[name]
    real, _ = run(parse_spec(st.entry_spec), parse_spec(st.exit_spec), dataset("train"), control=0)
    ctx = market_context(lab_data.load(config.INDEX_SYMBOL))
    prices = {s: lab_data.load(s).loc[:config.TRAIN_END] for s in config.LAB_STOCKS}
    bt = backtest_strategy(st, prices, ctx, start=config.TRAIN_START)
    key = ["symbol", "signal_date"]
    a = real[real["status"] == "CLOSED"].set_index(key)["return_pct"].sort_index()
    b = bt[bt["status"] == "CLOSED"].set_index(key)["return_pct"].sort_index()
    assert len(a) > 0
    pd.testing.assert_series_equal(a, b, check_exact=False, rtol=1e-12)
