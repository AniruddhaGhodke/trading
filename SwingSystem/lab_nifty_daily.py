"""Nifty INDEX dip-buying on daily bars - the family that passed on stocks, tested on the index itself.

Fixed before the run (2026-10-20):
  data      Yahoo ^NSEI daily 2007-09 .. 2026-10, frozen in data/lab/nifty_daily_2007.csv (2007-10 opens are bad,
            so trades are at CLOSES: enter at the signal day's close; robustness: enter at the NEXT day's close)
  long only, one position at a time, max hold 10 days
  entries   dip N/X  : close <= (1 - X%) x highest close of the last N days   N 10/20/50, X 2/3/5/8
            rsi2 < L (5/10/15/25), also with close > SMA200
            K down closes in a row (3/4/5)
            close < lower Bollinger(20, 2), also with close > SMA200
            IBS (close-low)/(high-low) < 0.2, also with close > SMA200
  exits     close > SMA5 | RSI2 > 70 | fixed 3 / 5 / 10 days (all also capped at 10 days)
  costs     round trip, futures: base 0.04%, stress 0.08% (brokerage + STT + exchange + ~2-4 pts slippage)
  control   matched random entries: same number of trades, same holding days, random entry days in the same
            period (long) - removes Nifty's upward drift. 50 draws.
  split     train 2008-2018 (rank), holdout 2019-2026 (top 5 by train, ONE look, logged)
  pass      holdout stress-net > 0, beats the control per trade with t > 2, positive in >= 5 of 8 holdout years,
            and the next-day-close variant also positive.

  python lab_nifty_daily.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

LAB = Path(__file__).resolve().parent / "data" / "lab"
OUT = LAB / "nifty_daily"
COST = {"base": 0.04, "stress": 0.08}
MAXHOLD = 10
TRAIN_END, HOLD_FROM = "2018-12-31", "2019-01-01"
RNG = np.random.default_rng(2026)
DRAWS = 50


def rsi(c, n):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def load():
    d = pd.read_csv(LAB / "nifty_daily_2007.csv", index_col=0, parse_dates=True)
    c = d["Close"]
    d["sma5"], d["sma200"] = c.rolling(5).mean(), c.rolling(200).mean()
    d["rsi2"] = rsi(c, 2)
    m, s = c.rolling(20).mean(), c.rolling(20).std()
    d["bbl"] = m - 2 * s
    d["ibs"] = (c - d["Low"]) / (d["High"] - d["Low"]).replace(0, np.nan)
    dn = (c < c.shift()).astype(int)
    d["down_run"] = dn.groupby((dn == 0).cumsum()).cumsum()
    for n in (10, 20, 50):
        d[f"hh{n}"] = c.rolling(n).max()
    return d.dropna(subset=["sma200"])


def entries(d):
    c, up = d["Close"], d["Close"] > d["sma200"]
    E = {}
    for n in (10, 20, 50):
        for x in (2, 3, 5, 8):
            E[f"dip{n}_{x}%"] = c <= (1 - x / 100) * d[f"hh{n}"]
    for L in (5, 10, 15, 25):
        E[f"rsi2<{L}"] = d["rsi2"] < L
        E[f"rsi2<{L}+sma200"] = (d["rsi2"] < L) & up
    for k in (3, 4, 5):
        E[f"down{k}"] = d["down_run"] >= k
    E["bb_lower"] = c < d["bbl"]
    E["bb_lower+sma200"] = (c < d["bbl"]) & up
    E["ibs<0.2"] = d["ibs"] < 0.2
    E["ibs<0.2+sma200"] = (d["ibs"] < 0.2) & up
    return E


EXITS = ["close>sma5", "rsi2>70", "fixed3", "fixed5", "fixed10"]


def run(d, sig, ex, delay=0):
    """Trades as (entry_i, exit_i). delay=1 -> enter at the next day's close."""
    c, sma5, r2 = d["Close"].to_numpy(), d["sma5"].to_numpy(), d["rsi2"].to_numpy()
    s = sig.to_numpy()
    n, out, i = len(c), [], 0
    while i < n:
        if not s[i]:
            i += 1
            continue
        e = i + delay
        if e >= n - 1:
            break
        fixed = int(ex[5:]) if ex.startswith("fixed") else MAXHOLD
        x = None
        for k in range(e + 1, min(n, e + 1 + min(fixed, MAXHOLD))):
            if (ex == "close>sma5" and c[k] > sma5[k]) or (ex == "rsi2>70" and r2[k] > 70):
                x = k
                break
        if x is None:
            x = min(n - 1, e + min(fixed, MAXHOLD))
        out.append((e, x))
        i = x + 1
    return out


def stats(d, tr, cost):
    c = d["Close"].to_numpy()
    if not tr:
        return None
    ret = np.array([(c[x] / c[e] - 1) * 100 - cost for e, x in tr])
    hold = np.array([x - e for e, x in tr])
    return ret, hold


def control(d, tr, cost, lo, hi):
    """Matched random entries in index range [lo, hi): same holding days. Returns mean control return per trade."""
    c = d["Close"].to_numpy()
    means = []
    for _ in range(DRAWS):
        r = []
        for e, x in tr:
            h = x - e
            j = int(RNG.integers(lo, max(lo + 1, hi - h)))
            r.append((c[j + h] / c[j] - 1) * 100 - cost)
        means.append(np.mean(r))
    return np.array(means)


def evaluate(d, sig, ex, period, cost, delay=0):
    idx = d.index
    lo, hi = (0, int(idx.searchsorted(pd.Timestamp(TRAIN_END), side="right"))) if period == "train" else \
        (int(idx.searchsorted(pd.Timestamp(HOLD_FROM))), len(idx))
    sub = sig.copy()
    sub.iloc[:lo] = False
    sub.iloc[hi:] = False
    tr = [t for t in run(d, sub, ex, delay) if t[1] < hi]
    if len(tr) < 5:
        return {"n": len(tr)}
    ret, hold = stats(d, tr, cost)
    ctl = control(d, tr, cost, lo, hi)
    yrs = pd.Series(ret, index=[idx[e].year for e, _ in tr]).groupby(level=0).sum()
    se = ret.std(ddof=1) / np.sqrt(len(ret))
    return {"n": len(tr), "per_year": round(len(tr) / ((hi - lo) / 248), 1), "avg%": ret.mean(), "win%": (ret > 0).mean() * 100,
            "hold": hold.mean(), "ctl%": ctl.mean(), "edge%": ret.mean() - ctl.mean(),
            "t_edge": (ret.mean() - ctl.mean()) / se if se > 0 else np.nan, "t": ret.mean() / se if se > 0 else np.nan,
            "years_pos": f"{(yrs > 0).sum()}/{len(yrs)}", "total%": ret.sum()}


def main():
    d = load()
    E = entries(d)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for en, sig in E.items():
        for ex in EXITS:
            r = evaluate(d, sig, ex, "train", COST["stress"])
            rows.append({"entry": en, "exit": ex, **r})
    tr = pd.DataFrame(rows)
    tr.to_csv(OUT / "train.csv", index=False)
    pd.set_option("display.width", 220)
    ok = tr[tr["n"] >= 30].sort_values("t_edge", ascending=False)
    print(f"TRAIN 2008-2018, stress cost {COST['stress']}% round trip; {len(ok)} configs with >= 30 trades")
    print(f"  beat the random control: {(ok['edge%'] > 0).sum()} / {len(ok)};  t_edge > 2: {(ok['t_edge'] > 2).sum()};"
          f"  positive after costs: {(ok['avg%'] > 0).sum()}")
    print(ok.head(15).round(2).to_string(index=False))

    top = ok.head(5)
    hv = []
    for r in top.itertuples():
        h = evaluate(d, E[r.entry], r.exit, "hold", COST["stress"])
        h1 = evaluate(d, E[r.entry], r.exit, "hold", COST["stress"], delay=1)
        hb = evaluate(d, E[r.entry], r.exit, "hold", COST["base"])
        ok_ = (h.get("avg%", -1) > 0 and h.get("t_edge", 0) > 2 and int(str(h.get("years_pos", "0/0")).split("/")[0]) >= 5
               and h1.get("avg%", -1) > 0)
        hv.append({"entry": r.entry, "exit": r.exit, "train_t_edge": r.t_edge, **h,
                   "next_close_avg%": h1.get("avg%"), "base_avg%": hb.get("avg%"), "PASS": ok_})
    hv = pd.DataFrame(hv)
    hv.to_csv(OUT / "holdout.csv", index=False)
    print("\nHOLDOUT 2019-2026 (one look, top 5 by train t_edge), stress costs:")
    print(hv.round(2).to_string(index=False))
    log = LAB / "holdout_log.csv"
    pd.DataFrame([{"date": pd.Timestamp.now().date(), "view": "nifty_daily holdout 2019-2026 top5: " +
                   "; ".join(f"{a}|{b}" for a, b in zip(hv["entry"], hv["exit"]))}]
                 ).to_csv(LAB / "nifty_daily" / "views_log.csv", mode="a",
                          header=not (LAB / "nifty_daily" / "views_log.csv").exists(), index=False)


if __name__ == "__main__":
    sys.exit(main())
