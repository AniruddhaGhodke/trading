"""BASEBREAK: sideways base -> higher lows (CE) / lower highs (PE) -> break of the base -> FIRST retracement -> enter
on resumption, small stop beyond the retracement, trail. The user's setup from the 21-Oct 22800PE chart.

Pre-registered 2026-10-21, before the first run. Nifty 1m, signals on closed bars only, fill at the next open.

  BASE      the W bars before the break bar: high-low band <= k ATR (the RANGE regime's 7-ATR band, or tighter).
  STRUCTURE (struct=on) split the base into 3 equal parts: CE = their lows rise (L1 < L2 < L3, higher lows),
            PE = their highs fall (H1 > H2 > H3, lower highs). struct=off = any base.
  BREAK     bar closes beyond the base: CE above the base high, PE below the base low. Fill window 09:30-12:00.
  RETRACE   after the break, a bar touches the 1m 9-EMA or the broken base edge (CE: low <= max(9-EMA, base high)).
  ENTRY     then a bar closes beyond the extreme since the break (CE: above the highest high since the break)
            -> fill next open. Cancelled if price closes back past the base middle or 15 bars pass after the break.
  STOP      retracement extreme -/+ 0.5, at least 1 ATR from the signal close; on a Nifty CLOSE (as all our rules).
            Skipped if the stop is more than R points away (R = 15 ~ Rs 540/lot, R = 25 ~ Rs 900/lot at delta 0.55).
  EXITS     X3 = at +18 Nifty (~+10 premium) stop -> entry, then exit on a close beyond the 21-EMA (pullback_lab X3)
            XS = at +18 stop -> entry, then stop trails the lowest low (CE) of the last 5 bars -0.5, on a close
            X4 = breakeven at +18, target 2R
            all: 15:10 time exit. Max 2 trades/day, one at a time.
  GRID      W {20, 30} x k {5, 7} x struct {on, off} x R {15, 25} x exit {X3, XS, X4} = 48 configs.
  SCORING   pullback_lab.premium (Black-Scholes, next expiry, 1 strike ITM, VIX) - 5.5 stress premium pts/trade;
            control = same bars, random direction (10 reps), monthly t - run for the top 3 by train net only (speed;
            amended after the first 2 configs printed, before any result was ranked).
  DATA      TRAIN 2018-2022 ONLY. 2023+ is spent for breakout/pullback-family entries (alert_tune, pullback_lab):
            no holdout look. Bar for a paper candidate: train net > 0, vs random-direction monthly t > 2,
            >= 4 of 5 years positive, n >= 150. Anything else = attention aid only, forward paper decides.

  ROUND 1 RESULT: 0/48 pass; best W30 k5 struct R15 gross +0.3, net -5.2, 0/5 years, vs random t 0.16.

ROUND 2 (pre-registered 2026-10-21 after round 1, before any run; targets the measured failure modes).
  Applied to B1 = W30 k5 struct R15 XS (round-1 best) and B2 = W30 k7 struct R25 XS (more trades):
  A1 runner  exit XR: no breakeven move; stop trails the 15-bar swing (from 15 bars after entry), max hold 180 min
             -> profit is in the top 5% of trades, the +18 breakeven may cut them.
  A2 3m      base / break / retracement on 3-minute bars (W = 10 bars, k in 3m-ATR, R = 25), fill on the 1m open
             -> 17% of trades died within 5 min on 1m noise.
  A3 2nd     enter on the SECOND retracement + resumption after the break (expiry 30 bars).
  A4 day     break only in the day's direction: beyond the day open and the opening range (09:15-09:29).
  A12        A1 + A2.     ALL = A1 + A2 + A3 + A4.
  (The ITM-1-strike contract and the 09:30-12:00 window are already in round 1's pricing / rules.)
  Control = random direction, 5 reps, for every config. Same paper-candidate bar as round 1, train 2018-2022 only.

  python basebreak.py day 2026-10-21     signals + paper outcome for one day (default config)
  python basebreak.py lab                round-1 train sweep -> SwingSystem/data/lab/basebreak/
  python basebreak.py lab2               round-2 train run   -> SwingSystem/data/lab/basebreak/round2.csv
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from assist import CFG, hm

DEFAULT = {"W": 30, "k": 7.0, "struct": True, "R": 25.0, "ex": "XS"}
THROUGH, EXPIRE, ARM, SWING = 0.5, 15, CFG["trail_arm"] / CFG["delta"], 5


def _struct_ok(H, L, i0, i1, sg):
    p = np.array_split(np.arange(i0, i1), 3)
    if sg > 0:
        lo = [L[s].min() for s in p]
        return lo[0] < lo[1] < lo[2]
    hi = [H[s].max() for s in p]
    return hi[0] > hi[1] > hi[2]


class Scanner:
    """The setup state machine, one closed bar at a time (shared by the lab and the live/replay rule)."""

    def __init__(self, d, W=30, k=7.0, struct=True, R=25.0, retests=1, ctx=None):
        self.d, self.W, self.k, self.struct, self.R, self.retests, self.ctx = d, W, k, struct, R, retests, ctx
        self.expire, self.s = EXPIRE * retests, None

    def reset(self):
        self.s = None

    def on_bar(self, a, i):
        """a = arrays holding at least bars 0..i (closed). Returns (sign, stop, setup info) when bar i completes a
        signal (entry = next open), else None."""
        H, L, C, e9, atr = a["H"], a["L"], a["C"], a["e9"], a["atr"]
        W, s = self.W, self.s
        nxt = a["idx"][i] + pd.Timedelta("1min")
        in_win = hm(CFG["setup_from"]) <= nxt - self.d <= hm(CFG["setup_until"])
        if s is None:
            if i < W or not in_win or not np.isfinite(atr[i - 1]):
                return None
            bh, bl = H[i - W:i].max(), L[i - W:i].min()
            if bh - bl > self.k * atr[i - 1]:
                return None
            sg = 1 if C[i] > bh else (-1 if C[i] < bl else 0)
            if sg == 0 or (self.struct and not _struct_ok(H, L, i - W, i, sg)):
                return None
            ctx = self.ctx
            if ctx is not None and not (sg * (C[i] - ctx["open"]) > 0 and sg * (C[i] - (ctx["orh"] if sg > 0 else ctx["orl"])) > 0):
                return None
            self.s = {"sg": sg, "t": i, "bh": bh, "bl": bl, "ext": H[i] if sg > 0 else L[i], "pb": None, "n": 0,
                      "t_break": a["idx"][i]}
            return None
        sg = s["sg"]
        if sg * (C[i] - (s["bh"] + s["bl"]) / 2) < 0 or i - s["t"] > self.expire:
            self.s = None
            return None
        if s["pb"] is None:
            touched = L[i] <= max(e9[i], s["bh"]) if sg > 0 else H[i] >= min(e9[i], s["bl"])
            if touched:
                s["pb"] = L[i] if sg > 0 else H[i]
            else:
                s["ext"] = max(s["ext"], H[i]) if sg > 0 else min(s["ext"], L[i])
            return None
        if sg * (C[i] - s["ext"]) > 0:
            s["n"] += 1
            if s["n"] < self.retests:                    # an earlier retest held: wait for the next one
                s["pb"] = None
                s["ext"] = max(s["ext"], H[i]) if sg > 0 else min(s["ext"], L[i])
                return None
            self.s = None
            if in_win:
                stop = min(s["pb"] - THROUGH, C[i] - atr[i]) if sg > 0 else max(s["pb"] + THROUGH, C[i] + atr[i])
                if abs(C[i] - stop) <= self.R:
                    return sg, stop, s
            return None
        s["pb"] = min(s["pb"], L[i]) if sg > 0 else max(s["pb"], H[i])
        s["ext"] = max(s["ext"], H[i]) if sg > 0 else min(s["ext"], L[i])
        return None


def signals(a, d, W=30, k=7.0, struct=True, R=25.0, busy_until=None, retests=1, ctx=None):
    """[(signal_bar, sign, stop, info)] for every entry signal of the day, in order. busy_until(j, sg, stop) -> bar
    index the trade filled at bar j ends on (no new setup starts while in a trade); None = ignore positions.
    retests = enter on the n-th retracement + resumption (round 2: 2). ctx = {"open", "orh", "orl"}: break only in
    the day's direction (beyond the day open and the opening range)."""
    sc, O, busy, out = Scanner(d, W, k, struct, R, retests, ctx), a["O"], -1, []
    for i in range(W, len(O) - 1):
        if i <= busy:
            sc.reset()
            continue
        got = sc.on_bar(a, i)
        if got is not None:
            sg, stop, s = got
            if sg * (O[i + 1] - stop) > 0:
                out.append((i, sg, stop, dict(s)))
                if busy_until is not None:
                    busy = busy_until(i + 1, sg, stop)
    return out


def sim(a, j, px, sg, stop, ex):
    """Filled at px during bar j (its open). Returns (exit_bar, exit_price, reason). Checks start the bar after."""
    O, H, L, C, e21, tod = a["O"], a["H"], a["L"], a["C"], a["e21"], a["tod"]
    n, R = len(O), sg * (px - stop)
    hwm, be = px, False
    for kk in range(j, n):
        if tod[kk] >= 15 * 60 + 10:
            return kk, O[kk], "time"
        if kk > j:
            adverse, fav = (L[kk], H[kk]) if sg > 0 else (H[kk], L[kk])
            if be and sg * (adverse - px) <= 0 and sg * (stop - px) <= 0:
                return kk, (O[kk] if sg * (O[kk] - px) <= 0 else px), "breakeven"
            if ex == "X4":
                tgt = px + sg * 2 * R
                if sg * (fav - tgt) >= 0:
                    return kk, (O[kk] if sg * (O[kk] - tgt) >= 0 else tgt), "target"
            hwm = max(hwm, fav) if sg > 0 else min(hwm, fav)
            if sg * (hwm - px) >= ARM:
                be = True
            if ex == "XR":                          # runner: no breakeven, 15-bar swing trail from entry
                be = False
                sw = L[max(j, kk - 14):kk + 1].min() - THROUGH if sg > 0 else H[max(j, kk - 14):kk + 1].max() + THROUGH
                if kk - j >= 15:
                    stop = max(stop, sw) if sg > 0 else min(stop, sw)
                if kk - j >= 180 and kk + 1 < n:
                    return kk + 1, O[kk + 1], "hold180"
            if be and ex == "XS":
                sw = L[max(j, kk - SWING + 1):kk + 1].min() - THROUGH if sg > 0 else H[max(j, kk - SWING + 1):kk + 1].max() + THROUGH
                stop = max(stop, sw) if sg > 0 else min(stop, sw)
        if kk + 1 < n:
            if sg * (C[kk] - stop) < 0:
                return kk + 1, O[kk + 1], "stop" if sg * (stop - px) < 0 else "trail"
            if be and ex == "X3" and sg * (C[kk] - e21[kk]) < 0:
                return kk + 1, O[kk + 1], "ema21"
    return n - 1, C[-1], "close"


def bars3(a):
    """3-minute bars from the day's 1m arrays (09:15, 09:18, ...); a 3m bar is known at the close of its 3rd 1m bar."""
    df = pd.DataFrame({"Open": a["O"], "High": a["H"], "Low": a["L"], "Close": a["C"]}, index=a["idx"])
    b = df.resample("3min", origin="start_day", offset="15min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return {"O": b["Open"].to_numpy(float), "H": b["High"].to_numpy(float), "L": b["Low"].to_numpy(float),
            "C": b["Close"].to_numpy(float), "e9": b["Close"].ewm(span=9, adjust=False).mean().to_numpy(float),
            "atr": (b["High"] - b["Low"]).rolling(20, min_periods=5).mean().to_numpy(float), "idx": b.index}


def day_trades(D, cfg=DEFAULT, random_dir=False, rng=None):
    a, d = D["a"], D["day"]
    res = []
    tf = cfg.get("tf", 1)
    sa = a if tf == 1 else bars3(a)
    ctx = None
    if cfg.get("ctx"):
        orm = a["idx"] < d + hm("09:30")
        ctx = {"open": a["O"][0], "orh": a["H"][orm].max(), "orl": a["L"][orm].min()}

    def run(j3, sg, stop):
        j = j3 if tf == 1 else int(np.searchsorted(a["idx"], sa["idx"][j3]))
        if len(res) >= CFG["max_trades"] or j >= len(a["O"]):
            return len(sa["O"])
        px = a["O"][j]
        if random_dir:
            dist = abs(px - stop)
            sg = 1 if rng.random() < 0.5 else -1
            stop = px - sg * dist
        kk, xp, why = sim(a, j, px, sg, stop, cfg["ex"])
        res.append((d, a["idx"][j], a["idx"][min(kk, len(a["O"]) - 1)], "CE" if sg > 0 else "PE", px, xp,
                    sg * (xp - px), abs(px - stop), why))
        if tf == 1:
            return kk
        return int(np.searchsorted(sa["idx"], a["idx"][min(kk, len(a["O"]) - 1)], side="right")) - 1

    signals(sa, d, cfg["W"] // tf, cfg["k"], cfg["struct"], cfg["R"], busy_until=lambda j, sg, st: run(j, sg, st),
            retests=cfg.get("retests", 1), ctx=ctx)
    return res


COLS = ["day", "t0", "t1", "side", "s0", "s1", "gross", "risk", "reason"]


def trades(days, cfg, random_dir=False, rng=None):
    return pd.DataFrame([r for D in days for r in day_trades(D, cfg, random_dir, rng)], columns=COLS)


# ------------------------------------------------------------------ live / sim / replay practice alert
# = DEFAULT (W30 k7 struct R25 XS): ~0.7 signals/day 2018-22 and it catches the user's 21-Oct chart (11:11, 11:34 PE).
# The round-1 "best" (k5 R15, ~0.15/day) fired nothing on 21-Oct and is not better in any real sense (all ~0 gross).
LIVE = dict(DEFAULT)


def _pullback():
    import pullback
    return pullback


class BaseBreakRule(_pullback().PullbackRule):
    """BASEBREAK alert bar by bar = signals() + sim(..., "XS") with LIVE (check: `python basebreak.py verify`).
    Contract, premium pricing, journal row and exit text come from PullbackRule; the paper journal is
    basebreak_journal.csv. Practice only: 0/62 configs had an edge (see the module docstring)."""
    KIND = "BASEBREAK"
    JOURNAL = _pullback().JOURNAL.with_name("basebreak_journal.csv")

    def __init__(self, day, mode="live", cfg=None):
        super().__init__(day, mode)
        self.cfg = cfg or LIVE
        self.scan = Scanner(self.day, self.cfg["W"], self.cfg["k"], self.cfg["struct"], self.cfg["R"])
        self.A = {x: [] for x in ("O", "H", "L", "C", "e9", "atr", "idx")}

    def step(self, ts, r, regime=None):
        out = []
        O, H, L, C = (float(r[k]) for k in ("Open", "High", "Low", "Close"))
        for x, v in (("O", O), ("H", H), ("L", L), ("C", C), ("e9", float(r["ema9"])), ("atr", float(r["atr"])), ("idx", ts)):
            self.A[x].append(v)
        if self.sig is not None:                     # fill at this bar's open
            sg, stop, _, sym = self.sig
            self.sig = None
            if sg * (O - stop) > 0:
                self.entries += 1
                self.pos = {"sg": sg, "px": O, "stop": stop, "t0": ts, "hwm": O, "be": False, "first": True, "nxt": None,
                            "sym": sym, "prem": self._prem(sym, ts), "bars": []}
        if self.pos is not None:
            if self.pos["prem"][1] == "ltp":
                got = self._prem(self.pos["sym"], self.pos["t0"], bar_only=True)
                if got[0] is not None:
                    self.pos["prem"] = got
            done = self._manage(ts, O, H, L, C, None)
            if done:
                out.append(done)
            self.scan.reset()
            return out
        if self.entries >= CFG["max_trades"]:
            return out
        a = {x: (self.A["idx"] if x == "idx" else np.asarray(v, float)) for x, v in self.A.items()}
        got = self.scan.on_bar(a, len(self.A["C"]) - 1)
        if got is None:
            return out
        sg, stop, s = got
        side = "CE" if sg > 0 else "PE"
        sym, lot = self._contract(ts, C, side)
        self.sig = (sg, stop, None, sym)
        risk = abs(C - stop)
        now_p = self._prem(sym, ts, close=True)[0]
        name = f"{sym[6:]}" if sym else f"ATM {round(C / 50) * 50:.0f}"
        prem_txt = f" ~{now_p:.1f}, premium stop ~{now_p - risk * CFG['delta']:.1f}" if now_p else ""
        shape = "higher lows" if sg > 0 else "lower highs"
        out.append((self.KIND, f"{side} {name}{prem_txt}: {self.cfg['W']}-min base {s['bl']:.0f}-{s['bh']:.0f} ({shape}) broke "
                               f"{'up' if sg > 0 else 'down'} at {s['t_break'] + pd.Timedelta('1min'):%H:%M}, retest {s['pb']:.0f} held, "
                               f"resumed {'above' if sg > 0 else 'below'} {s['ext']:.0f}. Entry next open (Nifty ~{C:.0f}), Nifty stop "
                               f"{'below' if sg > 0 else 'above'} {stop:.0f} ({risk:.0f} pts, ~Rs {risk * CFG['delta'] * lot:.0f}/lot). "
                               f"At +10 premium stop to entry, then trail the 5-bar swing. PRACTICE (no tested edge): take or skip."))
        return out

    def _manage(self, ts, O, H, L, C, e9):
        p, sg = self.pos, self.pos["sg"]
        if p["nxt"] is not None:
            return self._close(ts, O, p["nxt"])
        if ts - self.day >= hm("15:10"):
            return self._close(ts, O, "time")
        p["bars"].append((H, L))
        if not p["first"]:
            adverse, fav = (L, H) if sg > 0 else (H, L)
            if p["be"] and sg * (adverse - p["px"]) <= 0 and sg * (p["stop"] - p["px"]) <= 0:
                return self._close(ts, O if sg * (O - p["px"]) <= 0 else p["px"], "breakeven")
            p["hwm"] = max(p["hwm"], fav) if sg > 0 else min(p["hwm"], fav)
            if sg * (p["hwm"] - p["px"]) >= ARM:
                p["be"] = True
            if p["be"]:
                last = p["bars"][-SWING:]
                sw = min(lo for _, lo in last) - THROUGH if sg > 0 else max(h for h, _ in last) + THROUGH
                p["stop"] = max(p["stop"], sw) if sg > 0 else min(p["stop"], sw)
        p["first"] = False
        if sg * (C - p["stop"]) < 0:
            p["nxt"] = "stop" if sg * (p["stop"] - p["px"]) < 0 else "trail"
        return None


def verify():
    """The bar-by-bar rule == the lab (signals + sim XS, LIVE config) on the cached 2018-2026 days."""
    import pullback_lab as pl
    days = pl.precompute()
    ref = trades(days, LIVE)
    got = []
    for D in days:
        a, d = D["a"], D["day"]
        rule = BaseBreakRule(d, "verify")
        for i in range(len(a["O"])):
            rule.step(a["idx"][i], {"Open": a["O"][i], "High": a["H"][i], "Low": a["L"][i], "Close": a["C"][i],
                                    "ema9": a["e9"][i], "atr": a["atr"][i]})
        got += [(d, pd.Timestamp(f"{t['date']} {t['entry_time']}"), t["entry"], t["exit"]) for t in rule.closed]
    g = pd.DataFrame(got, columns=["day", "t0", "s0", "s1"])
    r = ref[["day", "t0", "s0", "s1"]]
    m = g.merge(r, how="outer", on=["day", "t0"], suffixes=("_rule", "_lab"), indicator=True)
    same = (m["_merge"] == "both") & np.isclose(m["s0_rule"], m["s0_lab"], atol=0.01) & np.isclose(m["s1_rule"], m["s1_lab"], atol=0.01)
    print(f"lab {len(r)} trades, rule {len(g)}, identical {same.sum()}")
    bad = m[~same]
    if len(bad):
        print(bad.head(20).to_string())
    return 0 if not len(bad) else 1


# ------------------------------------------------------------------ one day (no lookahead: only closed bars)
def day_arrays(day):
    import alert_backtest as ab
    import data_source
    import options
    from assist import features
    d = pd.Timestamp(day).normalize()
    spot = data_source.load(options.SPOT, "1m")
    i0 = int(spot.index.searchsorted(d))
    nb = spot.iloc[max(0, i0 - 60):int(spot.index.searchsorted(d + pd.Timedelta(days=1)))]
    f = features(nb[nb.index < d + hm("15:30")].copy(), d)
    a = ab.arrays(f)
    a["atr"], a["idx"] = f["atr"].to_numpy(float), f.index
    return {"day": d, "a": a}


def show_day(day, cfg=DEFAULT):
    import pullback_lab as pl
    D = day_arrays(day)
    a = D["a"]
    for i, sg, stop, s in signals(a, D["day"], cfg["W"], cfg["k"], cfg["struct"], cfg["R"]):
        t = a["idx"][i] + pd.Timedelta("1min")
        print(f"{t:%H:%M} {'CE' if sg > 0 else 'PE'}  base {s['bl']:.0f}-{s['bh']:.0f} broke at {a['idx'][s['t']]:%H:%M}, "
              f"retrace {s['pb']:.0f}, resumed past {s['ext']:.0f} -> entry next open {a['O'][i + 1]:.0f}, stop {stop:.0f} "
              f"({abs(a['C'][i] - stop):.0f} pts)")
    tr = trades([D], cfg)
    if len(tr):
        tr = pl.premium(tr)
        tr["net"] = tr["prem"] - pl.STRESS_PREM
        print(tr.assign(t0=tr.t0.dt.strftime("%H:%M"), t1=tr.t1.dt.strftime("%H:%M")).drop(columns="day").round(1).to_string(index=False))
    else:
        print("no trades")


def lab():
    import alert_backtest as ab
    import pullback_lab as pl
    out = ab.OUT.parent / "basebreak"
    out.mkdir(parents=True, exist_ok=True)
    days = [D for D in pl.precompute() if D["day"].year <= 2022]
    print(f"{len(days)} train days 2018-2022")
    res = []
    for W in (20, 30):
        for k in (5.0, 7.0):
            for st in (True, False):
                for R in (15.0, 25.0):
                    for ex in ("X3", "XS", "X4"):
                        cfg = {"W": W, "k": k, "struct": st, "R": R, "ex": ex}
                        tr = pl.premium(trades(days, cfg))
                        sc = pl.score(tr)
                        res.append({**cfg, **sc})
                        print(f"  W{W} k{k:.0f} struct={st!s:5} R{R:.0f} {ex}: n {sc['n']}, net {sc.get('net_stress', np.nan):+.2f}, "
                              f"gross {sc.get('prem_gross', np.nan):+.2f}, t_rand {sc.get('vs_randdir_t', np.nan):.2f}", flush=True)
    res = pd.DataFrame(res)
    for r in res.sort_values("net_stress", ascending=False).head(3).itertuples():      # control only for the top 3 (speed)
        cfg = {"W": r.W, "k": r.k, "struct": r.struct, "R": r.R, "ex": r.ex}
        rng = np.random.default_rng(20261021)
        ctl = pd.concat([pl.premium(trades(days, cfg, True, rng)) for _ in range(10)])
        sc = pl.score(pl.premium(trades(days, cfg)), ctl)
        res.loc[r.Index, ["vs_randdir", "vs_randdir_t"]] = sc["vs_randdir"], sc["vs_randdir_t"]
    res.to_csv(out / "train.csv", index=False)
    pd.set_option("display.width", 250)
    print("\nTRAIN 2018-2022, premium pts/trade (stress 5.5), best first")
    print(res.sort_values("net_stress", ascending=False).round(2).to_string(index=False))
    ok = res[(res["n"] >= 150) & (res["net_stress"] > 0) & (res["vs_randdir_t"] > 2)
             & (res["years_pos"].str.split("/").str[0].astype(int) >= 4)]
    print(f"\n{len(ok)} of {len(res)} configs meet the pre-registered paper-candidate bar")


def lab2():
    import alert_backtest as ab
    import pullback_lab as pl
    out = ab.OUT.parent / "basebreak"
    days = [D for D in pl.precompute() if D["day"].year <= 2022]
    var = {"base": {}, "A1 runner": {"ex": "XR"}, "A2 3m": {"tf": 3, "R": 25.0}, "A3 2nd": {"retests": 2},
           "A4 day": {"ctx": True}, "A12": {"ex": "XR", "tf": 3, "R": 25.0},
           "ALL": {"ex": "XR", "tf": 3, "R": 25.0, "retests": 2, "ctx": True}}
    res = []
    for bname, base in (("B1", {"W": 30, "k": 5.0, "struct": True, "R": 15.0, "ex": "XS"}),
                        ("B2", {"W": 30, "k": 7.0, "struct": True, "R": 25.0, "ex": "XS"})):
        for vname, v in var.items():
            cfg = {**base, **v}
            tr = pl.premium(trades(days, cfg))
            rng = np.random.default_rng(20261021)
            ctl = pd.concat([pl.premium(trades(days, cfg, True, rng)) for _ in range(5)])
            sc = pl.score(tr, ctl)
            p = tr["prem"] if len(tr) else pd.Series(dtype=float)
            sc["top5%_share"] = p[p >= p.quantile(.95)].sum() if len(p) else np.nan
            res.append({"base": bname, "variant": vname, **sc})
            print(f"  {bname} {vname:10}: n {sc['n']}, gross {sc.get('prem_gross', np.nan):+.2f}, net {sc.get('net_stress', np.nan):+.2f}, "
                  f"years {sc.get('years_pos')}, t_rand {sc.get('vs_randdir_t', np.nan):.2f}", flush=True)
    res = pd.DataFrame(res)
    res.to_csv(out / "round2.csv", index=False)
    pd.set_option("display.width", 250)
    print("\nROUND 2 TRAIN 2018-2022, premium pts/trade (stress 5.5)")
    print(res.round(2).to_string(index=False))
    ok = res[(res["n"] >= 150) & (res["net_stress"] > 0) & (res["vs_randdir_t"] > 2)
             & (res["years_pos"].str.split("/").str[0].astype(int) >= 4)]
    print(f"\n{len(ok)} of {len(res)} configs meet the pre-registered paper-candidate bar")


if __name__ == "__main__":
    if sys.argv[1:2] == ["day"]:
        show_day(sys.argv[2], LIVE)
    elif sys.argv[1:2] == ["verify"]:
        sys.exit(verify())
    elif sys.argv[1:2] == ["report"]:
        _pullback().report("BASEBREAK")
    elif sys.argv[1:2] == ["lab"]:
        lab()
    elif sys.argv[1:2] == ["lab2"]:
        lab2()
    else:
        print(__doc__)
