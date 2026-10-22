"""
Test fixtures. Every test runs against a COPY of the data in a temp folder: config paths are
redirected before anything is used, and any attempt to download prices fails the test.
The real journal, price cache and lab snapshot are only ever read (to make the copy).

Tests that need real price data are skipped if data/cache or data/lab is missing.
"""

import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

REAL_DATA = ROOT / "data"
HAVE_CACHE = (REAL_DATA / "cache" / "IDX_NSEI.csv").exists()
HAVE_JOURNAL = (REAL_DATA / "journal.db").exists()
HAVE_LAB = (REAL_DATA / "lab" / "manifest.json").exists()

needs_cache = pytest.mark.skipif(not HAVE_CACHE, reason="no data/cache (run main.py daily once)")
needs_journal = pytest.mark.skipif(not (HAVE_CACHE and HAVE_JOURNAL), reason="no journal/cache")
needs_lab = pytest.mark.skipif(not HAVE_LAB, reason="no lab snapshot (python lab.py freeze)")


@pytest.fixture(scope="session", autouse=True)
def sandbox(tmp_path_factory):
    """Redirect every data path in config (and modules that copied one) to a temp copy."""
    box = tmp_path_factory.mktemp("swing_data")
    if HAVE_CACHE:
        shutil.copytree(REAL_DATA / "cache", box / "cache")
    if HAVE_JOURNAL:
        shutil.copy(REAL_DATA / "journal.db", box / "journal.db")
    if (REAL_DATA / "nifty100.csv").exists():
        shutil.copy(REAL_DATA / "nifty100.csv", box / "nifty100.csv")
    if HAVE_LAB:   # only what the train dataset needs
        (box / "lab" / "prices").mkdir(parents=True)
        shutil.copy(REAL_DATA / "lab" / "manifest.json", box / "lab" / "manifest.json")
        for s in config.LAB_STOCKS + [config.INDEX_SYMBOL]:
            src = REAL_DATA / "lab" / "prices" / f"{s.replace('^', 'IDX_')}.csv"
            if src.exists():
                shutil.copy(src, box / "lab" / "prices" / src.name)

    mp = pytest.MonkeyPatch()
    mp.setattr(config, "DATA_DIR", box)
    mp.setattr(config, "CACHE_DIR", box / "cache")
    mp.setattr(config, "JOURNAL_DB", box / "journal.db")
    mp.setattr(config, "UNIVERSE_FILE", box / "nifty100.csv")
    mp.setattr(config, "LAB_DIR", box / "lab")
    mp.setattr(config, "LAB_PRICES_DIR", box / "lab" / "prices")
    from core import data, review
    mp.setattr(review, "REVIEW_DIR", box / "reviews")
    mp.setattr(review, "BASELINE_FILE", box / "reviews" / "baseline.json")

    def no_network(*a, **k):
        raise RuntimeError("tests must not download prices")
    mp.setattr(data, "_download", no_network)
    mp.setattr(data, "_cache_is_fresh", lambda symbol: True)
    yield box
    mp.undo()


def read_prices(symbol: str) -> pd.DataFrame:
    """Daily bars straight from the (copied) cache - no freshness logic, no network."""
    return pd.read_csv(config.CACHE_DIR / f"{symbol.replace('^', 'IDX_')}.csv", index_col=0, parse_dates=True)


@pytest.fixture(scope="session")
def index_df():
    return read_prices(config.INDEX_SYMBOL)
