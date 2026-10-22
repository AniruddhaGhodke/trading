"""Backtest the live assist system (alerts + checklist GO) as a mechanical strategy on Nifty 1m, 2018+.

Uses the LIVE code (assist.features / Detector / checklist), driven like `assist.py calibrate` (no snapshots, no
option bars -> the premium-chase check is WARN and doesn't block GO, so some chased entries are taken that live
would skip). Results are in NIFTY INDEX POINTS; option premiums are only a secondary estimate (Black-Scholes on
VIX, checked against the real recorded contracts where they exist).

Rules fixed before the first run (2026-10-19), NOT tuned:
  entries   GO     : first bar 09:30-12:00 whose checklist verdict is GO (CE or PE), while flat
            BREAK  : a BREAKOUT alert, in its direction          TREND : a TREND alert (= chasing it)
            GAP    : a GAP (failing gap) alert, against the gap
            entry fill = next bar's open
  exits     Nifty closes through the structure stop (frozen at entry), or close beyond 9-EMA beyond 21-EMA
            against the trade -> next bar's open; trail: arms at +18 Nifty pts (= +10 premium at delta 0.55),
            exits 27 pts (= 15 premium) off the best extreme, filled at that level (or the open if it gapped);
            time exit 15:10. Same bar: the trail is checked against the bar's adverse extreme BEFORE its
            favourable extreme counts (worse case first).
  costs     round trip in Nifty pts: base 7, stress 10 (1 pt charges + 0.5 spread + 2-3 slippage in premium,
            / delta 0.55). Gross also shown.
  controls  (a) same day, random bar in 09:30-12:00, SAME direction, same exits  -> does the timing add anything?
            (b) same bar, RANDOM direction, same exits                          -> does the direction read add anything?
  layers    per-signal (one position at a time, no day caps) and capped (max 2 trades/day, stop at -Rs 1,000)
  "works"   positive after STRESS costs, beats both controls with monthly t > 2, positive in most years and in
            both halves (2018-2022 / 2023+).

  python alert_backtest.py                 run everything, write SwingSystem/data/lab/alert_backtest/
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import assist
import data_source
import options
from assist import CFG, Detector, checklist, features, hm

OUT = Path(__file__).resolve().parent.parent / "SwingSystem" / "data" / "lab" / "alert_backtest"
DELTA, LOT = 0.55, 65
ARM, GAP_PTS = CFG["trail_arm"] / DELTA, CFG["trail_gap"] / DELTA       # ~18.2 / ~27.3 Nifty pts
COST = {"gross": 0.0, "base": 7.0, "stress": 10.0}
EXIT_AT = 15 * 60 + 10                 # minutes since midnight (bar open)
REPS = 20
RNG = np.random.default_rng(20261019)


class _Det:
    """Frozen detector state of one bar, for checklist()."""
    def __init__(self, day, regime, raw):
        self.day, self.regime, self.raw = day, regime, raw


def _book(entries=0, net=0.0):
    return {"ok": True, "unmatched": [], "open": [], "entries": entries, "net": net, "closed": []}


SNAP = {"options": {}, "expiry": {"lot": LOT}}


# ------------------------------------------------------------------ one day
def clean_day(day_bars: pd.DataFrame, d) -> bool:
    s = day_bars[(day_bars.index >= d + hm("09:15")) & (day_bars.index < d + pd.Timedelta(minutes=EXIT_AT))]
    if len(s) < 340:
        return False
    return s.index.to_series().diff().max() <= pd.Timedelta(minutes=3)


def run_day(f: pd.DataFrame, d, prev):
    """Detector over the day -> per-bar state + alerts."""
    det = Detector(d, prev)
    states, alerts = [], []
    for i, (ts, r) in enumerate(f.iterrows()):
        for kind, text in det.step(ts + pd.Timedelta("1min"), r, {}):
            if kind in ("TREND", "BREAKOUT", "GAP", "RANGE"):
                if kind == "RANGE":
                    side = None
                elif kind == "GAP":
                    side = "PE" if "Gap-up" in text else "CE"
                else:
                    side = "CE" if ("UP" in text and "Breakdown" not in text) else "PE"
                alerts.append((i, kind, side))
        states.append((det.regime, det.raw))
    return states, alerts


def simulate_exit(a, j, side, stop, fixed=None):
    """Enter at a['O'][j]. Returns (exit_index, exit_price, reason, gross_pts)."""
    O, H, L, C, e9, e21, tod = a["O"], a["H"], a["L"], a["C"], a["e9"], a["e21"], a["tod"]
    n = len(O)
    ce = side == "CE"
    sg = 1 if ce else -1
    entry = O[j]
    hwm, armed = entry, False
    for k in range(j, n):
        if tod[k] >= EXIT_AT or (fixed is not None and k >= j + fixed):
            return k, O[k], "time" if fixed is None or tod[k] >= EXIT_AT else "fixed", sg * (O[k] - entry)
        if fixed is None:
            if armed:
                lvl = hwm - sg * GAP_PTS
                adverse = L[k] if ce else H[k]
                if sg * (adverse - lvl) <= 0:
                    px = O[k] if sg * (O[k] - lvl) <= 0 else lvl
                    return k, px, "trail", sg * (px - entry)
            fav = H[k] if ce else L[k]
            if sg * (fav - hwm) > 0:
                hwm = fav
            if sg * (hwm - entry) >= ARM:
                armed = True
            if k + 1 < n:
                if sg * (C[k] - stop) < 0:
                    return k + 1, O[k + 1], "stop", sg * (O[k + 1] - entry)
                if (ce and C[k] < e9[k] < e21[k]) or (not ce and C[k] > e9[k] > e21[k]):
                    return k + 1, O[k + 1], "ema", sg * (O[k + 1] - entry)
    return n - 1, C[-1], "eod", sg * (C[-1] - entry)


def arrays(f):
    return {"O": f["Open"].to_numpy(float), "H": f["High"].to_numpy(float), "L": f["Low"].to_numpy(float),
            "C": f["Close"].to_numpy(float), "e9": f["ema9"].to_numpy(float), "e21": f["ema21"].to_numpy(float),
            "lo": f["low_n"].to_numpy(float), "hi": f["high_n"].to_numpy(float),
            "tod": ((f.index - f.index.normalize()).total_seconds() // 60).to_numpy(int)}


def go_trades(f, a, d, states, capped: bool, fixed=None):
    """Scan for GO entries while flat. capped: live day rules (2 trades, Rs 1,000 cap at base cost)."""
    out, i, n = [], 0, len(f)
    entries, net_rs = 0, 0.0
    lo_i = int(np.searchsorted(a["tod"], hm(CFG["setup_from"]).total_seconds() // 60 - 1))
    hi_t = hm(CFG["setup_until"])
    i = lo_i
    rows = f.to_dict("index")
    idx = f.index
    while i < n - 1:
        t = idx[i] + pd.Timedelta("1min")
        if t - d > hi_t:
            break
        if capped and (entries >= CFG["max_trades"] or net_rs <= -CFG["loss_cap"]):
            break
        r = rows[idx[i]]
        det = _Det(d, *states[i])
        b = _book(entries, net_rs) if capped else _book()
        hit = None
        if det.regime in ("up", "down"):
            side = "CE" if det.regime == "up" else "PE"
            if checklist(side, r, det, SNAP, b, t)["verdict"] == "GO":
                hit = side
        if hit is None:
            i += 1
            continue
        stop = a["lo"][i] if hit == "CE" else a["hi"][i]
        k, px, why, g = simulate_exit(a, i + 1, hit, stop, fixed)
        out.append({"day": d, "i": i, "time": idx[i + 1], "side": hit, "entry": a["O"][i + 1], "exit": px,
                    "reason": why, "gross": g, "bars": k - (i + 1)})
        entries += 1
        net_rs += (g - COST["base"]) * DELTA * LOT
        i = k + 1
    return out


def alert_trades(a, d, idx, alerts, kind):
    out, busy_until = [], -1
    for i, k_, side in alerts:
        if k_ != kind or side is None or i + 1 >= len(a["O"]) or i <= busy_until:
            continue
        stop = a["lo"][i] if side == "CE" else a["hi"][i]
        k, px, why, g = simulate_exit(a, i + 1, side, stop)
        out.append({"day": d, "i": i, "time": idx[i + 1], "side": side, "entry": a["O"][i + 1], "exit": px,
                    "reason": why, "gross": g, "bars": k - (i + 1)})
        busy_until = k
    return out


def controls(a, d, window, trades, fixed=None):
    """(a) random bar same direction, (b) same bar random direction. REPS draws each."""
    ra, rb = [], []
    for tr in trades:
        for _ in range(REPS):
            i = int(RNG.choice(window))
            side = tr["side"]
            stop = a["lo"][i] if side == "CE" else a["hi"][i]
            ra.append((d, simulate_exit(a, i + 1, side, stop, fixed)[3]))
            side = "CE" if RNG.random() < 0.5 else "PE"
            i = tr["i"]
            stop = a["lo"][i] if side == "CE" else a["hi"][i]
            rb.append((d, simulate_exit(a, i + 1, side, stop, fixed)[3]))
    return ra, rb


def alert_moves(a, alerts, window):
    """Forward 15/30-min moves after TREND/BREAKOUT (signed) and RANGE (unsigned), plus random-bar controls."""
    C, H, L = a["C"], a["H"], a["L"]
    n = len(C)

    def mv(i, sg):
        if i + 30 >= n:
            return None
        return {"f15": sg * (C[i + 15] - C[i]), "f30": sg * (C[i + 30] - C[i]),
                "mfe30": max(sg * (H[i + 1:i + 31] - C[i]).max(), sg * (L[i + 1:i + 31] - C[i]).max()),
                "mae30": min(sg * (H[i + 1:i + 31] - C[i]).min(), sg * (L[i + 1:i + 31] - C[i]).min()),
                "abs30": abs(C[i + 30] - C[i]), "rng30": H[i + 1:i + 31].max() - L[i + 1:i + 31].min()}
    out = []
    for i, kind, side in alerts:
        sg = 0 if side is None else (1 if side == "CE" else -1)
        m = mv(i, sg if sg else 1)
        if m is None:
            continue
        out.append({"kind": kind, "ctrl": False, **m})
        for _ in range(REPS):
            j = int(RNG.choice(window))
            mc = mv(j, sg if sg else 1)
            if mc is not None:
                out.append({"kind": kind, "ctrl": True, **mc})
    return out


def option_layer(trades: pd.DataFrame) -> pd.DataFrame:
    """Premium P&L of each GO trade on the ATM contract of the next expiry (never the expiring one):
    model = Black-Scholes on VIX; real = recorded Angel One candles where they exist."""
    rows = []
    for tr in trades.itertuples():
        t0 = pd.Timestamp(tr.time)
        t1 = t0 + pd.Timedelta(minutes=int(tr.bars))
        exps = [e for e in options.expiries_after(t0, 3) if pd.Timestamp(e["expiry"]).normalize() != t0.normalize()]
        if not exps:
            continue
        e = pd.Timestamp(exps[0]["expiry"])
        K = round(tr.entry / options.STEP) * options.STEP
        sig0, sig1 = options.vix_at(t0)[0] / 100, options.vix_at(t1)[0] / 100
        p0 = float(options.price(tr.entry, K, options.years(t0, e)[0], sig0, tr.side))
        p1 = float(options.price(tr.exit, K, options.years(t1, e)[0], sig1, tr.side))
        real = np.nan
        raw = options.real_raw(options.make(e, K, tr.side))
        if raw is not None and t0 in raw.index and t1 in raw.index:
            real = float(raw.loc[t1, "Open"] - raw.loc[t0, "Open"])
        rows.append({"time": t0, "model_pts": p1 - p0, "real_pts": real, "index_x_delta": tr.gross * DELTA})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ stats
def tstat(x):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def summarize(name, tr, ca, cb):
    if not len(tr):
        return {"entry": name, "n": 0}
    tr = tr.copy()
    tr["month"] = pd.to_datetime(tr["day"]).dt.to_period("M")
    res = {"entry": name, "n": len(tr), "per_day": round(len(tr) / tr["day"].nunique(), 2),
           "gross": tr["gross"].mean(), "net_base": tr["gross"].mean() - COST["base"],
           "net_stress": tr["gross"].mean() - COST["stress"], "win_stress%": (tr["gross"] > COST["stress"]).mean() * 100,
           "t_stress": tstat(tr["gross"] - COST["stress"])}
    for lab, c in (("ctlA_time", ca), ("ctlB_dir", cb)):
        c = c.copy()
        c["month"] = pd.to_datetime(c["day"]).dt.to_period("M")
        m = tr.groupby("month")["gross"].mean() - c.groupby("month")["gross"].mean()
        res[lab] = c["gross"].mean()
        res[lab + "_edge"] = tr["gross"].mean() - c["gross"].mean()
        res[lab + "_t_month"] = tstat(m.dropna())
        res[lab + "_beat%months"] = (m.dropna() > 0).mean() * 100
    tr["year"] = pd.to_datetime(tr["day"]).dt.year
    yr = tr.groupby("year")["gross"].mean() - COST["stress"]
    res["years_pos"] = f"{(yr > 0).sum()}/{len(yr)}"
    half = np.where(tr["year"] <= 2022, "2018-22", "2023+")
    for h, g in tr.groupby(half)["gross"]:
        res[f"stress_{h}"] = g.mean() - COST["stress"]
    return res


def main():
    t0 = time.time()
    spot = data_source.load(options.SPOT, "1m")
    days = pd.DatetimeIndex(spot.index.normalize().unique())
    days = days[days >= pd.Timestamp(sys.argv[1] if len(sys.argv) > 1 else "2018-01-01")]
    if len(sys.argv) > 2:
        days = days[days <= pd.Timestamp(sys.argv[2])]
    global OUT
    if len(sys.argv) > 1:
        OUT = OUT / "smoke"          # partial runs never overwrite (or count as) the real view
    T = {k: [] for k in ("GO", "GO_capped", "GO_fixed30", "BREAK", "TREND", "GAP")}
    CA = {k: [] for k in T}
    CB = {k: [] for k in T}
    moves, skipped, used = [], 0, 0
    for n_day, d in enumerate(days):
        i0 = int(spot.index.searchsorted(d))
        i1 = int(spot.index.searchsorted(d + pd.Timedelta(days=1)))
        if i0 == 0 or not clean_day(spot.iloc[i0:i1], d):
            skipped += 1
            continue
        nb = spot.iloc[max(0, i0 - 60):i1]
        nb = nb[nb.index < d + hm("15:30")].copy()
        prev = float(spot.iloc[i0 - 1]["Close"])
        f = features(nb, d)
        if len(f) < 340:
            skipped += 1
            continue
        used += 1
        a = arrays(f)
        states, alerts = run_day(f, d, prev)
        tod = a["tod"]
        window = np.where((tod >= 9 * 60 + 29) & (tod < 12 * 60) & (np.arange(len(tod)) < len(tod) - 2))[0]
        day_tr = {"GO": go_trades(f, a, d, states, capped=False),
                  "GO_capped": go_trades(f, a, d, states, capped=True),
                  "GO_fixed30": go_trades(f, a, d, states, capped=False, fixed=30),
                  "BREAK": alert_trades(a, d, f.index, alerts, "BREAKOUT"),
                  "TREND": alert_trades(a, d, f.index, alerts, "TREND"),
                  "GAP": alert_trades(a, d, f.index, alerts, "GAP")}
        for k, trs in day_tr.items():
            T[k] += trs
            if k != "GO_capped":
                ra, rb = controls(a, d, window, trs, fixed=30 if k == "GO_fixed30" else None)
                CA[k] += ra
                CB[k] += rb
        moves += [m | {"day": d} for m in alert_moves(a, alerts, window)]
        if n_day % 100 == 0:
            print(f"  {d.date()}  days used {used}  GO trades {len(T['GO'])}  {time.time() - t0:.0f}s", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for k in T:
        tr = pd.DataFrame(T[k])
        tr.to_csv(OUT / f"trades_{k}.csv", index=False)
        ca = pd.DataFrame(CA[k] or CA["GO"], columns=["day", "gross"])
        cb = pd.DataFrame(CB[k] or CB["GO"], columns=["day", "gross"])
        rows.append(summarize(k, tr, ca, cb))
    summ = pd.DataFrame(rows).set_index("entry")
    summ.to_csv(OUT / "summary.csv")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(f"\nDays used {used}, skipped {skipped} (partial / gaps). Costs per round trip in Nifty pts: {COST}")
    print(summ.round(2).T.to_string())

    # capped layer in rupees
    cp = pd.DataFrame(T["GO_capped"])
    if len(cp):
        cp["rs_stress"] = (cp["gross"] - COST["stress"]) * DELTA * LOT
        dd = cp.groupby("day")["rs_stress"].sum()
        eq = dd.cumsum()
        print(f"\nCapped layer (2 trades, Rs 1,000 cap, stress costs, 1 lot {LOT}, delta {DELTA}): "
              f"{len(cp)} trades on {len(dd)} days, Rs {dd.sum():+,.0f} total, Rs {dd.mean():+.0f}/trading day, "
              f"worst day Rs {dd.min():+,.0f}, max drawdown Rs {(eq - eq.cummax()).min():+,.0f}")
        print("by year (Rs):", cp.groupby(pd.to_datetime(cp["day"]).dt.year)["rs_stress"].sum().round(0).to_dict())

    for k in ("GO", "BREAK", "TREND"):
        tr = pd.DataFrame(T[k])
        if len(tr):
            print(f"\n{k} exit reasons:", tr["reason"].value_counts().to_dict(),
                  f"| avg hold {tr['bars'].mean():.0f} min")

    mv = pd.DataFrame(moves)
    mv.to_csv(OUT / "alert_moves.csv", index=False)
    if len(mv):
        print("\nAlert usefulness: forward Nifty move after the alert vs random 09:30-12:00 bars (same day)")
        g = mv.groupby(["kind", "ctrl"])[["f15", "f30", "mfe30", "mae30", "abs30", "rng30"]].mean().round(1)
        g["n"] = mv.groupby(["kind", "ctrl"]).size()
        print(g.to_string())
        for kind in mv["kind"].unique():
            s, c = mv[(mv.kind == kind) & ~mv.ctrl], mv[(mv.kind == kind) & mv.ctrl]
            col = "rng30" if kind == "RANGE" else "f30"
            diff = s.groupby(pd.to_datetime(s["day"]).dt.to_period("M"))[col].mean() - \
                c.groupby(pd.to_datetime(c["day"]).dt.to_period("M"))[col].mean()
            print(f"  {kind:8s} {col}: alert {s[col].mean():+.1f} vs random {c[col].mean():+.1f}  "
                  f"(monthly t {tstat(diff.dropna()):+.1f}, n alerts {len(s)})")

    ol = option_layer(pd.DataFrame(T["GO"]))
    if len(ol):
        ol.to_csv(OUT / "option_layer.csv", index=False)
        print(f"\nOption layer (GO trades, ATM next expiry, BS on VIX): model premium change avg {ol['model_pts'].mean():+.2f} pts "
              f"vs index x delta {ol['index_x_delta'].mean():+.2f}; after ~3.5 pts premium costs: {ol['model_pts'].mean() - 3.5:+.2f}")
        rv = ol.dropna(subset=["real_pts"])
        if len(rv):
            print(f"  real vs model on {len(rv)} trades with recorded contracts: real {rv['real_pts'].mean():+.2f}, "
                  f"model {rv['model_pts'].mean():+.2f}, corr {rv['real_pts'].corr(rv['model_pts']):.2f}")
        else:
            print("  no GO trade overlaps a recorded contract - model premiums unvalidated")

    log = OUT / "views_log.csv"
    pd.DataFrame([{"when": pd.Timestamp.now(), "what": "first as-is run, all years (counts as the holdout view)",
                   "cfg": json.dumps(CFG)}]).to_csv(log, mode="a", header=not log.exists(), index=False)
    print(f"\nDone in {time.time() - t0:.0f}s -> {OUT}")


def usefulness():
    """Do the alerts tell you WHERE to look? Detector only. Control = the SAME minute of day on 20 random days within
    +-30 trading days (no time-of-day or price-level bias; control (a) of main() leaked the day's direction).
    Pass bar, fixed before the run (2026-10-20):
      RANGE          useful as "stand aside" if the next-30-min range (in ATR) is >= 10% smaller than control, monthly t < -2
      TREND/BREAKOUT useful if the signed forward 30-min move (ATR) is clearly > 0 (monthly t > 2), or the next-30-min
                     range is >= 10% larger (more room for an option buyer)
    """
    t0 = time.time()
    spot = data_source.load(options.SPOT, "1m")
    days = pd.DatetimeIndex(spot.index.normalize().unique())
    days = days[days >= pd.Timestamp("2018-01-01")]
    store, alerts_all = {}, []
    for n_day, d in enumerate(days):
        i0 = int(spot.index.searchsorted(d))
        i1 = int(spot.index.searchsorted(d + pd.Timedelta(days=1)))
        if i0 == 0 or not clean_day(spot.iloc[i0:i1], d):
            continue
        nb = spot.iloc[max(0, i0 - 60):i1]
        nb = nb[nb.index < d + hm("15:30")].copy()
        f = features(nb, d)
        if len(f) < 340:
            continue
        a = arrays(f)
        store[d] = {"tod": a["tod"], "C": a["C"], "H": a["H"], "L": a["L"], "atr": f["atr"].to_numpy(float)}
        _, alerts = run_day(f, d, float(spot.iloc[i0 - 1]["Close"]))
        alerts_all += [(d, a["tod"][i], kind, side) for i, kind, side in alerts]
        if n_day % 200 == 0:
            print(f"  {d.date()}  alerts {len(alerts_all)}  {time.time() - t0:.0f}s", flush=True)
    dl = list(store)
    pos = {d: k for k, d in enumerate(dl)}

    def metrics(d, minute, sg):
        s = store[d]
        i = int(np.searchsorted(s["tod"], minute))
        if i >= len(s["tod"]) or s["tod"][i] != minute or i + 30 >= len(s["C"]):
            return None
        C, H, L, atr = s["C"], s["H"], s["L"], s["atr"][i]
        if not np.isfinite(atr) or atr <= 0:
            return None
        return {"f15": sg * (C[i + 15] - C[i]) / atr, "f30": sg * (C[i + 30] - C[i]) / atr,
                "abs30": abs(C[i + 30] - C[i]) / atr, "rng30": (H[i + 1:i + 31].max() - L[i + 1:i + 31].min()) / atr}
    rows = []
    for d, minute, kind, side in alerts_all:
        sg = -1 if side == "PE" else 1
        m = metrics(d, minute, sg)
        if m is None:
            continue
        k = pos[d]
        near = [x for x in dl[max(0, k - 30):k + 31] if x != d]
        ctl = [c for c in (metrics(x, minute, sg) for x in RNG.choice(near, size=min(REPS, len(near)), replace=False)) if c]
        if not ctl:
            continue
        cm = {key: np.mean([c[key] for c in ctl]) for key in m}
        rows.append({"day": d, "kind": kind, **{f"a_{k_}": v for k_, v in m.items()}, **{f"c_{k_}": v for k_, v in cm.items()}})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "alert_usefulness.csv", index=False)
    print(f"\nAlert usefulness, {len(df)} alerts, in ATR units (ATR = avg 1m bar range at the alert). "
          f"Control = same minute of day, 20 days within +-30 trading days.")
    out = []
    for kind, g in df.groupby("kind"):
        mon = pd.to_datetime(g["day"]).dt.to_period("M")
        r = {"kind": kind, "n": len(g)}
        for key in ("f15", "f30", "abs30", "rng30"):
            diff = (g[f"a_{key}"] - g[f"c_{key}"]).groupby(mon).mean()
            r[f"{key} alert"] = g[f"a_{key}"].mean()
            r[f"{key} ctrl"] = g[f"c_{key}"].mean()
            r[f"{key} t"] = tstat(diff.dropna())
        r["rng30 vs ctrl %"] = (g["a_rng30"].mean() / g["c_rng30"].mean() - 1) * 100
        out.append(r)
    print(pd.DataFrame(out).set_index("kind").round(2).T.to_string())
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "usefulness":
        sys.exit(usefulness())
    sys.exit(main())


