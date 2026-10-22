"""Trend + 9-EMA pullback entries for intraday Nifty option BUYING: does the user's setup beat the costs?

Pre-registered 2026-10-20, before the first run. Changes after the holdout look -> forward (paper) data only.

Question: the checklist GO entry (trend + near the 9-EMA) has the right payoff shape (median -6 pts, max +180) but
~0 gross on average (alert_backtest.py). Does entering exactly AT the pullback, or on the RESUMPTION after it, with
better exits, turn that into a positive premium P&L after costs?

The user's two winning trades (16-Oct 10:12 CE, 19-Oct 12:14 PE) both touched the 1-minute 9-EMA and entered when
price resumed 2-6 minutes later -> RESUME1m is the user's setup (regression check: `python pullback_lab.py check`).

Trend = the live assist Detector regime (up/down confirmed), 09:30-12:00, max 2 entries/day, one position at a time.
ENTRIES (signal uses closed bars only)
  GO        live checklist GO, fill next open, stop = 5-bar swing (>= 1 ATR)              (baseline)
  TOUCH1m   limit at the previous bar's 1m 9-EMA while price is above it (CE); fills only if the bar trades
            0.5 pt THROUGH the level, at the level (or the open if it gapped through). stop = 5-bar swing of the
            bars before the fill, and at least 1 ATR below the limit.
  RESUME1m  a bar touches its 1m 9-EMA in the trend; within the next 10 bars a bar closes beyond the extreme of
            the bars since the touch (CE: above their high) and on the trend side of the 9-EMA -> fill next open.
            stop = pullback extreme since the touch -0.5, at least 1 ATR away. Cancelled if price closes
            through the 21-EMA first.
  TOUCH5m / RESUME5m  same with the 9-EMA of COMPLETED 5-minute bars.
  Each new entry in {first, first+clean}: first = first entry of each trend episode; clean = also no opposite
  trend confirmed earlier today and price beyond the opening range in the trade direction.
EXITS (1 lot -> no partial exits; checks start the bar after the fill; worst case first inside a bar)
  X1 live   Nifty close through the stop, close beyond 9-EMA beyond 21-EMA against, trail +18/-27 index pts
  X2 be_e9  close through the stop; at +18 index pts (= +10 premium) the stop moves to entry (intrabar);
            after that exit on a close beyond the 1m 9-EMA against the trade
  X3 be_e21 same, but the 21-EMA
  X4 be_2R  close-stop, breakeven at +18, target 2R (intrabar, at the level)
  all: time exit 15:10.
SCORING  premium points on the next expiry (never the expiring one), ONE STRIKE IN THE MONEY (delta ~0.55-0.6),
  Black-Scholes on India VIX at entry and exit (includes theta). Stress cost 5.5 premium pts per round trip.
RANKING  train 2018-2022, net stress per trade, n >= 200.
PASS     holdout 2023+ (one logged look, top 3 + the GO|X1 baseline as reference): net stress > 0, beats the
  same-bar random-direction control (monthly t > 2), >= 3 of 4 holdout years positive.
  NOTE: 2023+ was already looked at for GO and alert entries (alert_backtest, alert_tune), so a pass here only
  makes a rule a PAPER candidate for lab_alerts.py, not a proven edge.

  python pullback_lab.py smoke      quick run on Oct-2022..Mar-2023
  python pullback_lab.py check      regression: does RESUME1m fire on the user's two winners?
  python pullback_lab.py            full run -> SwingSystem/data/lab/pullback/
"""
from __future__ import annotations

import pickle
import sys
import time

import numpy as np
import pandas as pd

import alert_backtest as ab
import data_source
import options
from assist import CFG, checklist, features, hm

OUT = ab.OUT.parent / "pullback"
STRESS_PREM = 5.5
ARM = ab.ARM                     # +10 premium pts in index pts (~18.2)
THROUGH = 0.5                    # limit fills only if traded this far through
RESUME_BARS = 10
TOP = 3
RNG = np.random.default_rng(20261020)
REG = {"up": 1, "down": -1}


# ------------------------------------------------------------------ precompute (cached)
def ema5_known(spot: pd.DataFrame) -> pd.Series:
    """For each 1m bar: the 9-EMA of the 5-minute bars completed by the end of that bar."""
    c5 = spot["Close"].resample("5min", label="left").last().dropna()
    e5 = c5.ewm(span=9, adjust=False).mean()
    e5.index = e5.index + pd.Timedelta(minutes=4)          # known at the close of the 5th 1m bar
    return e5.reindex(spot.index, method="ffill")


def precompute(smoke=False):
    path = OUT / ("pre_smoke.pkl" if smoke else "pre.pkl")
    if path.exists():
        return pickle.loads(path.read_bytes())
    t0 = time.time()
    spot = data_source.load(options.SPOT, "1m")
    e5_all = ema5_known(spot)
    days = pd.DatetimeIndex(spot.index.normalize().unique())
    days = days[days >= pd.Timestamp("2018-01-01")]
    if smoke:
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
        states, _ = ab.run_day(f, d, float(spot.iloc[i0 - 1]["Close"]))
        a["reg"] = np.array([REG.get(s[0], 0) for s in states], int)
        a["atr"] = f["atr"].to_numpy(float)
        a["e5"] = e5_all.reindex(f.index).to_numpy(float)
        a["orh"], a["orl"] = f["or_high"].to_numpy(float), f["or_low"].to_numpy(float)
        a["idx"] = f.index
        rows, idx, go = f.to_dict("index"), f.index, []
        for i in range(len(f) - 1):
            t = idx[i] + pd.Timedelta("1min")
            if not hm(CFG["setup_from"]) <= t - d <= hm(CFG["setup_until"]) or a["reg"][i] == 0:
                continue
            side = "CE" if a["reg"][i] == 1 else "PE"
            if checklist(side, rows[idx[i]], ab._Det(d, states[i][0], states[i][1]), ab.SNAP, ab._book(), t)["verdict"] == "GO":
                go.append(i)
        a["go"] = np.array(go, int)
        out.append({"day": d, "a": a})
        if n_day % 200 == 0:
            print(f"  precompute {d.date()}  {time.time() - t0:.0f}s", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(out))
    return out


# ------------------------------------------------------------------ exits
def sim(a, j, px, side, stop, ex):
    """Position filled at price px during bar j. Returns (exit_bar, exit_price)."""
    O, H, L, C, e9, e21, tod = a["O"], a["H"], a["L"], a["C"], a["e9"], a["e21"], a["tod"]
    n, sg = len(O), (1 if side == "CE" else -1)
    R = sg * (px - stop)
    hwm, armed, be = px, False, False
    for k in range(j, n):
        if tod[k] >= ab.EXIT_AT:
            return k, O[k]
        if k > j:
            adverse, fav = (L[k], H[k]) if sg > 0 else (H[k], L[k])
            if ex == "X1" and armed:                       # live trail, worse case first
                lvl = hwm - sg * ab.GAP_PTS
                if sg * (adverse - lvl) <= 0:
                    return k, (O[k] if sg * (O[k] - lvl) <= 0 else lvl)
            if ex != "X1" and be and sg * (adverse - px) <= 0:
                return k, (O[k] if sg * (O[k] - px) <= 0 else px)
            if ex == "X4":
                tgt = px + sg * 2 * R
                if sg * (fav - tgt) >= 0:
                    return k, (O[k] if sg * (O[k] - tgt) >= 0 else tgt)
            if sg * (fav - hwm) > 0:
                hwm = fav
            if sg * (hwm - px) >= ARM:
                armed = be = True
        if k + 1 < n:
            if sg * (C[k] - stop) < 0:
                return k + 1, O[k + 1]
            if ex == "X1" and ((sg > 0 and C[k] < e9[k] < e21[k]) or (sg < 0 and C[k] > e9[k] > e21[k])):
                return k + 1, O[k + 1]
            if be and ex in ("X2", "X3"):
                e = e9[k] if ex == "X2" else e21[k]
                if sg * (C[k] - e) < 0:
                    return k + 1, O[k + 1]
    return n - 1, C[-1]


# ------------------------------------------------------------------ entries
def _window(a, d, j):
    t = a["idx"][j] - d
    return hm(CFG["setup_from"]) <= t <= hm(CFG["setup_until"])


def _clean(a, j, sg, seen_opp):
    if seen_opp:
        return False
    lvl = a["orh"][j] if sg > 0 else a["orl"][j]
    return np.isfinite(lvl) and sg * (a["C"][j] - lvl) > 0


def day_trades(D, kind, mode, ex, random_dir=False):
    """kind in GO / TOUCH1m / RESUME1m / TOUCH5m / RESUME5m; mode in all / first / clean."""
    a, d = D["a"], D["day"]
    O, H, L, C, e21, reg, atr = a["O"], a["H"], a["L"], a["C"], a["e21"], a["reg"], a["atr"]
    ema = a["e5"] if kind.endswith("5m") else a["e9"]
    n = len(O)
    go = set(a["go"].tolist())
    out, entries, busy, used_ep = [], 0, -1, set()
    ep, ep_id, seen = 0, -1, {1: False, -1: False}
    pend = None                                   # RESUME: (touch_bar, side_sign, episode)
    for i in range(1, n - 1):
        if reg[i] != 0 and reg[i] != reg[i - 1]:
            ep_id += 1
        cur_ep = ep_id
        seen_opp_before = seen[-reg[i]] if reg[i] != 0 else True
        if reg[i] != 0:
            seen[reg[i]] = True
        if entries >= CFG["max_trades"]:
            break
        if i <= busy:
            pend = None
            continue
        sig = None                                # (fill_bar, fill_px, sign, stop)
        if kind == "GO":
            if i in go and _window(a, d, i + 1):
                sg = reg[i]
                sig = (i + 1, O[i + 1], sg, a["lo"][i] if sg > 0 else a["hi"][i], i)
        elif kind.startswith("TOUCH"):
            p = i - 1                              # signal bar (closed); fill on bar i
            sg = reg[p]
            if sg != 0 and _window(a, d, i) and np.isfinite(ema[p]) and sg * (C[p] - ema[p]) > 0:
                lvl = ema[p]
                hit = (L[i] <= lvl - THROUGH) if sg > 0 else (H[i] >= lvl + THROUGH)
                if hit:
                    px = min(O[i], lvl) if sg > 0 else max(O[i], lvl)
                    sw = a["lo"][p] if sg > 0 else a["hi"][p]
                    stop = min(sw, lvl - atr[p]) if sg > 0 else max(sw, lvl + atr[p])
                    sig = (i, px, sg, stop, p)
        else:                                      # RESUME
            sg = reg[i]
            if pend is not None:
                t0, psg, pep = pend
                if reg[i] != psg or i - t0 > RESUME_BARS or psg * (C[i] - e21[i]) < 0:
                    pend = None
                elif i > t0:
                    seg = slice(t0, i)
                    ext = H[seg].max() if psg > 0 else L[seg].min()
                    if psg * (C[i] - ext) > 0 and psg * (C[i] - ema[i]) > 0 and _window(a, d, i + 1):
                        pb = L[t0:i + 1].min() - THROUGH if psg > 0 else H[t0:i + 1].max() + THROUGH
                        stop = min(pb, C[i] - atr[i]) if psg > 0 else max(pb, C[i] + atr[i])
                        sig = (i + 1, O[i + 1], psg, stop, i)
                        pend = None
            if sig is None and pend is None and sg != 0 and np.isfinite(ema[i]):
                touched = (L[i] <= ema[i]) if sg > 0 else (H[i] >= ema[i])
                if touched and sg * (C[i] - e21[i]) >= 0:
                    pend = (i, sg, cur_ep)
        if sig is None:
            continue
        j, px, sg, stop, sb = sig
        if sg * (px - stop) <= 0:
            continue
        if mode in ("first", "clean"):
            if cur_ep in used_ep:
                continue
            if mode == "clean" and not _clean(a, sb, sg, seen_opp_before):
                continue
        used_ep.add(cur_ep)
        if random_dir:
            dist = abs(px - stop)
            sg = 1 if RNG.random() < 0.5 else -1
            stop = px - sg * dist
        side = "CE" if sg > 0 else "PE"
        k, xp = sim(a, j, px, side, stop, ex)
        out.append((d, a["idx"][j], a["idx"][min(k, n - 1)], side, px, xp, (1 if sg > 0 else -1) * (xp - px), abs(px - stop)))
        entries += 1
        busy = k
        pend = None
    return out


def trades(days, kind, mode, ex, random_dir=False):
    rows = [r for D in days for r in day_trades(D, kind, mode, ex, random_dir)]
    return pd.DataFrame(rows, columns=["day", "t0", "t1", "side", "s0", "s1", "gross", "risk"])


def premium(tr: pd.DataFrame) -> pd.DataFrame:
    """Black-Scholes premium change, next (non-expiring) expiry, one strike in the money."""
    if not len(tr):
        return tr.assign(prem=[])
    cal = options.expiry_calendar().index
    t0, t1 = pd.DatetimeIndex(tr["t0"]), pd.DatetimeIndex(tr["t1"])
    e = cal[np.minimum(cal.searchsorted(t0.normalize(), side="right"), len(cal) - 1)]
    atm = (tr["s0"] / options.STEP).round() * options.STEP
    K = np.where(tr["side"] == "CE", atm - options.STEP, atm + options.STEP)
    T0 = ((e + options.EXPIRY_TIME) - t0).total_seconds().to_numpy() / (365 * 86400.0)
    T1 = ((e + options.EXPIRY_TIME) - t1).total_seconds().to_numpy() / (365 * 86400.0)
    v0, v1 = options.vix_at(t0) / 100, options.vix_at(t1) / 100
    out = tr.copy()
    out["prem"] = np.nan
    for typ in ("CE", "PE"):
        m = (tr["side"] == typ).to_numpy()
        if m.any():
            p0 = options.price(tr["s0"].to_numpy()[m], K[m], T0[m], v0[m], typ)
            p1 = options.price(tr["s1"].to_numpy()[m], K[m], T1[m], v1[m], typ)
            out.loc[m, "prem"] = p1 - p0
    return out


def score(tr, ctl=None):
    if not len(tr):
        return {"n": 0}
    net = tr["prem"] - STRESS_PREM
    y = pd.to_datetime(tr["day"]).dt.year
    w, l = net[net > 0], net[net <= 0]
    r = {"n": len(tr), "idx_gross": tr["gross"].mean(), "prem_gross": tr["prem"].mean(), "net_stress": net.mean(),
         "t": ab.tstat(net), "win%": 100 * len(w) / len(net), "avg_win": w.mean() if len(w) else 0,
         "avg_loss": l.mean() if len(l) else 0, "risk_pts": tr["risk"].median(),
         "years_pos": f"{(net.groupby(y).mean() > 0).sum()}/{y.nunique()}", "rs_per_trade": net.mean() * 65}
    if ctl is not None and len(ctl):
        mon = lambda x: pd.to_datetime(x["day"]).dt.to_period("M")
        diff = tr["prem"].groupby(mon(tr)).mean() - ctl["prem"].groupby(mon(ctl)).mean()
        r["vs_randdir"] = tr["prem"].mean() - ctl["prem"].mean()
        r["vs_randdir_t"] = ab.tstat(diff.dropna())
    return r


CONFIGS = [("GO", "all")] + [(k, m) for k in ("TOUCH1m", "RESUME1m", "TOUCH5m", "RESUME5m") for m in ("first", "clean")]
EXITS = ("X1", "X2", "X3", "X4")


def check():
    """Regression: the user's two winners should be RESUME1m signals."""
    spot = data_source.load(options.SPOT, "1m")
    e5_all = ema5_known(spot)
    for d, want in (("2026-10-16", "10:12 CE"), ("2026-10-19", "12:14 PE")):
        d = pd.Timestamp(d)
        i0 = int(spot.index.searchsorted(d))
        nb = spot.iloc[max(0, i0 - 60):int(spot.index.searchsorted(d + pd.Timedelta(days=1)))]
        f = features(nb[nb.index < d + hm("15:30")].copy(), d)
        a = ab.arrays(f)
        states, _ = ab.run_day(f, d, float(spot.iloc[i0 - 1]["Close"]))
        a["reg"] = np.array([REG.get(s[0], 0) for s in states], int)
        a["atr"], a["e5"] = f["atr"].to_numpy(float), e5_all.reindex(f.index).to_numpy(float)
        a["orh"], a["orl"], a["idx"], a["go"] = f["or_high"].to_numpy(float), f["or_low"].to_numpy(float), f.index, np.array([], int)
        print(f"{d.date()} user entry {want}")
        for kind in ("TOUCH1m", "RESUME1m", "TOUCH5m", "RESUME5m"):
            tr = day_trades({"day": d, "a": a}, kind, "all", "X1")
            print(f"  {kind:9}", "; ".join(f"{r[1]:%H:%M} {r[3]} @{r[4]:.0f} stop {r[4] - (1 if r[3] == 'CE' else -1) * r[7]:.0f}" for r in tr) or "-")


def main():
    if "check" in sys.argv:
        return check()
    smoke = "smoke" in sys.argv
    days = precompute(smoke)
    out = OUT / "smoke" if smoke else OUT
    out.mkdir(parents=True, exist_ok=True)
    cut = 2022 if not smoke else 2022
    train = [D for D in days if D["day"].year <= cut]
    hold = [D for D in days if D["day"].year > cut]
    print(f"\n{len(train)} train days, {len(hold)} holdout days")
    res = []
    for kind, mode in CONFIGS:
        for ex in EXITS:
            res.append({"entry": kind, "mode": mode, "exit": ex, **score(premium(trades(train, kind, mode, ex)))})
    res = pd.DataFrame(res)
    res.to_csv(out / "train.csv", index=False)
    pd.set_option("display.width", 250)
    print("\nTRAIN 2018-2022 (premium pts per trade, stress cost 5.5; best first)")
    print(res.sort_values("net_stress", ascending=False).round(2).to_string(index=False))
    ok = res[res["n"] >= (5 if smoke else 200)].sort_values("net_stress", ascending=False)
    print(f"\n{(ok['net_stress'] > 0).sum()} of {len(ok)} configs (n >= 200) positive on train after stress costs")
    if smoke:
        return
    look = pd.concat([ok.head(TOP), res[(res.entry == "GO") & (res.exit == "X1")]]).drop_duplicates(["entry", "mode", "exit"])
    hv = []
    for r in look.itertuples():
        tr = premium(trades(hold, r.entry, r.mode, r.exit))
        ctl = pd.concat([premium(trades(hold, r.entry, r.mode, r.exit, random_dir=True)) for _ in range(10)])
        h = score(tr, ctl)
        passed = (h.get("net_stress", -1) > 0 and h.get("vs_randdir_t", 0) > 2
                  and int(h.get("years_pos", "0/0").split("/")[0]) >= 3)
        hv.append({"entry": r.entry, "mode": r.mode, "exit": r.exit, "train_net": r.net_stress, **h, "PASS": passed})
    hv = pd.DataFrame(hv)
    hv.to_csv(out / "holdout.csv", index=False)
    print(f"\nHOLDOUT 2023+ (one look: top {TOP} by train + GO|X1 reference):")
    print(hv.round(2).to_string(index=False))
    log = ab.OUT / "views_log.csv"
    pd.DataFrame([{"when": pd.Timestamp.now(), "what": "pullback_lab holdout look 2023+: " +
                   "; ".join(f"{a}|{b}|{c}" for a, b, c in zip(hv["entry"], hv["mode"], hv["exit"])),
                   "cfg": "pullback_lab pre-registered 2026-10-20"}]).to_csv(log, mode="a", header=not log.exists(), index=False)


if __name__ == "__main__":
    sys.exit(main())
