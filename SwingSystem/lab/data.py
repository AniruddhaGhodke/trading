"""
Frozen price snapshot for research. `data/cache` is re-downloaded (and
re-adjusted for dividends) every day, so research reads ONLY from the
snapshot in data/lab/prices - results stay reproducible between runs.
"""

import json
import shutil
from datetime import datetime

import pandas as pd

import config
from core import data


def freeze(refresh: bool = False):
    """Copy the full universe + index from the cache into the lab snapshot."""
    symbols = data.load_universe()
    data.load_prices(symbols + [config.INDEX_SYMBOL], refresh=refresh)
    if config.LAB_PRICES_DIR.exists():
        shutil.rmtree(config.LAB_PRICES_DIR)
    config.LAB_PRICES_DIR.mkdir(parents=True)
    copied = []
    for s in symbols + [config.INDEX_SYMBOL]:
        src = data._cache_path(s)
        if src.exists():
            shutil.copy(src, config.LAB_PRICES_DIR / src.name)
            copied.append(s)
    last = pd.read_csv(config.LAB_PRICES_DIR / data._cache_path(config.INDEX_SYMBOL).name,
                       index_col=0, parse_dates=True).index[-1]
    manifest = {"frozen_at": datetime.now().isoformat(timespec="seconds"),
                "last_bar": str(last.date()), "symbols": copied}
    (config.LAB_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"Froze {len(copied)} series up to {last.date()} into {config.LAB_PRICES_DIR}")


def manifest() -> dict:
    p = config.LAB_DIR / "manifest.json"
    if not p.exists():
        raise SystemExit("No lab snapshot. Run: python lab.py freeze")
    return json.loads(p.read_text())


def load(symbol: str) -> pd.DataFrame:
    p = config.LAB_PRICES_DIR / data._cache_path(symbol).name
    return pd.read_csv(p, index_col=0, parse_dates=True)
