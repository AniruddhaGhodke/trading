"""
Lab engine: run one (entry config, exit config) on a dataset, with a
random-entry control, using the SAME simulator as the live system.

Datasets (fixed in config.py):
  train : LAB_STOCKS, signals TRAIN_START..TRAIN_END, data cut at TRAIN_END (no future bars at all)
  A     : LAB_STOCKS, signals from HOLDOUT_START (time holdout)
  B     : the other 90 stocks, signals from TRAIN_START (stock holdout)
  B24   : the other 90 stocks, signals from HOLDOUT_START (out of sample in both stocks and time)
"""

import numpy as np
import pandas as pd

import config
from core import data as core_data
from core.execution import Bars, simulate, with_dates
from core.stats import edge_vs_control
from lab import data as lab_data
from core.regime import align_context, market_context
from lab.families import ENTRY_BY_NAME, EXIT_BY_NAME, Ind, entry_signal
from core.plan import ExitPlan

WARMUP = 252


class Dataset:
    def __init__(self, name: str, symbols: list[str], start: str, end: str | None, cut: bool):
        self.name, self.start, self.end = name, pd.Timestamp(start), pd.Timestamp(end) if end else None
        self.inds = {}
        ctx = market_context(lab_data.load(config.INDEX_SYMBOL))
        for s in symbols:
            df = lab_data.load(s)
            if cut and self.end is not None:
                df = df[df.index <= self.end]
            if len(df) > WARMUP + 20:
                self.inds[s] = Ind(df, regime=align_context(ctx, df.index)["regime"])

    def window(self, I: Ind) -> np.ndarray:
        idx = I.c.index
        m = (np.arange(len(idx)) >= WARMUP) & (idx >= self.start)
        if self.end is not None:
            m &= idx <= self.end
        return m


_DATASETS = {}


def dataset(name: str) -> Dataset:
    if name not in _DATASETS:
        lab_data.manifest()
        others = [s for s in core_data.load_universe() if s not in config.LAB_STOCKS]
        spec = {
            "train": (config.LAB_STOCKS, config.TRAIN_START, config.TRAIN_END, True),
            "A": (config.LAB_STOCKS, config.HOLDOUT_START, None, False),
            "B": (others, config.TRAIN_START, None, False),
            "B24": (others, config.HOLDOUT_START, None, False),
        }[name]
        _DATASETS[name] = Dataset(name, *spec)
    return _DATASETS[name]


def run(entry: tuple[str, dict], exit_: tuple[str, dict], ds: Dataset,
        control: int = 3, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (trades, control_trades).
    Real trades: one position per stock at a time (so a 5-day RSI<30 streak is one trade).
    Control: `control` x as many entries on random bars, same stop and same exits.
    For rule-based exits the control uses a time exit with holds sampled from the
    real trades instead (a random bar often satisfies the exit rule immediately).
    """
    efam, eparams = ENTRY_BY_NAME[entry[0]], entry[1]
    xfam, xparams = EXIT_BY_NAME[exit_[0]], exit_[1]
    rng = np.random.default_rng(seed)
    real_rows, ctrl_jobs = [], []

    for sym, I in ds.inds.items():
        signal = entry_signal(efam, I, eparams).to_numpy(bool)
        stop_s, exit_sig, plan = xfam.fn(I, **xparams)
        atr = I.atr().to_numpy()
        stop = stop_s.to_numpy()
        df = I.df.assign(exit_signal=exit_sig.fillna(False))
        bars = Bars(df)
        ok = ds.window(I) & ~np.isnan(atr) & (stop < I.c.to_numpy())

        busy_until = -1
        n_taken = 0
        for i in np.flatnonzero(signal & ok):
            if i <= busy_until:
                continue
            t = simulate(bars, int(i), float(stop[i]), float(atr[i]), plan)
            if t["status"] == "CLOSED":
                busy_until = t["exit_i"]
            elif t["status"] == "OPEN":
                busy_until = len(df)
            n_taken += 1
            real_rows.append({"symbol": sym, **with_dates(t, df.index)})

        if control and n_taken:
            eligible = np.flatnonzero(ok)
            picks = rng.choice(eligible, size=min(control * n_taken, len(eligible)), replace=False)
            ctrl_jobs.append((sym, df, stop, atr, picks))

    real = pd.DataFrame(real_rows)
    if not control or real.empty:
        return real, pd.DataFrame()

    rule_exit = xfam.name == "rule"
    holds = real.loc[real["status"] == "CLOSED", "bars_held"].to_numpy() if rule_exit else None
    ctrl_rows = []
    for sym, df, stop, atr, picks in ctrl_jobs:
        bars = Bars(df.assign(exit_signal=False)) if rule_exit else Bars(df)
        for i in picks:
            p = ExitPlan(max_hold=int(rng.choice(holds))) if rule_exit and len(holds) else plan
            t = simulate(bars, int(i), float(stop[i]), float(atr[i]), p)
            ctrl_rows.append({"symbol": sym, **with_dates(t, df.index)})
    return real, pd.DataFrame(ctrl_rows)


def evaluate(entry, exit_, ds_name: str, control: int = 3) -> dict:
    """Run + summarise one config on one dataset."""
    ds = dataset(ds_name)
    real, ctrl = run(entry, exit_, ds, control=control)
    closed = real[real["status"] == "CLOSED"] if len(real) else real
    n = len(closed)
    out = {"trades": n}
    if n == 0:
        return out
    r = closed["return_pct"]
    years = (ds.end or closed["exit_date"].max()) - max(ds.start, closed["signal_date"].min())
    out.update({
        "win%": (r > 0).mean() * 100,
        "avg%": r.mean(),
        "hold": closed["bars_held"].mean(),
        "trades/stock/yr": n / len(ds.inds) / max(years.days / 365.25, 0.25),
        "pf": r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9),
    })
    if control:
        e = edge_vs_control(real, ctrl)
        # edge% = mean monthly (real - control), which is what t tests; edge_trade% = per-trade difference
        out.update({"edge%": e["edge_monthly"], "edge_trade%": e["edge"], "t": e["t"], "beat%": e["beat"]})
    out = {k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in out.items()}
    return out
