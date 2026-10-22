"""PULLBACK alert: the user's trend + 1m 9-EMA pullback setup, bar by bar (live, sim, replay, ChartReplay UI).

Rule = pullback_lab.py RESUME1m / first / X2, exactly (check: `python pullback.py verify`):
  in a confirmed assist trend, a bar touches its 1m 9-EMA; within 10 bars a bar closes beyond the extreme of the
  bars since the touch, on the trend side of the 9-EMA -> enter at the next open (09:30-12:00), first pullback of
  each trend episode, max 2/day, one at a time. Stop = pullback extreme -0.5 (>= 1 ATR) on a Nifty CLOSE;
  at +18 Nifty pts (~+10 premium) stop -> entry; then exit on a close beyond the 9-EMA. 15:10 time exit.

NOT an edge (pullback_lab: gross ~+1 premium pt/trade, direction ~ coin flip vs random). It marks the setup so the
user decides; the paper outcome of every signal and the user's Take/Skip goes to pullback_journal.csv, so we can
measure whether the user's selection beats the mechanical rule.

Signals and exits are decided on Nifty (as tested); the trade itself is the ATM contract fixed at the signal (strike =
signal close rounded to 50, expiry = assist_data.pick_expiry, as in the checklist plan). Paper P&L is that contract's
real premium: fill = the contract's 1m bar open at the entry/exit minute, else its live ltp (journal column prem_src).

  python pullback.py verify       rule == pullback_lab on cached 2018-2026 days
  python pullback.py day 2026-10-16   alerts for one day (what the ChartReplay panel shows)
  python pullback.py report           your Take/Skip decisions vs the paper outcome of the same signals
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from assist import CFG, hm

DECISIONS = Path(__file__).resolve().parent.parent / "SwingSystem" / "data" / "assist" / "pullback_decisions.csv"
JOURNAL = Path(__file__).resolve().parent.parent / "SwingSystem" / "data" / "assist" / "pullback_journal.csv"
ARM = CFG["trail_arm"] / CFG["delta"]        # +10 premium in Nifty pts (~18.2)
THROUGH, RESUME_BARS, EXIT_AT = 0.5, 10, hm("15:10")
LOT = 65
SETUP = "BASEBREAK"     # the practice setup shown live / in replay / in the panel (21-Oct: BASEBREAK replaces PULLBACK)


def make_rule(day, mode: str = "live", setup: str | None = None):
    """The practice-setup rule for one session: BASEBREAK (basebreak.py) or PULLBACK (this file)."""
    if (setup or SETUP) == "BASEBREAK":
        import basebreak
        return basebreak.BaseBreakRule(day, mode)
    return PullbackRule(day, mode)


def decisions_path(kind: str) -> Path:
    return DECISIONS if kind == "PULLBACK" else DECISIONS.with_name(f"{kind.lower()}_decisions.csv")


class PullbackRule:
    KIND, JOURNAL = "PULLBACK", JOURNAL

    def __init__(self, day: pd.Timestamp, mode: str = "live"):
        self.day, self.mode = pd.Timestamp(day).normalize(), mode
        self.snap = None         # live/sim: latest /api/assist snapshot (option bars + ltp), set by assist.run_live
        self.prev_reg, self.ep, self.used = 0, -1, set()
        self.entries, self.pend, self.since = 0, None, []     # since = (H, L) of bars from the touch
        self.sig = None          # signal waiting for the next open
        self.pos = None          # open paper position
        self.closed = []         # finished paper trades (dicts)

    # -------------------------------------------------------------- one closed 1m bar
    def step(self, ts: pd.Timestamp, r, regime: str) -> list[tuple[str, str]]:
        """ts = bar open time; r has Open High Low Close ema9 ema21 atr; regime = Detector.regime after this bar."""
        out = []
        reg = {"up": 1, "down": -1}.get(regime, 0)
        if reg != 0 and reg != self.prev_reg:
            self.ep += 1
        self.prev_reg = reg
        O, H, L, C = (float(r[k]) for k in ("Open", "High", "Low", "Close"))
        e9, e21, atr = float(r["ema9"]), float(r["ema21"]), float(r["atr"])

        if self.sig is not None:                     # fill at this bar's open
            sg, stop, ep, sym = self.sig
            self.sig = None
            if sg * (O - stop) > 0:
                self.used.add(ep)
                self.entries += 1
                self.pos = {"sg": sg, "px": O, "stop": stop, "t0": ts, "hwm": O, "be": False, "first": True, "nxt": None,
                            "sym": sym, "prem": self._prem(sym, ts)}
        if self.pos is not None:
            if self.pos["prem"][1] == "ltp":         # the entry minute's bar arrived late: use its open
                got = self._prem(self.pos["sym"], self.pos["t0"], bar_only=True)
                if got[0] is not None:
                    self.pos["prem"] = got
            done = self._manage(ts, O, H, L, C, e9)
            if done:
                out.append(done)
            self.pend, self.since = None, []
            return out
        if self.entries >= CFG["max_trades"]:
            return out

        if self.pend is not None:
            t0_sg = self.pend
            if reg != t0_sg or len(self.since) > RESUME_BARS or t0_sg * (C - e21) < 0:
                self.pend, self.since = None, []
            elif self.since:
                ext = max(h for h, _ in self.since) if t0_sg > 0 else min(lo for _, lo in self.since)
                fill_t = ts + pd.Timedelta("1min") - self.day
                if t0_sg * (C - ext) > 0 and t0_sg * (C - e9) > 0 and hm(CFG["setup_from"]) <= fill_t <= hm(CFG["setup_until"]):
                    lows = [lo for _, lo in self.since] + [L]
                    highs = [h for h, _ in self.since] + [H]
                    stop = min(min(lows) - THROUGH, C - atr) if t0_sg > 0 else max(max(highs) + THROUGH, C + atr)
                    self.pend, self.since = None, []
                    if self.ep not in self.used:
                        side = "CE" if t0_sg > 0 else "PE"
                        sym, lot = self._contract(ts, C, side)
                        self.sig = (t0_sg, stop, self.ep, sym)
                        risk = abs(C - stop)
                        now_p = self._prem(sym, ts, close=True)[0]
                        name = f"{sym[6:]}" if sym else f"ATM {round(C / 50) * 50:.0f}"
                        prem_txt = f" ~{now_p:.1f}, premium stop ~{now_p - risk * CFG['delta']:.1f}" if now_p else ""
                        out.append(("PULLBACK", f"{side} {name}{prem_txt}: trend {'UP' if t0_sg > 0 else 'DOWN'}, pullback to the 9-EMA "
                                                f"held and resumed ({'above' if t0_sg > 0 else 'below'} {ext:.0f}). Entry next open "
                                                f"(Nifty ~{C:.0f}), Nifty stop {'below' if t0_sg > 0 else 'above'} {stop:.0f} ({risk:.0f} pts, ~Rs "
                                                f"{risk * CFG['delta'] * lot:.0f}/lot). At +10 premium stop to entry, then exit on a close "
                                                f"{'below' if t0_sg > 0 else 'above'} the 9-EMA. Your call: take or skip."))
                        return out
                    return out
            if self.pend is not None:
                self.since.append((H, L))
                return out
        if self.pend is None and reg != 0 and np.isfinite(e9):
            touched = (L <= e9) if reg > 0 else (H >= e9)
            if touched and reg * (C - e21) >= 0:
                self.pend, self.since = reg, [(H, L)]
        return out

    def _manage(self, ts, O, H, L, C, e9):
        p, sg = self.pos, self.pos["sg"]
        if p["nxt"] is not None:
            return self._close(ts, O, p["nxt"])
        if ts - self.day >= EXIT_AT:
            return self._close(ts, O, "time")
        if not p["first"]:
            adverse, fav = (L, H) if sg > 0 else (H, L)
            if p["be"] and sg * (adverse - p["px"]) <= 0:
                return self._close(ts, O if sg * (O - p["px"]) <= 0 else p["px"], "breakeven")
            if sg * (fav - p["hwm"]) > 0:
                p["hwm"] = fav
            if sg * (p["hwm"] - p["px"]) >= ARM:
                p["be"] = True
        p["first"] = False
        if sg * (C - p["stop"]) < 0:
            p["nxt"] = "stop"
        elif p["be"] and sg * (C - e9) < 0:
            p["nxt"] = "ema9"
        return None

    # -------------------------------------------------------------- the option contract
    def _contract(self, ts, C, side):
        if self.mode == "verify":
            return None, LOT
        try:
            import assist_data
            import options
            e = assist_data.pick_expiry(ts + pd.Timedelta("1min"))["expiry"]
            return options.make(e, round(C / 50) * 50, side), options.lot_size(e)
        except Exception:
            return None, LOT

    def _prem(self, sym, ts, close=False, bar_only=False):
        """(price, source) of the contract at bar ts: its open (close=True: its close), else the live ltp."""
        if not sym or self.mode == "verify":
            return None, None
        live_mode = self.mode in ("live", "sim")
        o = ((self.snap or {}).get("options") or {}).get(sym) or {}
        for row in o.get("bars") or []:
            if row[0] == str(ts):
                return float(row[4 if close else 1]), "bar"
        try:
            import options
            df = options.real_raw(sym) if live_mode else options.load(sym, "1m")
            if df is not None and ts in df.index:
                return float(df.loc[ts, "Close" if close else "Open"]), ("bar" if live_mode else options.source(sym).lower())
        except Exception:
            pass
        if not bar_only and o.get("ltp"):
            return float(o["ltp"]), "ltp"
        return None, None

    def _close(self, ts, px, why):
        p = self.pos
        self.pos = None
        pts = p["sg"] * (px - p["px"])
        (p0, s0), (p1, s1) = p["prem"], self._prem(p["sym"], ts)
        pp = round(p1 - p0, 2) if p0 is not None and p1 is not None else None
        lot = LOT
        if p["sym"]:
            import options
            lot = options.lot_size(options.parse(p["sym"])[0])
        tr = {"date": f"{self.day:%Y-%m-%d}", "side": "CE" if p["sg"] > 0 else "PE", "entry_time": f"{p['t0']:%H:%M}",
              "entry": round(p["px"], 2), "stop": round(p["stop"], 2), "exit_time": f"{ts:%H:%M}", "exit": round(px, 2),
              "reason": why, "nifty_pts": round(pts, 2), "mode": self.mode, "contract": p["sym"],
              "prem_entry": p0, "prem_exit": p1, "prem_pts": pp, "lot": lot,
              "rs_gross": round(pp * lot) if pp is not None else None,
              "rs_net": round(pp * lot - CFG["cost_per_trip"]) if pp is not None else None,
              "prem_src": f"{s0}/{s1}"}
        self.closed.append(tr)
        if self.mode in ("live", "sim"):
            log_trade(tr, self.JOURNAL)
        if pp is None:
            return (self.KIND, f"paper exit {tr['side']} {p['sym'] or ''} ({why}): no premium for the contract - "
                                f"Nifty {p['px']:.0f} -> {px:.0f}, {pts:+.1f} pts (~{pts * CFG['delta']:+.1f} premium est.).")
        return (self.KIND, f"paper exit {tr['side']} {p['sym'][6:]} ({why}): premium {p0:.1f} -> {p1:.1f}, {pp:+.1f} pts = "
                            f"Rs {tr['rs_gross']:+.0f}/lot, ~Rs {tr['rs_net']:+.0f} after charges (Nifty {p['px']:.0f} -> {px:.0f}).")


def log_trade(tr: dict, path: Path = JOURNAL):
    """Append once (a restarted live loop rebuilds the day and would log the same trade again)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        j = pd.read_csv(path, dtype=str)
        if ((j["date"] == tr["date"]) & (j["entry_time"] == tr["entry_time"]) & (j["mode"] == tr["mode"])).any():
            return
        if list(j.columns) != list(tr):         # new columns (option premium, 21-Oct): rewrite with the union
            pd.concat([j, pd.DataFrame([tr])], ignore_index=True).to_csv(path, index=False)
            return
    pd.DataFrame([tr]).to_csv(path, mode="a", header=not path.exists(), index=False)


# ------------------------------------------------------------------ whole day (ChartReplay panel)
def day_alerts(day, until=None, rule_out: list | None = None, setup: str | None = None) -> list[dict]:
    """Detector + practice-setup (SETUP) alerts of one day from Nifty 1m history, each stamped with its bar-close time.
    until = only bars that closed by then (live)."""
    import alert_backtest as ab
    import data_source
    import options
    from assist import Detector, features
    day = pd.Timestamp(day).normalize()
    spot = data_source.load(options.SPOT, "1m")
    i0 = int(spot.index.searchsorted(day))
    i1 = int(spot.index.searchsorted(day + pd.Timedelta(days=1)))
    if i0 >= i1 or i0 == 0:
        return []
    nb = spot.iloc[max(0, i0 - 60):i1]
    nb = nb[nb.index < day + hm("15:30")]
    if until is not None:
        nb = nb[nb.index + pd.Timedelta("1min") <= until]
    f = features(nb.copy(), day)
    det, pb, out = Detector(day, float(spot.iloc[i0 - 1]["Close"])), make_rule(day, "replay", setup), []
    for ts, r in f.iterrows():
        done = ts + pd.Timedelta("1min")
        for kind, text in det.step(done, r, {}) + pb.step(ts, r, det.regime):
            a = {"time": int(done.value // 10**9), "hhmm": f"{done:%H:%M}", "kind": kind, "text": text}
            if kind == pb.KIND and not text.startswith("paper exit"):
                a["side"] = text[:2]
            out.append(a)
    if rule_out is not None:
        rule_out.extend(pb.closed)
    return out


def log_decision(alert: pd.Timestamp, side: str, choice: str, clock: pd.Timestamp, mode: str, kind: str = "PULLBACK") -> dict:
    row = {"date": f"{alert:%Y-%m-%d}", "alert": f"{alert:%H:%M}", "side": side, "choice": choice,
           "clock": f"{clock:%H:%M}", "mode": mode, "logged_at": f"{pd.Timestamp.now():%Y-%m-%d %H:%M:%S}"}
    path = decisions_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(path, mode="a", header=not path.exists(), index=False)
    return {"ok": True, "kind": kind, **row}


def report(kind: str = "PULLBACK"):
    """Your Take/Skip decisions vs the rule's paper outcome of the same signals."""
    path = decisions_path(kind)
    if not path.exists():
        print(f"no {kind} decisions yet - use Take / Skip in the ChartReplay Alerts panel")
        return
    d = pd.read_csv(path, dtype=str).drop_duplicates(["date", "alert", "mode"], keep="last")
    rows = []
    for day in sorted(d["date"].unique()):
        closed = []
        day_alerts(day, rule_out=closed, setup=kind)
        for t in closed:
            rows.append({"date": day, "alert": t["entry_time"], "nifty_pts": t["nifty_pts"], "reason": t["reason"],
                         "contract": t.get("contract"), "prem_pts": t.get("prem_pts"), "src": t.get("prem_src")})
    out = d.merge(pd.DataFrame(rows), on=["date", "alert"], how="left")
    # the contract's own premium move (real bars, or Black-Scholes on days without them); else the delta approximation
    out["prem_net"] = pd.to_numeric(out["prem_pts"]).fillna(out["nifty_pts"] * CFG["delta"]) - 5.5
    print(out[["date", "alert", "side", "choice", "mode", "contract", "nifty_pts", "prem_pts", "src", "reason", "prem_net"]].to_string(index=False))
    g = out.groupby("choice")["prem_net"].agg(["count", "mean", "sum"]).round(2)
    print("\npremium pts per trade after ~5.5 costs (spread + charges):")
    print(g.to_string())
    print(f"all signals you saw: {out['prem_net'].mean():+.2f}/trade over {out['prem_net'].count()}")
    print("Your selection adds value only if 'take' beats 'all signals' over many (50+) decisions.")


def verify():
    import pullback_lab as pl
    days = pl.precompute()
    ref = pl.trades(days, "RESUME1m", "first", "X2")
    got = []
    for D in days:
        a, d = D["a"], D["day"]
        rule = PullbackRule(d, "verify")
        names = {1: "up", -1: "down"}
        for i in range(len(a["O"])):
            r = {"Open": a["O"][i], "High": a["H"][i], "Low": a["L"][i], "Close": a["C"][i],
                 "ema9": a["e9"][i], "ema21": a["e21"][i], "atr": a["atr"][i]}
            rule.step(a["idx"][i], r, names.get(int(a["reg"][i]), "neutral"))
        got += [(d, pd.Timestamp(f"{t['date']} {t['entry_time']}"), round(t["entry"], 2), round(t["exit"], 2)) for t in rule.closed]
    g = pd.DataFrame(got, columns=["day", "t0", "s0", "s1"])
    r = ref.assign(s0=ref["s0"].round(2), s1=ref["s1"].round(2))[["day", "t0", "s0", "s1"]]
    m = g.merge(r, how="outer", on=["day", "t0"], suffixes=("_rule", "_lab"), indicator=True)
    same = (m["_merge"] == "both") & np.isclose(m["s0_rule"], m["s0_lab"]) & np.isclose(m["s1_rule"], m["s1_lab"])
    print(f"lab {len(r)} trades, rule {len(g)}, identical {same.sum()}")
    bad = m[~same]
    if len(bad):
        print(bad.head(20).to_string())
    return 0 if not len(bad) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["verify"]:
        sys.exit(verify())
    if sys.argv[1:2] == ["report"]:
        sys.exit(report())
    if sys.argv[1:2] == ["day"]:
        for x in day_alerts(sys.argv[2], setup="PULLBACK"):
            print(x["hhmm"], x["kind"], x["text"])
