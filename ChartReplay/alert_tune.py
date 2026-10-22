"""Tune the assist entries x exits (no detector thresholds) on 2018-2022, look at 2023+ once for the top configs.

Fixed before the run (2026-10-20):
  entries  GO_all (re-enter after each exit), GO_first (first GO of the day), TREND_first, BREAK_all,
           GO_first_late (first GO from 10:00)
  exits    trail at scale 1 / 1.5 / 2 / 3 x the live (+18 / -27 pts), with and without the 9/21-EMA exit;
           hold to 15:10 with the structure stop; fixed 30 / 60 / 120 min
  scoring  OPTION premium points on the ATM contract of the next expiry (never the expiring one), Black-Scholes on
           India VIX at entry and exit -> includes time decay (matters for long holds). Stress cost 5.5 premium pts
           per round trip (= 10 Nifty pts x 0.55). Index gross shown too.
  ranking  train = 2018-2022 stress-net per trade, n >= 200
  pass     holdout (2023+) stress-net > 0, beats same-bar random direction (monthly t > 2),
           positive in >= 3 of 4 holdout years. One holdout look, logged. Changes after it -> forward data only.

  python alert_tune.py
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

import alert_backtest as ab
import data_source
import options
from assist import CFG, checklist, features, hm

STRESS_PREM = 5.5
TOP = 3
RNG = np.random.default_rng(7)


def precompute():
    t0 = time.time()
    spot = data_source.load(options.SPOT, "1m")
    days = pd.DatetimeIndex(spot.index.normalize().unique())
    days = days[days >= pd.Timestamp("2018-01-01")]
    if "smoke" in sys.argv:
        days = days[(days >= "2022-10-01") & (days <= "2023-03-31")]
    out = []
    for n_day, d in enumerate(days):
        i0 = int(spot.index.searchsorted(d))
        i1 = int(spot.index.searchsorted(d + pd.Timedelta(days=1)))
        if i0 == 0 or not ab.clean_day(spot.iloc[i0:i1], d):
            continue
        nb = spot.iloc[max(0, i0 - 60):i1]
        nb = nb[nb.index < d + hm("15:30")].copy()
        f = features(nb, d)
        if len(f) < 340:
            continue
        a = ab.arrays(f)
        states, alerts = ab.run_day(f, d, float(spot.iloc[i0 - 1]["Close"]))
        rows, idx, go = f.to_dict("index"), f.index, []
        for i in range(len(f) - 1):
            t = idx[i] + pd.Timedelta("1min")
            if t - d < hm(CFG["setup_from"]) or t - d > hm(CFG["setup_until"]):
                continue
            reg, raw = states[i]
            if reg not in ("up", "down"):
                continue
            side = "CE" if reg == "up" else "PE"
            if checklist(side, rows[idx[i]], ab._Det(d, reg, raw), ab.SNAP, ab._book(), t)["verdict"] == "GO":
                go.append((i, side))
        a["idx"] = idx
        out.append({"day": d, "a": a, "go": go,
                    "trend": [(i, s) for i, k, s in alerts if k == "TREND"],
                    "break": [(i, s) for i, k, s in alerts if k == "BREAKOUT"]})
        if n_day % 200 == 0:
            print(f"  precompute {d.date()}  {time.time() - t0:.0f}s", flush=True)
    return out


def sim(a, j, side, stop, ex):
    """Exit per config ex. Returns (exit_index, exit_price, gross_index_pts)."""
    O, H, L, C, e9, e21, tod = a["O"], a["H"], a["L"], a["C"], a["e9"], a["e21"], a["tod"]
    n, ce = len(O), side == "CE"
    sg, entry = (1 if ce else -1), O[j]
    hwm, armed = entry, False
    arm, gap = ab.ARM * ex.get("scale", 1), ab.GAP_PTS * ex.get("scale", 1)
    for k in range(j, n):
        if tod[k] >= ab.EXIT_AT or ("fixed" in ex and k >= j + ex["fixed"]):
            return k, O[k], sg * (O[k] - entry)
        if "fixed" in ex:
            continue
        if ex.get("trail", True):
            if armed:
                lvl = hwm - sg * gap
                adverse = L[k] if ce else H[k]
                if sg * (adverse - lvl) <= 0:
                    px = O[k] if sg * (O[k] - lvl) <= 0 else lvl
                    return k, px, sg * (px - entry)
            fav = H[k] if ce else L[k]
            if sg * (fav - hwm) > 0:
                hwm = fav
            if sg * (hwm - entry) >= arm:
                armed = True
        if k + 1 < n:
            if sg * (C[k] - stop) < 0:
                return k + 1, O[k + 1], sg * (O[k + 1] - entry)
            if ex.get("ema", True) and ((ce and C[k] < e9[k] < e21[k]) or (not ce and C[k] > e9[k] > e21[k])):
                return k + 1, O[k + 1], sg * (O[k + 1] - entry)
    return n - 1, C[-1], sg * (C[-1] - entry)


ENTRIES = {
    "GO_all": lambda D: (D["go"], True),
    "GO_first": lambda D: (D["go"][:1], False),
    "TREND_first": lambda D: (D["trend"][:1], False),
    "BREAK_all": lambda D: (D["break"], True),
    "GO_first_late": lambda D: ([g for g in D["go"] if D["a"]["tod"][g[0]] >= 10 * 60][:1], False),
}
EXITS = {f"trail x{s}{'' if e else ' noEMA'}": {"scale": s, "ema": e} for s in (1, 1.5, 2, 3) for e in (True, False)}
EXITS |= {"hold to 15:10 (stop only)": {"trail": False, "ema": False}}
EXITS |= {f"fixed {m}m": {"fixed": m} for m in (30, 60, 120)}


def trades_for(days, ent, ex, random_dir=False):
    rows = []
    for D in days:
        a = D["a"]
        sigs, reenter = ENTRIES[ent](D)
        busy = -1
        for i, side in sigs:
            if i <= busy or i + 1 >= len(a["O"]):
                continue
            if random_dir:
                side = "CE" if RNG.random() < 0.5 else "PE"
            stop = a["lo"][i] if side == "CE" else a["hi"][i]
            k, px, g = sim(a, i + 1, side, stop, ex)
            rows.append((D["day"], a["idx"][i + 1], a["idx"][min(k, len(a["idx"]) - 1)], side, a["O"][i + 1], px, g))
            busy = k
            if not reenter:
                break
    return pd.DataFrame(rows, columns=["day", "t0", "t1", "side", "s0", "s1", "gross"])


def premium(tr: pd.DataFrame) -> pd.DataFrame:
    """Black-Scholes premium change on the ATM contract of the next (non-expiring) expiry."""
    if not len(tr):
        return tr.assign(prem=[])
    cal = options.expiry_calendar().index
    t0, t1 = pd.DatetimeIndex(tr["t0"]), pd.DatetimeIndex(tr["t1"])
    e_i = cal.searchsorted(t0.normalize(), side="right")          # first expiry AFTER today (never the expiring one)
    e = cal[np.minimum(e_i, len(cal) - 1)]
    K = (tr["s0"] / options.STEP).round() * options.STEP
    T0 = ((e + options.EXPIRY_TIME) - t0).total_seconds().to_numpy() / (365 * 86400.0)
    T1 = ((e + options.EXPIRY_TIME) - t1).total_seconds().to_numpy() / (365 * 86400.0)
    v0, v1 = options.vix_at(t0) / 100, options.vix_at(t1) / 100
    out = tr.copy()
    for typ in ("CE", "PE"):
        m = (tr["side"] == typ).to_numpy()
        if m.any():
            p0 = options.price(tr["s0"].to_numpy()[m], K.to_numpy()[m], T0[m], v0[m], typ)
            p1 = options.price(tr["s1"].to_numpy()[m], K.to_numpy()[m], T1[m], v1[m], typ)
            out.loc[m, "prem"] = p1 - p0
    return out


def score(tr, ctl=None):
    if not len(tr):
        return {"n": 0}
    net = tr["prem"] - STRESS_PREM
    y = pd.to_datetime(tr["day"]).dt.year
    r = {"n": len(tr), "idx_gross": tr["gross"].mean(), "prem_gross": tr["prem"].mean(), "net_stress": net.mean(),
         "t": ab.tstat(net), "years_pos": f"{(net.groupby(y).mean() > 0).sum()}/{y.nunique()}",
         "rs_per_trade": net.mean() * 65}
    if ctl is not None and len(ctl):
        mon = lambda x: pd.to_datetime(x["day"]).dt.to_period("M")
        diff = tr["prem"].groupby(mon(tr)).mean() - ctl["prem"].groupby(mon(ctl)).mean()
        r["vs_randdir"] = tr["prem"].mean() - ctl["prem"].mean()
        r["vs_randdir_t"] = ab.tstat(diff.dropna())
    return r


def main():
    days = precompute()
    train = [D for D in days if D["day"].year <= 2022]
    hold = [D for D in days if D["day"].year >= 2023]
    print(f"\n{len(train)} train days, {len(hold)} holdout days")
    res = []
    for ent in ENTRIES:
        for exn, ex in EXITS.items():
            r = score(premium(trades_for(train, ent, ex)))
            res.append({"entry": ent, "exit": exn, **r})
    res = pd.DataFrame(res)
    ab.OUT = ab.OUT / "smoke" if "smoke" in sys.argv else ab.OUT
    ab.OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(ab.OUT / "tune_train.csv", index=False)
    pd.set_option("display.width", 220)
    print("\nTRAIN 2018-2022 (premium pts per trade, stress cost 5.5; best first)")
    ok = res[res["n"] >= (5 if "smoke" in sys.argv else 200)].sort_values("net_stress", ascending=False)
    print(ok.round(2).to_string(index=False))
    print(f"\n{(ok['net_stress'] > 0).sum()} of {len(ok)} configs positive on train after stress costs")

    top = ok.head(TOP)
    print(f"\nHOLDOUT 2023+ (one look, top {TOP} by train):")
    hv = []
    for r in top.itertuples():
        ex = EXITS[r.exit]
        tr = premium(trades_for(hold, r.entry, ex))
        ctl = pd.concat([premium(trades_for(hold, r.entry, ex, random_dir=True)) for _ in range(10)])
        h = score(tr, ctl)
        passed = h.get("net_stress", -1) > 0 and h.get("vs_randdir_t", 0) > 2 and int(h["years_pos"].split("/")[0]) >= 3
        hv.append({"entry": r.entry, "exit": r.exit, "train_net": r.net_stress, **h, "PASS": passed})
    hv = pd.DataFrame(hv)
    hv.to_csv(ab.OUT / "tune_holdout.csv", index=False)
    print(hv.round(2).to_string(index=False))
    log = ab.OUT / "views_log.csv"
    pd.DataFrame([{"when": pd.Timestamp.now(), "what": f"alert_tune holdout look 2023+, top {TOP}: " +
                   "; ".join(f"{a}|{b}" for a, b in zip(hv["entry"], hv["exit"])), "cfg": "entries x exits sweep"}]
                 ).to_csv(log, mode="a", header=not log.exists(), index=False)


if __name__ == "__main__":
    sys.exit(main())


