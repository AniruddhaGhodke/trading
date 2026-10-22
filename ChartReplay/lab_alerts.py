"""Forward test of the two best Nifty intraday lab rules (2026-10-20) - ALERTS + PAPER JOURNAL. Never places orders.

Both are the closest misses of SwingSystem/lab_nifty_intraday.py (fixed rules, NOT proven: out-of-sample 2023-26
t-stat 1.3 / 1.7). The journal records EVERY signal and how the exact rule would have done, traded or not.

  GAPFADE  09:15 open gaps >= 0.3% from the previous close -> trade AGAINST the gap from the 09:15 open.
           target = previous close, stop = entry -/+ 1x the gap, else exit at the 12:00 bar's close (12:01).
           lab config "GAP fade th0.3 e0 x165 stop1.0": OOS 2023-26 355 trades, +0.026%/trade after 0.05% cost.
  REVFADE  yesterday's close-to-close move >= 1.5% -> trade AGAINST it from the 09:30 open, exit 15:14. No stop.
           lab config "REV fade th1.5 e15 x359": OOS 2023-26 54 trades, +0.174%/trade after 0.05% cost.

Prices are Nifty INDEX points (the rules were tested as futures). The journal also notes the ATM option of the
assist's expiry (never the expiring one) as a suggestion, with its premium where the snapshot has it.

  python lab_alerts.py verify --from 2023-01-01        rules over history == lab engine results (self-check)
  python lab_alerts.py report                          journal summary vs the backtest expectation
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
SWING = ROOT.parent / "SwingSystem"
SPOT_FILE = SWING / "data" / "angel" / "1m" / "IDX_NSEI.csv"
JOURNAL = SWING / "data" / "assist" / "lab_journal.csv"
COST_STRESS = 0.05                  # % round trip, as in the lab
LOT = 65
RULES = {
    "GAPFADE": {"th": 0.3, "exit_bar": 165, "expect": 0.026, "oos_t": 1.29, "oos_n": 355},
    "REVFADE": {"th": 1.5, "entry_bar": 15, "exit_bar": 359, "expect": 0.174, "oos_t": 1.66, "oos_n": 54},
}
COLS = ["date", "rule", "side", "signal_pct", "entry_time", "entry", "stop", "target", "exit_time", "exit", "reason",
        "pts", "net_pct", "opt_symbol", "opt_entry", "opt_exit", "mode"]


def prev_closes(day: pd.Timestamp, n: int = 2) -> list[float]:
    """Closes (last 1m bar) of the n sessions before `day`, most recent first, from the tail of the 1m history file."""
    with open(SPOT_FILE, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 400_000))
        lines = fh.read().decode(errors="ignore").splitlines()[1:]
    rows = [l.split(",") for l in lines if l[:4].isdigit()]
    s = pd.Series([float(r[4]) for r in rows], index=pd.to_datetime([r[0] for r in rows]))
    s = s[s.index < day]
    daily = s.groupby(s.index.normalize()).last()
    return list(daily.iloc[::-1].head(n))


TB = {"L": 20, "vm": 1.25, "every": 30, "expect": 0.012, "oos_t": 0.57, "oos_n": 289}   # NOISE3 L20 vm1.25 f30 both hivol


def trendband_inputs(day: pd.Timestamp):
    """(sigma per minute 09:15..15:14 = avg |close/open - 1| over the last 20 sessions, hi_vol flag) from the 1m
    history before `day` - the same definitions as lab_nifty_intraday.f_noise3."""
    s = pd.read_csv(SPOT_FILE, index_col=0, parse_dates=True)
    s = s[s.index < day]
    m = (s.index.hour * 60 + s.index.minute - 555)
    full = s[(m >= 0) & (m <= 374)]
    dly = full.groupby(full.index.normalize()).agg(H=("High", "max"), L=("Low", "min"), C=("Close", "last"))
    pc = dly["C"].shift(1)
    tr = np.maximum(dly["H"] - dly["L"], np.maximum((dly["H"] - pc).abs(), (dly["L"] - pc).abs()))
    vol = (tr.rolling(14).mean() / dly["C"])          # atr14 known before the NEXT day, over that day's close
    med = vol.rolling(250, min_periods=60).median()
    hivol = bool(vol.iloc[-1] > med.iloc[-2]) if len(vol) > 61 else False
    core = full[(full.index.hour * 60 + full.index.minute - 555) < 360]
    days = pd.DatetimeIndex(core.index.normalize().unique())[-TB["L"]:]
    core = core[core.index.normalize().isin(days)]
    mm = (core.index.hour * 60 + core.index.minute - 555).to_numpy()
    mat = np.full((len(days), 360), np.nan)
    mat[days.get_indexer(core.index.normalize()), mm] = core["Close"].to_numpy()
    mat = pd.DataFrame(mat).ffill(axis=1).to_numpy()
    op = full["Open"].groupby(full.index.normalize()).first().reindex(days).to_numpy()
    sigma = np.nanmean(np.abs(mat / op[:, None] - 1), axis=0)
    return sigma, hivol


class LabRules:
    def __init__(self, day: pd.Timestamp, pC: float | None, ppC: float | None, mode: str = "live",
                 tb_sigma=None, tb_hivol: bool = False):
        self.day, self.pC, self.ppC, self.mode = day, pC, ppC, mode
        self.sig: dict[str, dict] = {}
        self.done: set[str] = set()
        self.tb_sigma, self.tb_hivol = tb_sigma, tb_hivol
        self.tb = {"cur": 0, "want": 0, "leg": None, "csum": 0.0, "n": 0, "up": None, "dn": None, "k": 0}

    def _trendband(self, ts, r, m, snap, out):
        """Noise-area trend rule (Zarattini-Aziz-Barbon style): at each 30-min check (bar closes 09:44, 10:14, ...)
        go long above the upper band AND the day's TWAP, short below the lower band AND TWAP; exit when price
        falls back below max(band, TWAP) (min for shorts). Only on high-volatility days. Positions change at
        the next bar's open; flat at 15:14."""
        tb = self.tb
        if m == 0:
            dO = r["Open"]
            tb["up"] = max(dO, self.pC) * (1 + TB["vm"] * self.tb_sigma)
            tb["dn"] = min(dO, self.pC) * (1 - TB["vm"] * self.tb_sigma)
        if tb["up"] is None:
            return
        if tb["want"] != tb["cur"]:                       # decided at the previous bar's close -> act at this open
            if tb["leg"] is not None:
                self._close_leg(r["Open"], ts, "band/TWAP exit" if tb["want"] == 0 else "reversed", snap, out)
            if tb["want"] != 0:
                tb["k"] += 1
                tb["leg"] = {"rule": "TRENDBAND", "side": tb["want"], "signal_pct": np.nan, "entry_time": ts,
                             "entry": float(r["Open"]), "stop": np.nan, "target": np.nan, "k": tb["k"]}
                d = "LONG (CE)" if tb["want"] > 0 else "SHORT (PE)"
                out.append(("LAB", f"TRENDBAND {d}: Nifty {r['Open']:.0f} broke {'above' if tb['want'] > 0 else 'below'} its noise band "
                                   f"and the day's average price (high-volatility day). Rule: hold while it stays beyond "
                                   f"max(band, TWAP) at the 30-min checks; flat by 15:14. PAPER only - unproven "
                                   f"(OOS t {TB['oos_t']})."))
            tb["cur"] = tb["want"]
        tb["csum"] += r["Close"]
        tb["n"] += 1
        twap = tb["csum"] / tb["n"]
        if m == 359:
            if tb["leg"] is not None:
                self._close_leg(r["Close"], ts + pd.Timedelta("1min"), "time 15:14", snap, out, at_close=True)
            tb["cur"] = tb["want"] = 0
            return
        if (m + 1) % TB["every"] == 0 and m >= 29:
            up, dn, c, cur = tb["up"][m], tb["dn"][m], r["Close"], tb["cur"]
            if cur > 0 and c < max(up, twap):
                cur = 0
            if cur < 0 and c > min(dn, twap):
                cur = 0
            if c > up and c > twap:
                cur = 1
            elif c < dn and c < twap:
                cur = -1
            tb["want"] = cur if np.isfinite(up) else 0

    def _close_leg(self, px, ts, why, snap, out, at_close=False):
        s = self.tb["leg"]
        pts = s["side"] * (px - s["entry"])
        net = pts / s["entry"] * 100 - COST_STRESS
        row = {"date": self.day.date(), "rule": "TRENDBAND", "side": "LONG" if s["side"] > 0 else "SHORT",
               "signal_pct": np.nan, "entry_time": f"{s['entry_time']:%H:%M}", "entry": s["entry"], "stop": np.nan,
               "target": np.nan, "exit_time": f"{ts:%H:%M}", "exit": round(px, 2), "reason": why, "pts": round(pts, 2),
               "net_pct": round(net, 4), **self._option(s, ts - pd.Timedelta("1min") if at_close else ts, snap), "mode": self.mode}
        write_row(row)
        self.tb["leg"] = None
        out.append(("LAB", f"TRENDBAND closed ({why}): {row['side']} {s['entry']:.0f} -> {px:.0f} = {pts:+.0f} pts, "
                           f"{net:+.3f}% after 0.05% cost. Paper journal updated."))

    # ---------------------------------------------------------------- per completed 1m bar
    def step(self, ts: pd.Timestamp, r, snap: dict | None = None) -> list[tuple[str, str]]:
        """ts = bar OPEN time; r has Open/High/Low/Close. Returns [(kind, text)] alerts."""
        out = []
        m = int((ts - self.day) / pd.Timedelta("1min")) - 555
        if self.pC is None:
            return out
        if self.tb_hivol and self.tb_sigma is not None:
            self._trendband(ts, r, m, snap, out)
        if m == 0 and "GAPFADE" not in self.sig:
            gap = (r["Open"] / self.pC - 1) * 100
            if abs(gap) >= RULES["GAPFADE"]["th"]:
                side = -1 if gap > 0 else 1
                g = abs(r["Open"] - self.pC)
                s = {"rule": "GAPFADE", "side": side, "signal_pct": gap, "entry_bar": 0, "entry_time": ts,
                     "entry": float(r["Open"]), "stop": float(r["Open"] - side * g), "target": float(self.pC),
                     "exit_bar": RULES["GAPFADE"]["exit_bar"]}
                self.sig["GAPFADE"] = s
                out.append(("LAB", self._open_text(s, snap)))
        if m == 14 and "REVFADE" not in self.sig and self.ppC:
            mv = (self.pC / self.ppC - 1) * 100
            if abs(mv) >= RULES["REVFADE"]["th"]:
                side = -1 if mv > 0 else 1
                s = {"rule": "REVFADE", "side": side, "signal_pct": mv, "entry_bar": 15, "entry_time": None,
                     "entry": None, "stop": np.nan, "target": np.nan, "exit_bar": RULES["REVFADE"]["exit_bar"]}
                self.sig["REVFADE"] = s
                out.append(("LAB", self._open_text(s, snap)))
        for name, s in self.sig.items():
            if name in self.done or m < s["entry_bar"]:
                continue
            if m == s["entry_bar"] and s["entry"] is None:
                s["entry"], s["entry_time"] = float(r["Open"]), ts
            if m == s["entry_bar"] and name == "GAPFADE" and not self._target_ahead(s):
                self._close(s, ts, s["entry"], "skip: target reached at entry", snap, out)
                continue
            px, why = self._exit_on_bar(s, r, m)
            if px is not None:
                self._close(s, ts, px, why, snap, out)
        return out

    # ---------------------------------------------------------------- mechanics (same as lab_nifty_intraday.sim)
    @staticmethod
    def _target_ahead(s):
        return not np.isfinite(s["target"]) or (s["target"] > s["entry"] if s["side"] > 0 else s["target"] < s["entry"])

    @staticmethod
    def _exit_on_bar(s, r, m):
        sd, st, tg = s["side"], s["stop"], s["target"]
        if np.isfinite(st) and (r["Low"] <= st if sd > 0 else r["High"] >= st):
            return (min(st, r["Open"]) if sd > 0 else max(st, r["Open"])), "stop"
        if np.isfinite(tg) and (r["High"] >= tg if sd > 0 else r["Low"] <= tg):
            return tg, "target"
        if m >= s["exit_bar"]:
            return float(r["Close"]), "time"
        return None, None

    def _close(self, s, ts, px, why, snap, out):
        self.done.add(s["rule"])
        skip = why.startswith("skip")
        pts = 0.0 if skip else s["side"] * (px - s["entry"])
        net = 0.0 if skip else pts / s["entry"] * 100 - COST_STRESS
        opt = self._option(s, ts, snap)
        row = {"date": self.day.date(), "rule": s["rule"], "side": "LONG" if s["side"] > 0 else "SHORT",
               "signal_pct": round(s["signal_pct"], 3), "entry_time": f"{s['entry_time']:%H:%M}", "entry": s["entry"],
               "stop": s["stop"], "target": s["target"], "exit_time": f"{ts + pd.Timedelta('1min'):%H:%M}",
               "exit": round(px, 2), "reason": why, "pts": round(pts, 2), "net_pct": round(net, 4), **opt, "mode": self.mode}
        write_row(row)
        out.append(("LAB", f"{s['rule']} closed ({why}): {row['side']} {s['entry']:.0f} -> {px:.0f} = {pts:+.0f} pts, "
                           f"{net:+.3f}% after 0.05% cost (~Rs {pts * LOT:+,.0f} on 1 futures lot before costs). Paper journal updated."))

    @staticmethod
    def _option(s, ts, snap):
        """Suggested ATM option (assist's expiry) and its premium at entry/exit where the snapshot has the bars."""
        out = {"opt_symbol": "", "opt_entry": np.nan, "opt_exit": np.nan}
        if not snap or not snap.get("expiry") or s["entry"] is None:
            return out
        import options
        sym = options.make(snap["expiry"]["expiry"], round(s["entry"] / options.STEP) * options.STEP,
                           "CE" if s["side"] > 0 else "PE")
        out["opt_symbol"] = sym
        o = (snap.get("options") or {}).get(sym)
        if o and o.get("bars"):
            b = pd.DataFrame(o["bars"], columns=["t", "Open", "High", "Low", "Close", "Volume"])
            b["t"] = pd.to_datetime(b["t"])
            e = b[b["t"] == s["entry_time"]]
            x = b[b["t"] == ts]
            out["opt_entry"] = float(e["Open"].iloc[0]) if len(e) else np.nan
            out["opt_exit"] = float(x["Close"].iloc[0]) if len(x) else np.nan
        return out

    def _open_text(self, s, snap):
        dirn = "LONG (CE)" if s["side"] > 0 else "SHORT (PE)"
        k = RULES[s["rule"]]
        sug = ""
        if snap and snap.get("expiry"):
            import options
            ref = s["entry"] or snap.get("nifty", [[0, 0, 0, 0, 0]])[-1][4]
            sug = f" Suggested option: {options.make(snap['expiry']['expiry'], round(ref / options.STEP) * options.STEP, 'CE' if s['side'] > 0 else 'PE')}."
        if s["rule"] == "GAPFADE":
            risk = abs(s["entry"] - s["stop"]) * LOT
            return (f"GAPFADE {dirn}: gap {s['signal_pct']:+.2f}% (open {s['entry']:.0f} vs prev close {self.pC:.0f}). "
                    f"Rule: {'buy' if s['side'] > 0 else 'sell'} from the open {s['entry']:.0f}, target {s['target']:.0f} (prev close), "
                    f"stop {s['stop']:.0f} (1x gap), exit 12:01. Risk ~Rs {risk:,.0f}/futures lot (above the Rs 1,000 cap -> PAPER)."
                    f"{sug} Unproven (OOS t {k['oos_t']}).")
        return (f"REVFADE {dirn}: yesterday moved {s['signal_pct']:+.2f}%. Rule: {'buy' if s['side'] > 0 else 'sell'} at the "
                f"09:30 open, no stop, exit 15:14. PAPER only.{sug} Unproven (OOS t {k['oos_t']}, {k['oos_n']} trades).")


def write_row(row: dict):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    if JOURNAL.exists():
        old = pd.read_csv(JOURNAL, dtype=str)
        if ((old["date"] == str(row["date"])) & (old["rule"] == row["rule"]) & (old["mode"] == row["mode"])
                & (old["entry_time"] == str(row["entry_time"]))).any():
            return                      # one row per day/rule/entry/mode (a restart replays the day)
    pd.DataFrame([row], columns=COLS).to_csv(JOURNAL, mode="a", header=not JOURNAL.exists(), index=False)


# ---------------------------------------------------------------- self-check against the lab engine
def run_history(lo: str, hi: str | None = None) -> pd.DataFrame:
    sys.path.insert(0, str(SWING))
    import lab_nifty_intraday as L
    d = L.load()
    D = pd.DatetimeIndex(d["D"])
    sig = pd.DataFrame(np.abs(d["C"] / d["O"][:, :1] - 1)).rolling(TB["L"]).mean().shift(1).to_numpy()
    vol = d["atr14"] / d["pC"]
    hv = vol > pd.Series(vol).rolling(250, min_periods=60).median().shift(1).to_numpy()
    rows = []
    for i, day in enumerate(D):
        if day < pd.Timestamp(lo) or (hi and day > pd.Timestamp(hi)):
            continue
        lab = LabRules(day, float(d["pC"][i]), float(d["ppC"][i]), mode="verify", tb_sigma=sig[i], tb_hivol=bool(hv[i]))
        for mi in range(L.NB):
            ts = day + pd.Timedelta(minutes=555 + mi)
            r = {"Open": d["O"][i, mi], "High": d["H"][i, mi], "Low": d["L"][i, mi], "Close": d["C"][i, mi]}
            for _ in lab.step(ts, r, None):
                pass
        for s in lab.sig.values():
            rows.append({"date": day, "rule": s["rule"], **{k: s.get(k) for k in ("side", "entry")}})
    return pd.DataFrame(rows)


def cmd_verify(a):
    """Re-run both rules bar by bar over history (journal writes go to a scratch file) and compare with the lab."""
    global JOURNAL
    sys.path.insert(0, str(SWING))
    import lab_nifty_intraday as L
    JOURNAL = Path(a.scratch)
    if JOURNAL.exists():
        JOURNAL.unlink()
    run_history(a.lo, a.hi)
    j = pd.read_csv(JOURNAL)
    j = j[~j["reason"].str.startswith("skip")]
    names, D, G, T = L.load_results()
    Dd = pd.DatetimeIndex(D)
    for rule, cfg in (("GAPFADE", "GAP fade th0.3 e0 x165 stop1.0"), ("REVFADE", "REV fade th1.5 e15 x359")):
        m = (Dd >= pd.Timestamp(a.lo)) & (Dd <= pd.Timestamp(a.hi or "2100-01-01"))
        lab_g = G[cfg][m]
        lab_n, lab_sum = int(np.isfinite(lab_g).sum()), np.nansum(lab_g)
        jj = j[j["rule"] == rule]
        mine = (jj["pts"] / jj["entry"] * 100).sum()
        print(f"{rule}: alerts module {len(jj)} trades, gross {mine:+.3f}%  |  lab engine {lab_n} trades, gross {lab_sum:+.3f}%  "
              f"-> {'MATCH' if len(jj) == lab_n and abs(mine - lab_sum) < 1e-6 * max(1, abs(lab_sum)) + 1e-6 else 'DIFFERENT'}")
    d = L.load()
    m = (Dd >= pd.Timestamp(a.lo)) & (Dd <= pd.Timestamp(a.hi or "2100-01-01"))
    cfg = "NOISE3 L20 vm1.25 f30 both hivol"
    lab_pts = np.nansum(G[cfg][m] * d["O"][m, 0] / 100)
    jj = j[j["rule"] == "TRENDBAND"]
    print(f"TRENDBAND: alerts module {len(jj)} legs, {jj['pts'].sum():+.1f} pts  |  lab engine {int(T[cfg][m].sum())} entries, "
          f"{lab_pts:+.1f} pts -> {'MATCH' if len(jj) == int(T[cfg][m].sum()) and abs(jj['pts'].sum() - lab_pts) < 0.05 * len(jj) + 0.5 else 'DIFFERENT'}")


def cmd_report(a):
    if not JOURNAL.exists():
        print("no journal yet")
        return
    j = pd.read_csv(JOURNAL)
    j = j[j["mode"] == a.mode]
    j = j[~j["reason"].str.startswith("skip")]
    print(f"Paper journal ({a.mode}): {len(j)} closed signals")
    for rule, g in j.groupby("rule"):
        k = RULES[rule]
        print(f"  {rule}: {len(g)} trades, avg net {g['net_pct'].mean():+.3f}%/trade (backtest OOS {k['expect']:+.3f}%), "
              f"win {(g['net_pct'] > 0).mean() * 100:.0f}%, total {g['net_pct'].sum():+.2f}%, "
              f"pts {g['pts'].sum():+.0f} (~Rs {g['pts'].sum() * LOT:+,.0f}/futures lot before costs)")
    if len(j):
        print(j.tail(10).to_string(index=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--from", dest="lo", default="2023-01-01")
    v.add_argument("--to", dest="hi")
    v.add_argument("--scratch", default=str(SWING / "data" / "assist" / "lab_journal_verify.csv"))
    r = sub.add_parser("report")
    r.add_argument("--mode", default="live")
    a = ap.parse_args()
    {"verify": cmd_verify, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
