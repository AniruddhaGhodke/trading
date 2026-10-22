"""Nifty 50 INDEX intraday strategy search on 1-minute bars, 2018-2026 (futures-style, index points).

Rules, costs, splits and the definition of "works": data/lab/nifty_intraday/PREREGISTRATION.md (written before any test).

  python lab_nifty_intraday.py dev                 run every family on all days, rank on DEV, save per-config daily P&L
  python lab_nifty_intraday.py validate F1 F2 ...  VALIDATION (2023-24) for the DEV finalists of these families (logged)
  python lab_nifty_intraday.py test NAME ...       TEST (2025-26) + full OOS criteria + walk-forward, ONE look (logged)
  python lab_nifty_intraday.py show NAME           every split + yearly table for one config (DEV only unless tested)
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "data" / "angel" / "1m" / "IDX_NSEI.csv"
OUT = ROOT / "data" / "lab" / "nifty_intraday"
NB = 360                                    # 09:15 .. 15:14 one-minute bars
COST = {"base": 0.035, "stress": 0.05}      # % round trip
DEV = ("2018-01-01", "2022-12-31")
VAL = ("2023-01-01", "2024-12-31")
TEST = ("2025-01-01", "2026-12-31")
OOS = ("2023-01-01", "2026-12-31")
NAN = np.nan


# ================================================================ data
def load():
    cache = OUT / "matrix_v2.npz"            # v2: audit fixes (day-open filter, ppC/plast on the full calendar)
    if cache.exists() and cache.stat().st_mtime > SRC.stat().st_mtime:
        z = np.load(cache, allow_pickle=True)
        return {k: z[k] for k in z.files}
    s = pd.read_csv(SRC, index_col=0, parse_dates=True)
    s = s[s.index >= "2017-12-01"]
    m = (s.index.hour * 60 + s.index.minute - 555).to_numpy()
    s = s[(m >= 0) & (m <= 374)]
    m = (s.index.hour * 60 + s.index.minute - 555).to_numpy()
    days = s.index.normalize()
    full = s.groupby(days).agg(dH=("High", "max"), dL=("Low", "min"), dC=("Close", "last"))
    core = s[m < NB]
    mc = m[m < NB]
    dc = core.index.normalize()
    ud = pd.DatetimeIndex(dc.unique())
    pos = ud.get_indexer(dc)
    arr = {}
    for col in ("Open", "High", "Low", "Close"):
        a = np.full((len(ud), NB), NAN)
        a[pos, mc] = core[col].to_numpy()
        arr[col] = a
    miss = np.isnan(arr["Close"]).sum(1)
    keep = (miss <= 5) & ~np.isnan(arr["Open"][:, 0])      # no back-filled day open (audit 2026-10-20)
    for col in arr:                         # forward-fill single missing bars (flat bar at the last close)
        a = pd.DataFrame(arr[col]).T
        arr[col] = a.T.to_numpy()
    c = pd.DataFrame(arr["Close"]).ffill(axis=1).bfill(axis=1).to_numpy()
    for col in ("Open", "High", "Low"):
        arr[col] = np.where(np.isnan(arr[col]), c, arr[col])
    arr["Close"] = c
    full = full.reindex(ud)
    d = {"D": ud.to_numpy(), "O": arr["Open"], "H": arr["High"], "L": arr["Low"], "C": arr["Close"],
         "dH": full["dH"].to_numpy(), "dL": full["dL"].to_numpy(), "dC": full["dC"].to_numpy(), "keep": keep}
    # previous trading day's values (on the full kept calendar)
    for k in ("dH", "dL", "dC"):
        d["p" + k[1:]] = np.r_[NAN, d[k][:-1]]
    d["ppC"] = np.r_[NAN, d["pC"][:-1]]                              # close two sessions ago (full calendar)
    d["plast"] = np.r_[NAN, (c[:-1, 359] / c[:-1, 299] - 1) * 100]   # previous session's last hour (full calendar)
    tr = np.maximum(d["dH"] - d["dL"], np.maximum(abs(d["dH"] - d["pC"]), abs(d["dL"] - d["pC"])))
    d["atr14"] = pd.Series(tr).rolling(14).mean().shift(1).to_numpy()          # known before today's open
    rng = d["dH"] - d["dL"]
    d["nr7"] = np.r_[False, (pd.Series(rng).rolling(7).min() == rng).to_numpy()[:-1]]  # YESTERDAY was NR7
    d["sma20"] = pd.Series(d["dC"]).rolling(20).mean().shift(1).to_numpy()
    sel = keep & (pd.DatetimeIndex(d["D"]) >= "2018-01-01") & np.isfinite(d["pC"]) & np.isfinite(d["atr14"])
    d = {k: (v[sel] if hasattr(v, "__len__") and len(v) == len(ud) else v) for k, v in d.items()}
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez(cache, **d)
    return d


# ================================================================ simulators (vectorized over days)
IDX = np.arange(NB)


def _first(mask):
    return np.where(mask.any(1), mask.argmax(1), 10_000)


def sim(d, ent, side, stop, tgt, xt, cond=None):
    """One trade per day. ent = entry bar (filled at its OPEN), -1 = none. side +1/-1. stop/tgt prices (nan = none).
    xt = last bar (exit at its CLOSE). cond (n, NB) bool: exit at the next bar's open after the first bar where True.
    Returns (gross % of entry price per day, nan when no trade)."""
    O, H, L, C = d["O"], d["H"], d["L"], d["C"]
    n = len(O)
    has = ent >= 0
    e0 = np.where(has, ent, 0)
    ent_px = O[np.arange(n), e0]
    with np.errstate(invalid="ignore"):          # target already reached at entry (e.g. gap filled before a late entry)
        has &= ~(np.where(side > 0, tgt <= ent_px, tgt >= ent_px) & np.isfinite(tgt))
    e = np.where(has, ent, 0)
    xt = np.clip(np.where(has, xt, 0), 0, NB - 1)
    win = (IDX >= e[:, None]) & (IDX <= xt[:, None]) & has[:, None]
    lng = side[:, None] > 0
    st, tg = stop[:, None], tgt[:, None]
    with np.errstate(invalid="ignore"):
        sh = win & np.where(lng, L <= st, H >= st)
        th = win & np.where(lng, H >= tg, L <= tg)
    si, ti = _first(sh), _first(th)
    ci = _first(win & cond & (IDX < xt[:, None])) if cond is not None else np.full(n, 10_000)
    entry = O[np.arange(n), e]
    r = np.arange(n)
    px = C[r, xt].copy()
    use_c = (ci < si) & (ci < ti) & (ci < 10_000)
    px[use_c] = O[r[use_c], np.minimum(ci[use_c] + 1, NB - 1)]
    use_t = (ti < si) & (ti <= ci) & (ti < 10_000)
    px[use_t] = tgt[use_t]
    use_s = (si <= ti) & (si <= ci) & (si < 10_000)
    so = O[r[use_s], si[use_s]]
    px[use_s] = np.where(side[use_s] > 0, np.minimum(stop[use_s], so), np.maximum(stop[use_s], so))
    g = side * (px - entry) / entry * 100
    return np.where(has, g, NAN), has.astype(int)


def possim(O, C, hold, price):
    """Position series on any bar grid. hold (n, B) = position held from bar i's OPEN to bar i+1's open (decided at
    bar i-1's close); flat after the last bar's close. Returns (gross % per day, trades per day, turnover legs)."""
    nxt = np.concatenate([O[:, 1:], C[:, -1:]], axis=1)
    pts = (hold * (nxt - O)).sum(1)
    h0 = np.concatenate([np.zeros((len(O), 1)), hold, np.zeros((len(O), 1))], axis=1)
    legs = np.abs(np.diff(h0, axis=1)).sum(1)                   # sides traded (entry + exit = 2 per round trip)
    entries = ((h0[:, 1:] != 0) & (h0[:, 1:] != h0[:, :-1])).sum(1)
    g = pts / price * 100
    return np.where(legs > 0, g, NAN), entries, legs


def bars(d, k):
    """Resample the 1m day matrix to k-minute bars (k divides 360)."""
    n = len(d["O"])
    O = d["O"].reshape(n, NB // k, k)[:, :, 0]
    C = d["C"].reshape(n, NB // k, k)[:, :, -1]
    H = d["H"].reshape(n, NB // k, k).max(2)
    L = d["L"].reshape(n, NB // k, k).min(2)
    return O, H, L, C


def shift_hold(sig):
    """Desired position at bar i's close -> held from bar i+1's open."""
    return np.concatenate([np.zeros((len(sig), 1)), sig[:, :-1]], axis=1)


# ================================================================ families
def f_imom(d):
    """Gao-Han-Li-Zhou intraday momentum: prev close -> bar k return predicts the late session."""
    O, C = d["O"], d["C"]
    out = {}
    for k, e, th, agree in itertools.product((30, 60), (300, 330), (0.0, 0.25, 0.5), (False, True)):
        r1 = (C[:, k - 1] / d["pC"] - 1) * 100
        side = np.sign(r1)
        ok = np.abs(r1) > th
        if agree:
            ok &= np.sign(C[:, e - 1] - C[:, k - 1]) == side
        ent = np.where(ok & (side != 0), e, -1)
        out[f"IMOM k{k} e{e} th{th} agree{int(agree)}"] = sim(d, ent, side, np.full(len(O), NAN), np.full(len(O), NAN),
                                                              np.full(len(O), NB - 1))
    return out


def f_noise(d):
    """Zarattini-Aziz-Barbon noise area: bands = open (gap-adjusted) +- vm x avg |move from open to minute m| over
    L days; checked every f minutes; long above / short below; exit when back inside the band; flat at 15:14."""
    O, H, L_, C = d["O"], d["H"], d["L"], d["C"]
    dO = O[:, 0]
    mv = np.abs(C / dO[:, None] - 1)
    out = {}
    for Lb, vm, f in itertools.product((10, 14, 20), (0.8, 1.0, 1.25, 1.5), (15, 30, 60)):
        sig_m = pd.DataFrame(mv).rolling(Lb).mean().shift(1).to_numpy()
        up = np.maximum(dO, d["pC"])[:, None] * (1 + vm * sig_m)
        dn = np.minimum(dO, d["pC"])[:, None] * (1 - vm * sig_m)
        chk = np.zeros(NB, bool)
        chk[np.arange(30 - 1, NB - 1, f)] = True          # bar closes at 09:45, then every f min
        pos = np.zeros_like(C)
        cur = np.zeros(len(C))
        for i in range(NB):
            if chk[i]:
                cur = np.where(C[:, i] > up[:, i], 1.0, np.where(C[:, i] < dn[:, i], -1.0,
                               np.where((cur > 0) & (C[:, i] < up[:, i]), 0.0,
                                        np.where((cur < 0) & (C[:, i] > dn[:, i]), 0.0, cur))))
                cur = np.where(np.isnan(up[:, i]), 0.0, cur)
            pos[:, i] = cur
        hold = shift_hold(pos)
        out[f"NOISE L{Lb} vm{vm} f{f}"] = possim(O, C, hold, dO)[:2]
    return out


def f_orb(d):
    O, H, L, C = d["O"], d["H"], d["L"], d["C"]
    n = len(O)
    out = {}
    orw_hist = {}
    for w in (5, 15, 30, 60):
        orw_hist[w] = (H[:, :w].max(1) - L[:, :w].min(1)) / O[:, 0]
    for w, stopm, tgtR, filt, cut in itertools.product((5, 15, 30, 60), ("opp", "mid"), (1.0, 2.0, 0.0),
                                                      ("all", "narrow", "nr7"), (180,)):
        hi, lo = H[:, :w].max(1), L[:, :w].min(1)
        up = (C > hi[:, None]) & (IDX >= w) & (IDX < cut)
        dn = (C < lo[:, None]) & (IDX >= w) & (IDX < cut)
        iu, idn = _first(up), _first(dn)
        side = np.where(iu < idn, 1.0, np.where(idn < iu, -1.0, 0.0))
        sig = np.minimum(iu, idn)
        ok = (side != 0) & (sig + 1 < NB)
        if filt == "narrow":
            med = pd.Series(orw_hist[w]).rolling(20).median().shift(1).to_numpy()
            ok &= orw_hist[w] < med
        elif filt == "nr7":
            ok &= d["nr7"].astype(bool)
        ent = np.where(ok, sig + 1, -1)
        stop = np.where(side > 0, lo, hi) if stopm == "opp" else (hi + lo) / 2
        entry = O[np.arange(n), np.clip(ent, 0, NB - 1)]
        R = np.abs(entry - stop)
        tgt = entry + side * tgtR * R if tgtR else np.full(n, NAN)
        out[f"ORB w{w} stop-{stopm} tgt{tgtR}R {filt}"] = sim(d, ent, side, stop.astype(float), tgt, np.full(n, NB - 1))
    return out


def f_gap(d):
    O, C = d["O"], d["C"]
    n = len(O)
    gap = (O[:, 0] / d["pC"] - 1) * 100
    out = {}
    for th, mode, e, x, stopk in itertools.product((0.3, 0.5, 0.8, 1.2), ("fade", "go"), (0, 5, 15),
                                                   (60, 165, NB - 1), (0.0, 1.0)):
        side = -np.sign(gap) if mode == "fade" else np.sign(gap)
        ent = np.where((np.abs(gap) >= th) & (side != 0), e, -1)
        entry = O[:, e]
        g_pts = np.abs(O[:, 0] - d["pC"])
        stop = entry - side * stopk * g_pts if stopk else np.full(n, NAN)
        tgt = d["pC"] if mode == "fade" else np.full(n, NAN)
        out[f"GAP {mode} th{th} e{e} x{x} stop{stopk}"] = sim(d, ent, side, stop, tgt.astype(float), np.full(n, x))
    return out


def f_pdhl(d):
    O, H, L, C = d["O"], d["H"], d["L"], d["C"]
    n = len(O)
    out = {}
    for mode, s, tgtR, cut in itertools.product(("break", "fade"), (0.25, 0.5), (1.0, 2.0, 0.0), (120, 240)):
        ph, pl = d["pH"][:, None], d["pL"][:, None]
        if mode == "break":
            up, dn = (C > ph), (C < pl)
        else:   # turtle soup: traded beyond the level earlier today, now closed back inside
            above = np.maximum.accumulate(H > ph, axis=1)
            below = np.maximum.accumulate(L < pl, axis=1)
            dn, up = above & (C < ph), below & (C > pl)
        rng_ok = (IDX >= 15) & (IDX < cut)
        iu, idn = _first(up & rng_ok), _first(dn & rng_ok)
        side = np.where(iu < idn, 1.0, np.where(idn < iu, -1.0, 0.0))
        sig = np.minimum(iu, idn)
        ok = (side != 0) & (sig + 1 < NB) & ~((O[:, 0] > d["pH"]) | (O[:, 0] < d["pL"]))   # opened inside y'day range
        ent = np.where(ok, sig + 1, -1)
        entry = O[np.arange(n), np.clip(ent, 0, NB - 1)]
        R = s * d["atr14"]
        stop = entry - side * R
        tgt = entry + side * tgtR * R if tgtR else np.full(n, NAN)
        out[f"PDHL {mode} stop{s}atr tgt{tgtR}R cut{cut}"] = sim(d, ent, side, stop, tgt, np.full(n, NB - 1))
    return out


def f_cpr(d):
    O, H, L, C = d["O"], d["H"], d["L"], d["C"]
    n = len(O)
    P = (d["pH"] + d["pL"] + d["pC"]) / 3
    BC = (d["pH"] + d["pL"]) / 2
    TC = 2 * P - BC
    width = np.abs(TC - BC) / P * 100
    out = {}
    for th, w, tgtR in itertools.product((0.1, 0.2, 0.3), (15, 30), (2.0, 0.0)):
        hi, lo = H[:, :w].max(1), L[:, :w].min(1)
        up = (C > np.maximum(hi, np.maximum(TC, BC))[:, None]) & (IDX >= w) & (IDX < 180)
        dn = (C < np.minimum(lo, np.minimum(TC, BC))[:, None]) & (IDX >= w) & (IDX < 180)
        iu, idn = _first(up), _first(dn)
        side = np.where(iu < idn, 1.0, np.where(idn < iu, -1.0, 0.0))
        sig = np.minimum(iu, idn)
        ok = (side != 0) & (width < th) & (sig + 1 < NB)
        ent = np.where(ok, sig + 1, -1)
        stop = np.where(side > 0, lo, hi).astype(float)
        entry = O[np.arange(n), np.clip(ent, 0, NB - 1)]
        tgt = entry + side * tgtR * np.abs(entry - stop) if tgtR else np.full(n, NAN)
        out[f"CPR narrow<{th} orb{w} tgt{tgtR}R"] = sim(d, ent, side, stop, tgt, np.full(n, NB - 1))
    return out


def _rsi(x, n):
    dlt = np.diff(x, prepend=x[0])
    up = pd.Series(np.clip(dlt, 0, None)).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    dn = pd.Series(np.clip(-dlt, 0, None)).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    return 100 - 100 / (1 + up / np.where(dn == 0, 1e-12, dn))


def f_mr(d):
    """5-minute RSI(2) mean reversion, intraday only (entries 09:30-14:30)."""
    O, H, L, C = bars(d, 5)
    n, B = C.shape
    r2 = _rsi(C.reshape(-1), 2).reshape(n, B)
    t = np.arange(B)
    win = (t >= 3) & (t <= 63)
    out = {}
    for lv, xv, filt, sides in itertools.product((5, 10, 20), (50, 70), ("none", "trend", "open"), ("long", "short", "both")):
        trend_up = (d["pC"] > d["sma20"])[:, None]
        pos = np.zeros_like(C)
        cur = np.zeros(n)
        for i in range(B):
            longs = (r2[:, i] < lv) & win[i] & (sides in ("long", "both"))
            shorts = (r2[:, i] > 100 - lv) & win[i] & (sides in ("short", "both"))
            if filt == "trend":
                longs &= trend_up[:, 0]
                shorts &= ~trend_up[:, 0]
            elif filt == "open":
                longs &= C[:, i] > O[:, 0]
                shorts &= C[:, i] < O[:, 0]
            cur = np.where((cur > 0) & (r2[:, i] > xv), 0.0, cur)
            cur = np.where((cur < 0) & (r2[:, i] < 100 - xv), 0.0, cur)
            cur = np.where(cur == 0, np.where(longs, 1.0, np.where(shorts, -1.0, 0.0)), cur)
            pos[:, i] = cur
        out[f"MR rsi2<{lv} exit{xv} {filt} {sides}"] = possim(O, C, shift_hold(pos), O[:, 0])[:2]
    return out


def f_trend(d):
    out = {}
    for k in (5, 15):
        O, H, L, C = bars(d, k)
        n, B = C.shape
        flat = C.reshape(-1)
        start = (30 // k)                       # no entries before 09:45
        for fa, sl in ((5, 20), (9, 21), (12, 26)):
            ef = pd.Series(flat).ewm(span=fa, adjust=False).mean().to_numpy().reshape(n, B)
            es = pd.Series(flat).ewm(span=sl, adjust=False).mean().to_numpy().reshape(n, B)
            sig = np.sign(ef - es)
            sig[:, :start] = 0
            out[f"TREND ema{fa}/{sl} {k}m"] = possim(O, C, shift_hold(sig), O[:, 0])[:2]
        for per, mult in ((10, 3.0), (7, 2.0), (10, 2.0)):
            hf, lf, cf = H.reshape(-1), L.reshape(-1), flat
            tr = np.maximum(hf - lf, np.maximum(abs(hf - np.r_[cf[0], cf[:-1]]), abs(lf - np.r_[cf[0], cf[:-1]])))
            atr = pd.Series(tr).ewm(alpha=1 / per, adjust=False).mean().to_numpy()
            mid = (hf + lf) / 2
            ub, lb = mid + mult * atr, mid - mult * atr
            fu, fl = ub.copy(), lb.copy()
            dirn = np.ones(len(cf))
            for i in range(1, len(cf)):
                fu[i] = ub[i] if (ub[i] < fu[i - 1] or cf[i - 1] > fu[i - 1]) else fu[i - 1]
                fl[i] = lb[i] if (lb[i] > fl[i - 1] or cf[i - 1] < fl[i - 1]) else fl[i - 1]
                dirn[i] = 1 if cf[i] > fu[i - 1] else (-1 if cf[i] < fl[i - 1] else dirn[i - 1])
            sig = dirn.reshape(n, B).copy()
            sig[:, :start] = 0
            out[f"TREND st{per},{mult} {k}m"] = possim(O, C, shift_hold(sig), O[:, 0])[:2]
    return out


def f_season(d):
    """Fixed holding windows (long or short), optionally one weekday only."""
    O, C = d["O"], d["C"]
    n = len(O)
    marks = [0, 15, 45, 105, 165, 225, 285, 345]       # 09:15 09:30 10:00 11:00 12:00 13:00 14:00 15:00
    dow = pd.DatetimeIndex(d["D"]).dayofweek.to_numpy()
    out = {}
    for a, b in itertools.combinations(marks + [NB], 2):
        for side in (1.0, -1.0):
            for wd in (None, 0, 1, 2, 3, 4):
                ok = np.ones(n, bool) if wd is None else dow == wd
                ent = np.where(ok, a, -1)
                out[f"SEASON {'long' if side > 0 else 'short'} {a}-{b - 1} dow{wd}"] = sim(
                    d, ent, np.full(n, side), np.full(n, NAN), np.full(n, NAN), np.full(n, b - 1))
    return out


def f_noise2(d):
    """Iteration 2 (designed on DEV after VALIDATION look #1): noise area by side, exit anchor, volatility regime."""
    O, C = d["O"], d["C"]
    dO = O[:, 0]
    mv = np.abs(C / dO[:, None] - 1)
    vol = d["atr14"] / d["pC"]
    hi_vol = vol > pd.Series(vol).rolling(250, min_periods=60).median().shift(1).to_numpy()
    out = {}
    for Lb, vm in itertools.product((10, 14, 20), (1.0, 1.25, 1.5)):
        sig_m = pd.DataFrame(mv).rolling(Lb).mean().shift(1).to_numpy()
        up = np.maximum(dO, d["pC"])[:, None] * (1 + vm * sig_m)
        dn = np.minimum(dO, d["pC"])[:, None] * (1 - vm * sig_m)
        ua, da = np.maximum(dO, d["pC"])[:, None], np.minimum(dO, d["pC"])[:, None]
        for f, sides, exm, vf in itertools.product((30, 60), ("both", "long", "short"), ("band", "open"), ("all", "hivol")):
            chk = np.zeros(NB, bool)
            chk[np.arange(30 - 1, NB - 1, f)] = True
            ex_up = up if exm == "band" else np.broadcast_to(ua, up.shape)
            ex_dn = dn if exm == "band" else np.broadcast_to(da, dn.shape)
            pos = np.zeros_like(C)
            cur = np.zeros(len(C))
            for i in range(NB):
                if chk[i]:
                    go_l = (C[:, i] > up[:, i]) & (sides != "short")
                    go_s = (C[:, i] < dn[:, i]) & (sides != "long")
                    cur = np.where((cur > 0) & (C[:, i] < ex_up[:, i]), 0.0, cur)
                    cur = np.where((cur < 0) & (C[:, i] > ex_dn[:, i]), 0.0, cur)
                    cur = np.where(go_l, 1.0, np.where(go_s, -1.0, cur))
                    cur = np.where(np.isnan(up[:, i]), 0.0, cur)
                pos[:, i] = cur
            if vf == "hivol":
                pos = pos * hi_vol[:, None]
            out[f"NOISE2 L{Lb} vm{vm} f{f} {sides} ex-{exm} {vf}"] = possim(O, C, shift_hold(pos), dO)[:2]
    return out


def f_noise3(d):
    """Iteration 3: closer to Zarattini-Aziz-Barbon - trailing exit at max(upper band, VWAP) for longs (min for
    shorts). The index has no volume, so VWAP is replaced by TWAP = running mean of 1m closes since 09:15."""
    O, C = d["O"], d["C"]
    dO = O[:, 0]
    mv = np.abs(C / dO[:, None] - 1)
    twap = np.cumsum(C, axis=1) / np.arange(1, NB + 1)
    vol = d["atr14"] / d["pC"]
    hi_vol = vol > pd.Series(vol).rolling(250, min_periods=60).median().shift(1).to_numpy()
    out = {}
    for Lb, vm in itertools.product((10, 14, 20), (0.75, 1.0, 1.25, 1.5)):
        sig_m = pd.DataFrame(mv).rolling(Lb).mean().shift(1).to_numpy()
        up = np.maximum(dO, d["pC"])[:, None] * (1 + vm * sig_m)
        dn = np.minimum(dO, d["pC"])[:, None] * (1 - vm * sig_m)
        for f, sides, vf in itertools.product((15, 30), ("both", "long", "short"), ("all", "hivol")):
            chk = np.zeros(NB, bool)
            chk[np.arange(30 - 1, NB - 1, f)] = True
            pos = np.zeros_like(C)
            cur = np.zeros(len(C))
            for i in range(NB):
                if chk[i]:
                    stop_l, stop_s = np.maximum(up[:, i], twap[:, i]), np.minimum(dn[:, i], twap[:, i])
                    cur = np.where((cur > 0) & (C[:, i] < stop_l), 0.0, cur)
                    cur = np.where((cur < 0) & (C[:, i] > stop_s), 0.0, cur)
                    go_l = (C[:, i] > up[:, i]) & (C[:, i] > twap[:, i]) & (sides != "short")
                    go_s = (C[:, i] < dn[:, i]) & (C[:, i] < twap[:, i]) & (sides != "long")
                    cur = np.where(go_l, 1.0, np.where(go_s, -1.0, cur))
                    cur = np.where(np.isnan(up[:, i]), 0.0, cur)
                pos[:, i] = cur
            if vf == "hivol":
                pos = pos * hi_vol[:, None]
            out[f"NOISE3 L{Lb} vm{vm} f{f} {sides} {vf}"] = possim(O, C, shift_hold(pos), dO)[:2]
    return out


def f_rev(d):
    """Yesterday's close-to-close move: fade or follow it from today's open."""
    O, C = d["O"], d["C"]
    n = len(O)
    r = (d["pC"] / d["ppC"] - 1) * 100
    out = {}
    for th, mode, e, x in itertools.product((0.5, 1.0, 1.5), ("fade", "follow"), (0, 15, 30), (165, NB - 1)):
        side = -np.sign(r) if mode == "fade" else np.sign(r)
        ent = np.where((np.abs(r) >= th) & (side != 0), e, -1)
        out[f"REV {mode} th{th} e{e} x{x}"] = sim(d, ent, side, np.full(n, NAN), np.full(n, NAN), np.full(n, x))
    return out


def expiry_days(D):
    """Nifty weekly expiry (Thursday; Tuesday from 1-Sep-2025; monthly-only before 11-Feb-2019), holiday -> previous
    trading day. Calendar rule only - no option data."""
    D = pd.DatetimeIndex(D)
    s = set(D)
    out = set()
    for day in pd.date_range(D[0], D[-1] + pd.Timedelta(days=7)):
        wd = 1 if day >= pd.Timestamp("2025-09-01") else 3
        if day.weekday() != wd:
            continue
        if day < pd.Timestamp("2019-02-11") and (day + pd.Timedelta(days=7)).month == day.month:
            continue
        e = day
        while e not in s and e > D[0]:
            e -= pd.Timedelta(days=1)
        out.add(e)
    return np.array([x in out for x in D])


def f_cal(d):
    O, C = d["O"], d["C"]
    n = len(O)
    D = pd.DatetimeIndex(d["D"])
    nxt = np.r_[D[1:], D[-1] + pd.Timedelta(days=1)]
    month_end = D.month != pd.DatetimeIndex(nxt).month
    prv = np.r_[D[0] - pd.Timedelta(days=1), D[:-1]]
    month_start = D.month != pd.DatetimeIndex(prv).month
    second = np.r_[False, month_start[:-1]]
    pre_hol = ((pd.DatetimeIndex(nxt) - D).days > 1) & ~(D.dayofweek == 4)            # gap other than a weekend
    pre_hol |= (D.dayofweek == 4) & ((pd.DatetimeIndex(nxt) - D).days > 3)
    exp = expiry_days(D)
    sets = {"tom": month_end | month_start | second, "prehol": pre_hol, "expiry": exp,
            "preexpiry": np.r_[exp[1:], False]}
    out = {}
    for nm, mask in sets.items():
        for side, (a, b) in itertools.product((1.0, -1.0), ((0, NB), (0, 165), (165, NB), (225, NB))):
            ent = np.where(mask, a, -1)
            out[f"CAL {nm} {'long' if side > 0 else 'short'} {a}-{b - 1}"] = sim(
                d, ent, np.full(n, side), np.full(n, NAN), np.full(n, NAN), np.full(n, b - 1))
    return out


def f_midday(d):
    """Iteration 4: move from the open to bar k -> follow or fade it into the close (or for 60 min)."""
    O, C = d["O"], d["C"]
    n = len(O)
    out = {}
    for k, th, mode, x in itertools.product((105, 165, 225), (0.3, 0.6, 1.0), ("follow", "fade"), (60, 999)):
        r = (C[:, k - 1] / O[:, 0] - 1) * 100
        side = np.sign(r) if mode == "follow" else -np.sign(r)
        ent = np.where((np.abs(r) >= th) & (side != 0), k, -1)
        xt = np.full(n, min(k + x, NB) - 1)
        out[f"MIDDAY k{k} th{th} {mode} x{x}"] = sim(d, ent, side, np.full(n, NAN), np.full(n, NAN), xt)
    return out


def f_prevlast(d):
    """Iteration 4: yesterday's last hour (14:15 -> 15:14) -> today's first 60/105 minutes."""
    O, C = d["O"], d["C"]
    n = len(O)
    last = d["plast"]
    out = {}
    for th, mode, x in itertools.product((0.2, 0.4, 0.6), ("follow", "fade"), (60, 105)):
        side = np.sign(last) if mode == "follow" else -np.sign(last)
        ent = np.where((np.abs(last) >= th) & (side != 0), 0, -1)
        out[f"PREVLAST th{th} {mode} x{x}"] = sim(d, ent, np.nan_to_num(side), np.full(n, NAN), np.full(n, NAN),
                                                 np.full(n, x - 1))
    return out


def f_pin(d):
    """Iteration 4: expiry-day pinning - in the afternoon, trade toward the nearest round level (50/100 grid);
    target = the level, exit 15:14. Same rule on non-expiry days as the control."""
    O, C = d["O"], d["C"]
    n = len(O)
    exp = expiry_days(d["D"])
    out = {}
    for a, step, frac, days in itertools.product((225, 285), (50, 100), (0.2, 0.3, 0.4), ("expiry", "other")):
        px = C[:, a - 1]
        lvl = np.round(px / step) * step
        dist = px - lvl
        side = -np.sign(dist)
        mask = exp if days == "expiry" else ~exp
        ent = np.where(mask & (np.abs(dist) >= frac * step) & (side != 0), a, -1)
        out[f"PIN a{a} step{step} frac{frac} {days}"] = sim(d, ent, side, np.full(n, NAN), lvl, np.full(n, NB - 1))
    return out


FAMILIES = {"IMOM": f_imom, "NOISE": f_noise, "ORB": f_orb, "GAP": f_gap, "PDHL": f_pdhl, "CPR": f_cpr,
            "MR": f_mr, "TREND": f_trend, "SEASON": f_season,
            "NOISE2": f_noise2, "REV": f_rev, "CAL": f_cal, "NOISE3": f_noise3,
            "MIDDAY": f_midday, "PREVLAST": f_prevlast, "PIN": f_pin}


# ================================================================ metrics
def net_of(g, trades, cost):
    return np.where(np.isnan(g), 0.0, g - trades * cost)


def metrics(dates, g, trades, cost_key="stress", lo=None, hi=None):
    D = pd.DatetimeIndex(dates)
    m = np.ones(len(D), bool)
    if lo:
        m &= D >= pd.Timestamp(lo)
    if hi:
        m &= D <= pd.Timestamp(hi)
    nt = trades[m].sum()
    if nt == 0:
        return {"trades": 0}
    net = net_of(g[m], trades[m], COST[cost_key])
    netb = net_of(g[m], trades[m], COST["base"])
    traded = ~np.isnan(g[m])
    eq = np.cumsum(net)
    dd = (eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min()
    pos, neg = netb[traded][netb[traded] > 0].sum(), -netb[traded][netb[traded] < 0].sum()
    yrs = pd.Series(net, index=D[m]).groupby(D[m].year).sum()
    sd = net.std(ddof=1)
    return {"trades": int(nt), "net%": net.sum(), "per_trade%": net.sum() / nt,
            "t_daily": net.mean() / (sd / np.sqrt(len(net))) if sd > 0 else NAN,
            "pf_base": pos / neg if neg > 0 else np.inf, "win_days%": (netb[traded] > 0).mean() * 100,
            "maxdd%": dd, "rf": net.sum() / -dd if dd < 0 else np.inf,
            "years_pos": f"{(yrs > 0).sum()}/{len(yrs)}", "yearly": yrs.round(2).to_dict()}


def neighbours(names, name):
    """Configs of the same family differing in exactly one parameter by one grid step."""
    import re

    def key(tok):
        num = re.findall(r"-?\d+\.?\d*", tok)
        return (re.sub(r"-?\d+\.?\d*", "#", tok), float(num[-1]) if num else 0.0, tok)
    fam = name.split()[0]
    toks = name.split()[1:]
    same = [n for n in names if n.split()[0] == fam and len(n.split()) == len(name.split())]
    out = []
    for n in same:
        t = n.split()[1:]
        diff = [i for i in range(len(toks)) if t[i] != toks[i]]
        if len(diff) == 1 and re.search(r"\d", toks[diff[0]]):      # categorical tokens have no neighbours
            i = diff[0]
            vals = sorted({x.split()[1:][i] for x in same}, key=key)
            if abs(vals.index(t[i]) - vals.index(toks[i])) == 1:     # one grid step either way
                out.append(n)
    return out


# ================================================================ commands
def cmd_dev():
    t0 = time.time()
    d = load()
    print(f"{len(d['D'])} days {pd.Timestamp(d['D'][0]).date()} .. {pd.Timestamp(d['D'][-1]).date()}  ({time.time() - t0:.0f}s)")
    G, T = {}, {}
    for fam, fn in FAMILIES.items():
        t1 = time.time()
        res = fn(d)
        for k, (g, tr) in res.items():
            G[k], T[k] = g.astype(np.float32), tr.astype(np.int16)
        print(f"  {fam}: {len(res)} configs  {time.time() - t1:.0f}s", flush=True)
    np.savez_compressed(OUT / "results.npz", names=np.array(list(G)), D=d["D"],
                        G=np.stack(list(G.values())), T=np.stack(list(T.values())))
    rank(G, T, d["D"])
    print(f"done in {time.time() - t0:.0f}s")


def load_results():
    z = np.load(OUT / "results.npz", allow_pickle=True)
    names = list(z["names"])
    return names, z["D"], {n: z["G"][i].astype(float) for i, n in enumerate(names)}, \
        {n: z["T"][i].astype(int) for i, n in enumerate(names)}


def rank(G, T, D):
    rows = []
    for k in G:
        m = metrics(D, G[k], T[k], "stress", *DEV)
        if m.get("trades", 0) == 0:
            continue
        rows.append({"name": k, "family": k.split()[0], **{x: m[x] for x in
                     ("trades", "net%", "per_trade%", "t_daily", "pf_base", "maxdd%", "years_pos")}})
    df = pd.DataFrame(rows)
    names = list(G)
    nb = []
    for r in df.itertuples():
        nbs = neighbours(names, r.name)
        vals = [metrics(D, G[n], T[n], "stress", *DEV).get("net%", 0) for n in nbs]
        nb.append(np.mean(vals) if vals else NAN)
    df["nbr_net%"] = nb
    # user request 2026-10-20: 2020 (COVID crash) must not carry a strategy -> DEV without 2020 must also be > 0
    Dd = pd.DatetimeIndex(D)
    ex = (Dd <= pd.Timestamp(DEV[1])) & (Dd.year != 2020)
    df["dev_ex2020_net%"] = [net_of(G[k][ex], T[k][ex], COST["stress"]).sum() for k in df["name"]]
    df.to_csv(OUT / "dev_rank.csv", index=False)
    pd.set_option("display.width", 230)
    print(f"\nDEV 2018-2022, stress cost {COST['stress']}%: {len(df)} configs, {(df['net%'] > 0).sum()} net-positive, "
          f"{((df['net%'] > 0) & (df['nbr_net%'] > 0) & (df['trades'] >= 150)).sum()} positive with positive neighbourhood and >= 150 trades")
    for fam, g in df.groupby("family"):
        g = g.sort_values("t_daily", ascending=False)
        print(f"\n[{fam}] {len(g)} configs, {(g['net%'] > 0).sum()} positive. Best 3 by daily t:")
        print(g.head(3).drop(columns=["family"]).round(3).to_string(index=False))


def log_view(kind, names):
    p = OUT / "views_log.csv"
    pd.DataFrame([{"when": pd.Timestamp.now(), "split": kind, "configs": " | ".join(names)}]).to_csv(
        p, mode="a", header=not p.exists(), index=False)


def finalists(df, fam, k=2):
    g = df[(df["family"] == fam) & (df["net%"] > 0) & (df["nbr_net%"] > 0) & (df["trades"] >= 150)
           & (df["dev_ex2020_net%"] > 0)]
    return list(g.sort_values("t_daily", ascending=False)["name"].head(k))


def cmd_validate(fams):
    names, D, G, T = load_results()
    df = pd.read_csv(OUT / "dev_rank.csv")
    sel = [n for f in fams for n in finalists(df, f)]
    if not sel:
        print("no DEV finalists (net > 0, neighbourhood > 0, >= 150 trades) in", fams)
        return
    log_view("VALIDATION", sel)
    rows = []
    for n in sel:
        v = metrics(D, G[n], T[n], "stress", *VAL)
        dv = metrics(D, G[n], T[n], "stress", *DEV)
        rows.append({"name": n, "dev_net%": dv["net%"], "dev_t": dv["t_daily"], **{f"val_{x}": v.get(x) for x in
                     ("trades", "net%", "per_trade%", "t_daily", "pf_base", "maxdd%", "years_pos")},
                     "VAL_OK": v.get("net%", -1) > 0 and v.get("pf_base", 0) >= 1.10})
    print(pd.DataFrame(rows).round(3).to_string(index=False))


def walk_forward(names, D, G, T, fam):
    """Each year 2021..2026: pick the family config with the best daily t on all earlier years, trade that year."""
    Dd = pd.DatetimeIndex(D)
    fam_names = [n for n in names if n.split()[0] == fam]
    tot, picks = [], {}
    for y in range(2021, 2027):
        best, bt = None, -np.inf
        for n in fam_names:
            m = metrics(D, G[n], T[n], "stress", "2018-01-01", f"{y - 1}-12-31")
            if m.get("trades", 0) >= 100 and m["t_daily"] > bt:
                best, bt = n, m["t_daily"]
        picks[y] = best
        sel = (Dd.year == y)
        tot.append(net_of(G[best][sel], T[best][sel], COST["stress"]).sum())
    return float(np.sum(tot)), dict(zip(range(2021, 2027), np.round(tot, 2))), picks


def cmd_test(sel):
    names, D, G, T = load_results()
    nbr = pd.read_csv(OUT / "dev_rank.csv").set_index("name")["nbr_net%"]
    log_view("TEST", sel)
    for n in sel:
        dv = metrics(D, G[n], T[n], "stress", *DEV)
        v = metrics(D, G[n], T[n], "stress", *VAL)
        te = metrics(D, G[n], T[n], "stress", *TEST)
        oo = metrics(D, G[n], T[n], "stress", *OOS)
        wf_tot, wf_y, picks = walk_forward(names, D, G, T, n.split()[0])
        crit = {
            "1 DEV net>0 & nbr>0": dv["net%"] > 0 and nbr.get(n, -1) > 0,
            "2 VAL net>0 & PF>=1.10": v.get("net%", -1) > 0 and v.get("pf_base", 0) >= 1.10,
            "3 TEST net>0 & PF>=1.10": te.get("net%", -1) > 0 and te.get("pf_base", 0) >= 1.10,
            "4 OOS t>=2, >=150 trades, >=3/4 yrs, RF>=1.5": oo.get("t_daily", 0) >= 2 and oo.get("trades", 0) >= 150
            and int(oo.get("years_pos", "0/1").split("/")[0]) >= 3 and oo.get("rf", 0) >= 1.5,
            "5 walk-forward 2021-26 > 0": wf_tot > 0,
        }
        print(f"\n=== {n}")
        for lab, m in (("DEV", dv), ("VAL", v), ("TEST", te), ("OOS 2023-26", oo)):
            print(f"  {lab:12s} " + ", ".join(f"{k}={m[k]:.3f}" if isinstance(m.get(k), float) else f"{k}={m.get(k)}"
                                             for k in ("trades", "net%", "per_trade%", "t_daily", "pf_base", "maxdd%", "rf", "years_pos")))
        print(f"  OOS yearly net%: {oo.get('yearly')}")
        print(f"  walk-forward 2021-26 net% {wf_tot:.2f} {wf_y}  picks {picks}")
        for k, ok in crit.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {k}")
        print(f"  => {'WORKS' if all(crit.values()) else 'does not work'}")


def cmd_final():
    """Amendment 1: mechanical finalists from 2018-2024 (blocks A = 2018-22 ex-2020, B = 2023-24), then ONE TEST look."""
    names, D, G, T = load_results()
    df = pd.read_csv(OUT / "dev_rank.csv").set_index("name")
    Dd = pd.DatetimeIndex(D)
    A = (Dd <= "2022-12-31") & (Dd.year != 2020)
    B = (Dd >= "2023-01-01") & (Dd <= "2024-12-31")
    rows = []
    for n in names:
        net = net_of(G[n], T[n], COST["stress"])
        tA = net[A].mean() / (net[A].std(ddof=1) / np.sqrt(A.sum()))
        tB = net[B].mean() / (net[B].std(ddof=1) / np.sqrt(B.sum()))
        nbr = df.loc[n, "nbr_net%"] if n in df.index else NAN
        rows.append({"name": n, "family": n.split()[0], "netA%": net[A].sum(), "netB%": net[B].sum(), "tA": tA, "tB": tB,
                     "trades_18_24": int(T[n][Dd <= "2024-12-31"].sum()), "nbr_net%": nbr, "score": min(tA, tB)})
    r = pd.DataFrame(rows)
    ok = r[(r["netA%"] > 0) & (r["netB%"] > 0) & (r["trades_18_24"] >= 150) & ~(r["nbr_net%"] <= 0)]
    print(f"{len(r)} configs; {len(ok)} pass the amended development rule (both blocks > 0, >= 150 trades, neighbourhood)")
    best = ok.sort_values("score", ascending=False).groupby("family").head(1).sort_values("score", ascending=False).head(5)
    pd.set_option("display.width", 220)
    print(best.round(3).to_string(index=False))
    best.to_csv(OUT / "finalists_amendment1.csv", index=False)
    if len(best):
        cmd_test(list(best["name"]))


def cmd_show(n):
    names, D, G, T = load_results()
    m = metrics(D, G[n], T[n], "stress", *DEV)
    print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()}, indent=1, default=str))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if cmd == "dev":
        cmd_dev()
    elif cmd == "validate":
        cmd_validate(sys.argv[2:])
    elif cmd == "test":
        cmd_test(sys.argv[2:])
    elif cmd == "final":
        cmd_final()
    elif cmd == "show":
        cmd_show(" ".join(sys.argv[2:]))
