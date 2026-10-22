"""Indicator registry for the replay chart.

Formulas come from SwingSystem/core/indicators.py so the chart matches the scanner / lab exactly.
Every indicator is computed on the symbol's FULL history (no warm-up gap at the window edge) and must
be causal: the value at bar i may only use bars <= i (tests/test_indicators.py enforces this).

To add an indicator: write fn(df, *params) -> {line_name: Series} and add a Spec to REGISTRY.
"""
from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

import data_source

_SWING = Path(__file__).resolve().parent.parent / "SwingSystem" / "core" / "indicators.py"
_spec = importlib.util.spec_from_file_location("swing_indicators", _SWING)
si = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(si)


@dataclass(frozen=True)
class Spec:
    label: str
    fn: Callable[..., dict[str, pd.Series]]
    params: tuple[tuple[str, float], ...] = ()      # (name, default)
    pane: str = "price"                              # "price" = overlay, "own" = separate pane
    lines: tuple[tuple[str, str, str], ...] = ()     # (name, kind: line|hist_sign|hist_updown, color)
    levels: tuple[float, ...] = ()                   # horizontal guide lines (RSI 30/70)
    intraday_only: bool = False
    warmup: Callable[..., int] = field(default=lambda *p: 0)  # bars to blank at the start of history


def _ema(df, n):
    return {"ema": si.ema(df["Close"], int(n))}


def _sma(df, n):
    return {"sma": si.sma(df["Close"], int(n))}


def _bb(df, n, k):
    mid = si.sma(df["Close"], int(n))
    sd = df["Close"].rolling(int(n)).std(ddof=0)
    return {"upper": mid + k * sd, "basis": mid, "lower": mid - k * sd}


def _vwap(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    day = df.index.normalize()
    pv = (tp * df["Volume"]).groupby(day).cumsum()
    vol = df["Volume"].groupby(day).cumsum()
    return {"vwap": pv / vol.replace(0, np.nan)}


def _volume(df):
    return {"volume": df["Volume"], "_up": (df["Close"] >= df["Open"]).astype(float)}


def _rsi(df, n):
    return {"rsi": si.rsi(df["Close"], int(n))}


def _macd(df, fast, slow, signal):
    line, sig, hist = si.macd(df["Close"], int(fast), int(slow), int(signal))
    return {"macd": line, "signal": sig, "hist": hist}


REGISTRY: dict[str, Spec] = {
    "ema": Spec("EMA", _ema, (("length", 20),), lines=(("ema", "line", "#f5a623"),),
                warmup=lambda n: int(n) - 1),
    "sma": Spec("SMA", _sma, (("length", 200),), lines=(("sma", "line", "#e040fb"),),
                warmup=lambda n: int(n) - 1),
    "bb": Spec("Bollinger", _bb, (("length", 20), ("mult", 2)),
               lines=(("upper", "line", "#5c9ded"), ("basis", "line", "#90a4ae"), ("lower", "line", "#5c9ded")),
               warmup=lambda n, k: int(n) - 1),
    "vwap": Spec("VWAP", _vwap, lines=(("vwap", "line", "#00bcd4"),), intraday_only=True),
    "vol": Spec("Volume", _volume, lines=(("volume", "hist_updown", ""),)),
    "rsi": Spec("RSI", _rsi, (("length", 14),), pane="own", lines=(("rsi", "line", "#b388ff"),),
                levels=(30, 70), warmup=lambda n: int(n)),
    "macd": Spec("MACD", _macd, (("fast", 12), ("slow", 26), ("signal", 9)), pane="own",
                 lines=(("hist", "hist_sign", ""), ("macd", "line", "#2962ff"), ("signal", "line", "#ff6d00")),
                 warmup=lambda f, s, g: int(s) + int(g) - 2),
}


def registry_meta() -> list[dict]:
    return [{"key": k, "label": s.label, "params": [{"name": n, "default": d} for n, d in s.params],
             "pane": s.pane, "intraday_only": s.intraday_only} for k, s in REGISTRY.items()]


def parse(ind: str) -> list[tuple[str, tuple[float, ...]]]:
    """'ema:20,rsi:14,macd:12:26:9,vwap' -> [('ema', (20.0,)), ...]. Missing params use defaults."""
    out = []
    for item in filter(None, (x.strip() for x in ind.split(","))):
        key, *raw = item.split(":")
        if key not in REGISTRY:
            raise ValueError(f"unknown indicator {key!r}")
        spec = REGISTRY[key]
        if len(raw) > len(spec.params):
            raise ValueError(f"{key} takes {len(spec.params)} params")
        vals = []
        for (name, default), r in zip(spec.params, raw + [None] * len(spec.params)):
            v = default if r in (None, "") else float(r)
            if not math.isfinite(v) or v <= 0 or (name != "mult" and (v != int(v) or v > 1000)):
                raise ValueError(f"{key} {name} must be a positive {'number' if name == 'mult' else 'integer <= 1000'}")
            vals.append(v)
        out.append((key, tuple(vals)))
    return out


@lru_cache(maxsize=128)
def compute_full(symbol: str, interval: str, key: str, params: tuple[float, ...]) -> pd.DataFrame:
    df = data_source.load_interval(symbol, interval)
    spec = REGISTRY[key]
    out = pd.DataFrame(spec.fn(df, *params), index=df.index)
    w = spec.warmup(*params)
    if w > 0:
        out.iloc[:w] = np.nan
    return out


def entry_id(key: str, params: tuple[float, ...]) -> str:
    pid = ":".join(f"{p:g}" for p in params)
    return f"{key}:{pid}" if pid else key


def _clean(s: pd.Series) -> list:
    return [None if not math.isfinite(v) else round(v, 4) for v in s.astype(float).tolist()]


def for_window(symbol: str, interval: str, items: list[tuple[str, tuple[float, ...]]], lo: int, hi: int) -> list[dict]:
    """Indicator values aligned 1:1 with window bars [lo, hi). None = no value (warm-up / gap)."""
    result = []
    for key, params in items:
        spec = REGISTRY[key]
        pid = ":".join(f"{p:g}" for p in params)
        entry = {"id": entry_id(key, params), "key": key, "label": f"{spec.label} {pid.replace(':', ' ')}".strip(),
                 "pane": spec.pane, "levels": list(spec.levels), "lines": []}
        if spec.intraday_only and interval == "1D":
            entry["skipped"] = f"{spec.label} is intraday only"
            result.append(entry)
            continue
        part = compute_full(symbol, interval, key, params).iloc[lo:hi]
        for name, kind, color in spec.lines:
            line = {"name": name, "kind": kind, "color": color, "values": _clean(part[name])}
            if kind == "hist_updown":
                line["up"] = [bool(x) for x in part["_up"].fillna(1).tolist()]
            entry["lines"].append(line)
        result.append(entry)
    return result


# Bars of history used when recomputing an indicator for a forming candle. Long enough that recursive
# indicators (EMA, Wilder RSI, MACD) match the full-history value to ~1e-9: (1 - 2/201)^2000 ~ 2e-9.
PARTIAL_TAIL = 2000


class _Stacked:
    """Looks like an OHLCV frame to the indicator functions, but each column is a (rows x K) DataFrame:
    K copies of the same history that differ only in the last row (partial state k of the forming bar).
    pandas rolling / ewm / diff / groupby work column-wise, so one call computes all K states."""

    def __init__(self, hist: pd.DataFrame, partials: pd.DataFrame):
        k = len(partials)
        self.index = hist.index.append(partials.index[:1])
        self._cols = {}
        for c in data_source._COLS:
            body = np.repeat(hist[c].to_numpy(float)[:, None], k, axis=1)
            last = partials[c].to_numpy(float)[None, :]
            self._cols[c] = pd.DataFrame(np.vstack([body, last]), index=self.index)

    def __getitem__(self, col: str) -> pd.DataFrame:
        return self._cols[col]


def for_partials(symbol: str, interval: str, items: list[tuple[str, tuple[float, ...]]], i: int,
                 partials: pd.DataFrame) -> dict:
    """Indicator values while bar i is still forming.

    partials = successive states of bar i (row k = bar built from its first k+1 sub-bars). Each value is
    computed on [completed bars before i] + [partial bar k] only - never on later sub-bars.
    Returns {indicator id: {line name: [value per k]}}.
    """
    df = data_source.load_interval(symbol, interval)
    stacked = _Stacked(df.iloc[max(0, i - PARTIAL_TAIL):i], partials)
    out = {}
    for key, params in items:
        spec = REGISTRY[key]
        if spec.intraday_only and interval == "1D":
            continue
        warm = i < spec.warmup(*params)
        res = spec.fn(stacked, *params)
        out[entry_id(key, params)] = {
            n: [None if warm or not math.isfinite(v) else round(v, 4) for v in res[n].iloc[-1].astype(float).tolist()]
            for n, _, _ in spec.lines
        }
    return out
