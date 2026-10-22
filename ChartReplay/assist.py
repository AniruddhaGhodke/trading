"""Live trading checklist + setup alerts for Nifty options.  DATA ONLY - never places orders.

Reads everything from the ChartReplay server (/api/assist), so there is one Angel One session (live.py's).
Start the server first:   python server.py          (http://127.0.0.1:8050)

  python assist.py live                       every 10 s: checklist line per minute, alerts (beep + alerts.log)
  python assist.py check CE|PE                full checklist + plan for one side, from the latest state.json
  python assist.py replay 2026-10-16 [--trades f.csv] [--log] [--speed 0.2]
                                              run a past session through the same detector (no lookahead)
  python assist.py calibrate --days 60        setup alerts per day over recent sessions (Nifty only)

Files (SwingSystem/data/assist/):
  state.json   overwritten every poll (atomic): features, regime, checklists for CE and PE, trades, alerts
  alerts.log   append-only, one line per alert: "<date time> ALERT <KIND> <text>"  (also DATA/DOWN lines)
               Claude Code watches it with:  tail -f alerts.log | grep --line-buffered -E "ALERT|STALE|DOWN"

Rules (from the 16-Oct review): max 2 trades/day, Rs 1000 daily loss cap (net of ~Rs 62 charges per round trip),
setup alerts 09:30-12:00 only, few alerts (confirmed state changes with cooldowns, not every candle).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / "SwingSystem" / "data" / "assist"
STATE, ALERTS = OUT / "state.json", OUT / "alerts.log"
SERVER = "http://127.0.0.1:8050"

CFG = {
    "max_trades": 2, "loss_cap": 1000.0, "loss_warn": 700.0, "cost_per_trip": 62.0,
    "setup_from": "09:30", "setup_until": "12:00",
    # regime: trend = efficient 15-min move of >= 3 ATR, backed by the 30-min move and EMA alignment;
    # range = 30-min high-low band <= 7 ATR with little net progress. Confirmed after 2 bars; a trend with no
    # trend reading for 15 bars decays to neutral silently.
    "er15_trend": 0.4, "net15_trend_atr": 3.0, "er30_trend": 0.3, "band30_range_atr": 7.0, "er30_range": 0.25,
    "confirm_bars": 2, "decay_bars": 15, "regime_cooldown": 20,
    # tight-range breakout: box = previous 8 bars
    "box_bars": 8, "box_max_atr": 3.0, "break_atr": 0.25, "breakout_cooldown": 20, "breakout_near_high_atr": 1.0,
    "gap_min_pct": 0.25,
    # checklist
    "stretch_atr": 1.5, "spike_atr": 2.5, "prem_chase_pct": 20.0, "stop_bars": 5, "stop_min_atr": 1.0, "delta": 0.55,
    "trail_arm": 10.0, "trail_gap": 15.0, "same_idea_min": 30, "same_idea_pts": 5.0,
    "stale_s": 180,
}


def hm(s: str) -> pd.Timedelta:
    h, m = s.split(":")
    return pd.Timedelta(hours=int(h), minutes=int(m))


# ------------------------------------------------------------------ features (causal: row t uses bars <= t only)
def features(nb: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """nb = Nifty 1m OHLC (previous-session seed + today). Returns today's rows with indicator columns."""
    f = nb.copy()
    c = f["Close"]
    f["ema9"] = c.ewm(span=9, adjust=False).mean()
    f["ema21"] = c.ewm(span=21, adjust=False).mean()
    f["atr"] = (f["High"] - f["Low"]).rolling(20, min_periods=5).mean()      # no gap term: overnight gap is not 1m noise
    f["net30"] = c - c.shift(30)
    f["er30"] = f["net30"].abs() / c.diff().abs().rolling(30).sum()
    f["net15"] = c - c.shift(15)
    f["er15"] = f["net15"].abs() / c.diff().abs().rolling(15).sum()
    f["band30"] = f["High"].rolling(30).max() - f["Low"].rolling(30).min()
    n = CFG["box_bars"]
    f["box_hi"] = f["High"].shift(1).rolling(n).max()
    f["box_lo"] = f["Low"].shift(1).rolling(n).min()
    # structure stop: the last stop_bars swing, but at least stop_min_atr away (a closer stop is noise)
    f["low_n"] = np.minimum(f["Low"].rolling(CFG["stop_bars"]).min(), c - CFG["stop_min_atr"] * f["atr"])
    f["high_n"] = np.maximum(f["High"].rolling(CFG["stop_bars"]).max(), c + CFG["stop_min_atr"] * f["atr"])
    f = f[f.index >= day].copy()
    if not len(f):
        return f
    f["day_open"] = f["Open"].iloc[0]
    f["day_hi_prev"] = f["High"].cummax().shift(1)
    f["day_lo_prev"] = f["Low"].cummin().shift(1)
    orng = f[f.index < day + hm("09:30")]
    done = f.index >= day + hm("09:29")                 # the 09:29 bar completes the opening range
    f["or_high"] = np.where(done, orng["High"].max() if len(orng) else np.nan, np.nan)
    f["or_low"] = np.where(done, orng["Low"].min() if len(orng) else np.nan, np.nan)
    f["bars_today"] = np.arange(1, len(f) + 1)
    return f


# ------------------------------------------------------------------ setup detector
@dataclass
class Detector:
    day: pd.Timestamp
    prev_close: float | None
    regime: str = "unknown"            # unknown / up / down / range (confirmed)
    raw: str = "unknown"               # this bar's unconfirmed reading
    _pend: str | None = None
    _pend_n: int = 0
    _off_n: int = 0
    _last: dict = field(default_factory=dict)       # alert kind -> last time
    _once: set = field(default_factory=set)

    def raw_regime(self, r) -> str:
        if r["bars_today"] < 30 or not np.isfinite(r["er30"]) or not np.isfinite(r["atr"]):
            return "unknown"
        a = r["atr"]
        trend = r["er15"] >= CFG["er15_trend"] and abs(r["net15"]) >= CFG["net15_trend_atr"] * a and r["er30"] >= CFG["er30_trend"]
        if trend and r["net15"] > 0 and r["net30"] > 0 and r["Close"] > r["ema21"] and r["ema9"] > r["ema21"]:
            return "up"
        if trend and r["net15"] < 0 and r["net30"] < 0 and r["Close"] < r["ema21"] and r["ema9"] < r["ema21"]:
            return "down"
        if r["band30"] <= CFG["band30_range_atr"] * a and r["er30"] <= CFG["er30_range"]:
            return "range"
        return "neutral"

    def _cool(self, kind: str, t, minutes: int) -> bool:
        """True (and start the cooldown) if no `kind` alert in the last `minutes`. Call only when about to alert."""
        last = self._last.get(kind)
        if last is not None and t - last < pd.Timedelta(minutes=minutes):
            return False
        self._last[kind] = t
        return True

    def step(self, t: pd.Timestamp, r, ctx: dict) -> list[tuple[str, str]]:
        """t = time the bar completed (bar open + 1 min). Returns [(kind, text)]."""
        out = []
        tod = t - self.day
        raw = self.raw = self.raw_regime(r)
        prev_regime = self.regime
        if raw in ("up", "down", "range") and raw != self.regime:
            self._pend_n = self._pend_n + 1 if raw == self._pend else 1
            self._pend = raw
            if self._pend_n >= CFG["confirm_bars"]:
                self.regime, self._pend, self._pend_n = raw, None, 0
        elif raw == self.regime:
            self._pend, self._pend_n = None, 0
        self._off_n = 0 if raw == self.regime else self._off_n + 1
        if self.regime in ("up", "down") and self._off_n >= CFG["decay_bars"]:
            self.regime, self._off_n = "neutral", 0
        in_window = hm(CFG["setup_from"]) <= tod <= hm(CFG["setup_until"])
        c, atr = r["Close"], r["atr"]

        if tod >= hm("09:30") and "open" not in self._once:
            self._once.add("open")
            gap = (r["day_open"] / self.prev_close - 1) * 100 if self.prev_close else float("nan")
            e = ctx.get("expiry") or {}
            out.append(("OPEN", f"Open {r['day_open']:.0f} ({gap:+.2f}% vs prev close {self.prev_close or float('nan'):.0f}). "
                                f"Opening range {r['or_low']:.0f}-{r['or_high']:.0f}, now {c:.0f}. VIX {ctx.get('vix') or float('nan'):.1f}. "
                                f"Expiry {e.get('expiry', '?')} ({e.get('days', '?')} d){' - expiry day: using next expiry' if e.get('same_day_skipped') else ''}."))
        if not in_window:
            if tod > hm(CFG["setup_until"]) and "cutoff" not in self._once:
                self._once.add("cutoff")
                out.append(("CUTOFF", f"{CFG['setup_until']} - setup alerts off for today (trade and discipline alerts stay on)."))
            return out

        # gap failing (once a day): gap >= gap_min and price closes through the opening range against the gap
        if self.prev_close and "gap" not in self._once and np.isfinite(r["or_low"]):
            gap = (r["day_open"] / self.prev_close - 1) * 100
            if gap >= CFG["gap_min_pct"] and c < r["or_low"]:
                self._once.add("gap")
                out.append(("GAP", f"Gap-up failing: opened {gap:+.2f}%, now {c:.0f} closed below the opening-range low {r['or_low']:.0f}. "
                                   f"Prev close {self.prev_close:.0f} is the next level. PE side only if it holds below; a quick reclaim of {r['or_low']:.0f} = trap."))
            elif gap <= -CFG["gap_min_pct"] and c > r["or_high"]:
                self._once.add("gap")
                out.append(("GAP", f"Gap-down failing: opened {gap:+.2f}%, now {c:.0f} closed above the opening-range high {r['or_high']:.0f}. "
                                   f"Prev close {self.prev_close:.0f} is the next level. CE side only if it holds above; a quick loss of {r['or_high']:.0f} = trap."))

        alert_regime = self.regime in ("up", "down") or (self.regime == "range" and prev_regime in ("up", "down", "neutral"))
        if self.regime != prev_regime and alert_regime and self._cool("regime", t, CFG["regime_cooldown"]):
            if self.regime == "up":
                out.append(("TREND", f"Trend UP: Nifty {r['net30']:+.0f} in 30 min, above 9/21-EMA. CE side. Enter on a pullback to the "
                                      f"9-EMA ({r['ema9']:.0f}) or a tight-range breakout; idea invalid below {r['low_n']:.0f}."))
            elif self.regime == "down":
                out.append(("TREND", f"Trend DOWN: Nifty {r['net30']:+.0f} in 30 min, below 9/21-EMA. PE side. Enter on a pullback to the "
                                      f"9-EMA ({r['ema9']:.0f}) or a tight-range breakdown; idea invalid above {r['high_n']:.0f}."))
            else:
                out.append(("RANGE", f"RANGE: last 30 min stuck in a {r['band30']:.0f}-pt band, net {r['net30']:+.0f}. Bought options "
                                      f"bleed here (16-Oct trade 4). Stand aside until a trend alert."))

        # tight-range breakout in the trend direction, at/near the day's extreme
        if np.isfinite(r["box_hi"]) and np.isfinite(atr) and r["box_hi"] - r["box_lo"] <= CFG["box_max_atr"] * atr:
            up = (c > r["box_hi"] + CFG["break_atr"] * atr and r["ema9"] > r["ema21"] and self.regime == "up"
                  and r["box_hi"] >= r["day_hi_prev"] - CFG["breakout_near_high_atr"] * atr)
            dn = (c < r["box_lo"] - CFG["break_atr"] * atr and r["ema9"] < r["ema21"] and self.regime == "down"
                  and r["box_lo"] <= r["day_lo_prev"] + CFG["breakout_near_high_atr"] * atr)
            if up and self._cool("break_up", t, CFG["breakout_cooldown"]):
                out.append(("BREAKOUT", f"Breakout UP: Nifty {c:.0f} closed above the {CFG['box_bars']}-min box {r['box_lo']:.0f}-{r['box_hi']:.0f} "
                                         f"near the day high. CE idea, stop below {r['box_lo']:.0f}. Don't chase if the premium already spiked."))
            if dn and self._cool("break_dn", t, CFG["breakout_cooldown"]):
                out.append(("BREAKOUT", f"Breakdown: Nifty {c:.0f} closed below the {CFG['box_bars']}-min box {r['box_lo']:.0f}-{r['box_hi']:.0f} "
                                         f"near the day low. PE idea, stop above {r['box_hi']:.0f}. Don't chase if the premium already spiked."))
        return out


# ------------------------------------------------------------------ trades (from fills; same code live and replay)
def fills_from_tradebook(trades: list, day: pd.Timestamp) -> list[dict]:
    import assist_data
    out = []
    for x in trades or []:
        s = assist_data.angel_to_symbol(x.get("tradingsymbol"))
        if not s or not x.get("filltime"):
            continue
        out.append({"time": day + pd.Timedelta(x["filltime"]), "symbol": s, "side": x["transactiontype"],
                    "qty": int(float(x.get("fillsize") or 0)), "price": float(x["fillprice"]), "order": x.get("orderid")})
    return sorted(out, key=lambda z: z["time"])


def fills_from_csv(path: str, day: pd.Timestamp) -> list[dict]:
    """CSV columns: time (HH:MM:SS), symbol (NIFTY_YYYYMMDD_<strike><CE|PE> or Angel tradingsymbol), side, qty, price."""
    import assist_data
    df = pd.read_csv(path, dtype=str)
    out = []
    for i, x in df.iterrows():
        s = x["symbol"] if x["symbol"].startswith("NIFTY_") else assist_data.angel_to_symbol(x["symbol"])
        out.append({"time": day + pd.Timedelta(x["time"]), "symbol": s, "side": x["side"].upper(),
                    "qty": int(x["qty"]), "price": float(x["price"]), "order": f"csv{i}"})
    return sorted(out, key=lambda z: z["time"])


def book(fills: list[dict], t: pd.Timestamp, ltp_of) -> dict:
    """Positions, realised + open P&L, entries used and the last exits, from the fills up to t."""
    pos, realised, closed, entries, unmatched = {}, 0.0, [], set(), []
    for f in fills:
        if f["time"] > t:
            break
        p = pos.setdefault(f["symbol"], {"qty": 0, "avg": 0.0, "entry_time": None})
        if f["side"] == "BUY":
            entries.add(f["order"])
            if p["qty"] == 0:
                p["entry_time"] = f["time"]
            p["avg"] = (p["avg"] * p["qty"] + f["price"] * f["qty"]) / (p["qty"] + f["qty"])
            p["qty"] += f["qty"]
        else:
            if p["qty"] <= 0:           # a sell with no long bought today (carried position / short): can't price it
                unmatched.append(f"{f['time']:%H:%M} {f['symbol']} SELL {f['qty']} @ {f['price']}")
                continue
            q = min(f["qty"], p["qty"])
            if f["qty"] > q:
                unmatched.append(f"{f['time']:%H:%M} {f['symbol']} SELL {f['qty'] - q} more than held")
            pnl = (f["price"] - p["avg"]) * q
            realised += pnl
            p["qty"] -= q
            if p["qty"] <= 0:
                closed.append({"symbol": f["symbol"], "type": f["symbol"][-2:], "time": f["time"], "price": f["price"],
                               "entry": p["avg"], "pnl": round(pnl, 2)})
                pos[f["symbol"]] = {"qty": 0, "avg": 0.0, "entry_time": None}
    open_ = []
    for s, p in pos.items():
        if p["qty"] > 0:
            ltp = ltp_of(s)
            open_.append({"symbol": s, "type": s[-2:], "qty": p["qty"], "avg": round(p["avg"], 2), "entry_time": p["entry_time"],
                          "ltp": ltp, "mtm": round((ltp - p["avg"]) * p["qty"], 2) if ltp is not None else 0.0})
    gross = realised + sum(o["mtm"] for o in open_)
    charges = CFG["cost_per_trip"] * len(entries)
    return {"entries": len(entries), "open": open_, "closed": closed, "realised": round(realised, 2),
            "gross": round(gross, 2), "charges_est": charges, "net": round(gross - charges, 2), "unmatched": unmatched}


@dataclass
class TradeWatch:
    """Discipline + in-trade alerts. The first book seen in live mode is a baseline (no alerts for the past)."""
    seen_entries: int = 0
    seen_closed: int = 0
    pos: dict = field(default_factory=dict)       # symbol -> {stop, high, armed, flags}
    flags: set = field(default_factory=set)

    def baseline(self, b: dict, feats: pd.DataFrame):
        self.seen_entries, self.seen_closed = b["entries"], len(b["closed"])
        for o in b["open"]:
            self._track(o, feats)
        if b["entries"] >= CFG["max_trades"]:
            self.flags.add("max")

    def _track(self, o: dict, feats: pd.DataFrame):
        before = feats[feats.index < (o["entry_time"] or feats.index[-1]).floor("min")]
        stop = (before["low_n"] if o["type"] == "CE" else before["high_n"]).iloc[-1] if len(before) else np.nan
        self.pos[o["symbol"]] = {"stop": float(stop) if np.isfinite(stop) else None, "high": o["avg"], "armed": False, "flags": set()}

    def step(self, b: dict, r, feats: pd.DataFrame) -> list[tuple[str, str]]:
        out, lot = [], None
        if b["entries"] > self.seen_entries:
            for o in b["open"]:
                if o["symbol"] not in self.pos:
                    self._track(o, feats)
                    st = self.pos[o["symbol"]]["stop"]
                    dist = abs(r["Close"] - st) if st else float("nan")
                    left = CFG["max_trades"] - b["entries"]
                    more = "LAST trade today." if left == 0 else f"{left} left." if left > 0 else "OVER the daily limit."
                    # NIFTY_<YYYYMMDD>_...: the rulebook says use the next expiry on expiry day
                    parts = o["symbol"].split("_")
                    expiring = len(parts) > 1 and isinstance(r.name, pd.Timestamp) and parts[1] == r.name.strftime("%Y%m%d")
                    warn = " EXPIRING TODAY (rulebook: next expiry) - time value decays fast, use a premium stop." if expiring else ""
                    out.append(("TRADE", f"Entry {b['entries']} (max {CFG['max_trades']}): {o['symbol']} @ {o['avg']:.2f}. Nifty stop "
                                         f"{'below' if o['type'] == 'CE' else 'above'} {st:.0f} ({dist:.0f} pts, ~Rs {dist * CFG['delta'] * o['qty']:.0f}). "
                                         f"Trail arms at +{CFG['trail_arm']:.0f}. {more}{warn}"))
            if b["entries"] > CFG["max_trades"]:
                out.append(("DISCIPLINE", f"Over the limit: {b['entries']} entries today (max {CFG['max_trades']})."))
            self.seen_entries = b["entries"]
        if len(b["closed"]) > self.seen_closed:
            for c in b["closed"][self.seen_closed:]:
                self.pos.pop(c["symbol"], None)
                out.append(("TRADE", f"Closed {c['symbol']} {c['entry']:.2f} -> {c['price']:.2f}: Rs {c['pnl']:+.0f}. "
                                     f"Day net Rs {b['net']:+.0f} after ~Rs {b['charges_est']:.0f} charges."))
            self.seen_closed = len(b["closed"])
            if b["entries"] >= CFG["max_trades"] and not b["open"] and "max" not in self.flags:
                self.flags.add("max")
                out.append(("DISCIPLINE", f"{CFG['max_trades']} trades done - stop for today. Day net Rs {b['net']:+.0f}."))
        if b["net"] <= -CFG["loss_cap"] and "cap" not in self.flags:
            self.flags.add("cap")
            out.append(("DISCIPLINE", f"LOSS CAP: day net Rs {b['net']:+.0f} (cap Rs {CFG['loss_cap']:.0f}). Close and stop for today."))
        elif b["realised"] - b["charges_est"] <= -CFG["loss_warn"] and "warn" not in self.flags:     # closed trades only
            self.flags.add("warn")
            booked = b["realised"] - b["charges_est"]
            out.append(("DISCIPLINE", f"Booked Rs {booked:+.0f} today - Rs {CFG['loss_cap'] + booked:.0f} left before the cap."))
        for o in b["open"]:
            p = self.pos.get(o["symbol"])
            if p is None or o["ltp"] is None:
                continue
            ce, ltp, fl = o["type"] == "CE", o["ltp"], p["flags"]
            p["high"] = max(p["high"], ltp)
            if p["stop"] is not None and "stop" not in fl and ((ce and r["Close"] < p["stop"]) or (not ce and r["Close"] > p["stop"])):
                fl.add("stop")
                out.append(("STOP", f"{o['symbol']}: Nifty closed {r['Close']:.0f}, {'below' if ce else 'above'} the stop {p['stop']:.0f}. "
                                    f"Idea invalid - exit (premium {ltp:.2f}, P&L Rs {o['mtm']:+.0f})."))
            if not p["armed"] and ltp >= o["avg"] + CFG["trail_arm"]:
                p["armed"] = True
                out.append(("TRAIL", f"{o['symbol']} +{ltp - o['avg']:.1f} pts: trail armed. Hold while Nifty stays "
                                     f"{'above' if ce else 'below'} the 9-EMA ({r['ema9']:.0f}); exit if premium falls {CFG['trail_gap']:.0f} from its high."))
            if p["armed"] and "trail" not in fl and ltp <= p["high"] - CFG["trail_gap"]:
                fl.add("trail")
                out.append(("TRAIL", f"{o['symbol']}: premium {ltp:.2f} is {p['high'] - ltp:.1f} below its high {p['high']:.2f} - trail hit, take it "
                                     f"(P&L Rs {o['mtm']:+.0f})."))
            if not fl & {"ema", "stop"} and ((ce and r["Close"] < r["ema9"] < r["ema21"]) or (not ce and r["Close"] > r["ema9"] > r["ema21"])):
                fl.add("ema")
                out.append(("STOP", f"{o['symbol']}: 9-EMA crossed {'under' if ce else 'over'} the 21-EMA against you "
                                    f"(Nifty {r['Close']:.0f}). Momentum is gone - consider exiting (P&L Rs {o['mtm']:+.0f})."))
        return out


# ------------------------------------------------------------------ checklist
def checklist(side: str, r, det: Detector, snap: dict, b: dict, t: pd.Timestamp) -> dict:
    ce = side == "CE"
    sg = 1 if ce else -1
    items = []

    def add(name, status, text):
        items.append({"check": name, "status": status, "text": text})

    c, e9, e21, atr = r["Close"], r["ema9"], r["ema21"], r["atr"]
    want = "up" if ce else "down"
    if det.regime == want:
        add("trend", "PASS", f"trend {want} (30-min {r['net30']:+.0f} pts)")
    elif det.regime == "range" and det.raw == "range":
        add("trend", "FAIL", f"range (30-min {r['net30']:+.0f} pts) - bought options bleed here")
    elif det.regime == "range":
        add("trend", "WAIT", f"leaving the range (30-min {r['net30']:+.0f} pts) - not a confirmed trend yet")
    elif det.regime in ("up", "down"):
        add("trend", "FAIL", f"trend is {det.regime} - this side is against it")
    else:
        add("trend", "WAIT", f"no confirmed trend yet (30-min {r['net30']:+.0f} pts)" if np.isfinite(r["net30"]) else "too early for a trend read")
    if sg * (e9 - e21) < 0:
        add("ema", "FAIL", f"9-EMA {e9:.0f} is {'below' if ce else 'above'} the 21-EMA {e21:.0f} - crossed against this side")
    elif sg * (c - e9) < 0:
        add("ema", "WAIT", f"Nifty {c:.0f} back {'under' if ce else 'over'} the 9-EMA {e9:.0f} - wait for it to turn back")
    else:
        add("ema", "PASS", f"Nifty {c:.0f} {'>' if ce else '<'} 9-EMA {e9:.0f} {'>' if ce else '<'} 21-EMA {e21:.0f}")
    ext = sg * (c - e9) / atr if atr else 0
    add("stretch", "WAIT" if ext > CFG["stretch_atr"] else "PASS",
        f"{sg * (c - e9):.0f} pts from the 9-EMA ({ext:.1f} ATR){' - chasing, wait for a pullback' if ext > CFG['stretch_atr'] else ''}")
    rng = r["High"] - r["Low"]
    spike = rng > CFG["spike_atr"] * atr and sg * (r["Close"] - r["Open"]) > 0
    add("spike", "WAIT" if spike else "PASS",
        f"last 1m bar {rng:.0f} pts ({rng / atr:.1f} ATR){' - spike candle, don' + chr(39) + 't buy its top' if spike else ''}")

    opt = [(s, o) for s, o in snap.get("options", {}).items() if o["type"] == side]
    S = c
    sym, o = min(opt, key=lambda so: abs(so[1]["strike"] - S)) if opt else (None, None)
    plan = {"symbol": sym}
    if o is not None and o["ltp"] is not None:
        bars = pd.DataFrame(o["bars"], columns=["t", "Open", "High", "Low", "Close", "Volume"]) if o["bars"] else None
        if bars is not None and len(bars) > 10:
            ch = (o["ltp"] / bars["Close"].iloc[-11] - 1) * 100
            add("premium", "WAIT" if ch > CFG["prem_chase_pct"] else "PASS",
                f"premium {ch:+.0f}% in 10 min{' - already ran, chasing' if ch > CFG['prem_chase_pct'] else ''}")
        else:
            add("premium", "WARN", "premium history too short to judge a chase")
        if o.get("bid") and o.get("ask"):
            spr = o["ask"] - o["bid"]
            ok = spr <= max(0.5, 0.005 * o["ltp"])
            add("spread", "PASS" if ok else "WARN", f"bid {o['bid']:.2f} / ask {o['ask']:.2f} (spread {spr:.2f})")
            limit = round(round((o["bid"] + o["ask"]) / 2 / 0.05) * 0.05, 2)
        else:
            limit = o["ltp"]
        plan.update(ltp=o["ltp"], bid=o.get("bid"), ask=o.get("ask"), limit=limit)
    else:
        add("premium", "WARN", "no premium data for the ATM contract")

    if not b.get("ok", True):
        add("book", "WAIT", f"trade book unavailable from Angel One - last known {b['entries']}/{CFG['max_trades']} trades, "
                            f"net Rs {b['net']:+.0f}; count your own until it is back")
    if b.get("unmatched"):
        add("book", "WARN", f"fills I can't pair (carried or short?): {'; '.join(b['unmatched'][:2])} - P&L may be off")
    held = [p for p in b["open"]]
    if held:
        add("position", "FAIL", f"already in {held[0]['symbol']} - manage that first")
    if b["entries"] >= CFG["max_trades"]:
        add("trades", "FAIL", f"{b['entries']} of {CFG['max_trades']} trades used - done for today")
    else:
        add("trades", "PASS", f"{b['entries']} of {CFG['max_trades']} trades used")
    left = CFG["loss_cap"] + b["net"]
    if left <= 0:
        add("loss", "FAIL", f"day net Rs {b['net']:+.0f} - loss cap hit")
    else:
        add("loss", "PASS" if b["net"] > -CFG["loss_warn"] else "WARN", f"day net Rs {b['net']:+.0f}, Rs {left:.0f} left before the cap")
    stop = r["low_n"] if ce else r["high_n"]
    dist = sg * (c - stop)
    lot = int((snap.get("expiry") or {}).get("lot") or 65)
    risk = dist * CFG["delta"] * lot + CFG["cost_per_trip"]
    plan.update(nifty_stop=round(float(stop), 1), stop_pts=round(float(dist), 1), risk_rs=round(float(risk)),
                premium_stop=round(plan["ltp"] - dist * CFG["delta"], 1) if plan.get("ltp") else None)
    if risk > left:
        add("risk", "FAIL", f"stop {stop:.0f} is {dist:.0f} pts away = ~Rs {risk:.0f} risk, more than the Rs {max(left, 0):.0f} left")
    elif dist < 0.5 * atr:
        add("risk", "WARN", f"stop {stop:.0f} only {dist:.0f} pts away - noise will hit it")
    else:
        add("risk", "PASS", f"stop {stop:.0f} ({dist:.0f} pts, ~Rs {risk:.0f} incl. charges)")
    if t - det.day < hm(CFG["setup_from"]):
        add("time", "WAIT", f"before {CFG['setup_from']} - opening range still forming")
    if t - det.day > hm(CFG["setup_until"]):
        add("time", "WARN", f"after {CFG['setup_until']} - your trades lost here; only an A+ setup")
    last = [x for x in b["closed"] if x["type"] == side]
    if last and plan.get("ltp") is not None:
        x = last[-1]
        if t - x["time"] <= pd.Timedelta(minutes=CFG["same_idea_min"]) and plan["ltp"] > x["price"] + CFG["same_idea_pts"]:
            add("same-idea", "WARN", f"you sold {side} at {x['price']:.2f} at {x['time']:%H:%M}; buying back {plan['ltp'] - x['price']:.0f} pts higher (16-Oct trades 2->3)")
    st = [i["status"] for i in items]
    verdict = "NO" if "FAIL" in st else "WAIT" if "WAIT" in st else "GO"
    plan["exit_rule"] = (f"exit if Nifty closes {'below' if ce else 'above'} {stop:.0f}; after +{CFG['trail_arm']:.0f} pts trail "
                         f"{CFG['trail_gap']:.0f} below the premium high; ~1 pt covers charges")
    return {"side": side, "verdict": verdict, "items": items, "plan": plan}


# ------------------------------------------------------------------ output
_SOUND = {"OPEN": [(660, 150)], "GAP": [(880, 150), (880, 150)], "TREND": [(880, 150), (880, 150)],
          "RANGE": [(500, 300)], "BREAKOUT": [(988, 120), (988, 120), (1175, 200)], "CUTOFF": [(400, 300)],
          "TRADE": [(1200, 100)], "TRAIL": [(1319, 120), (1319, 120)], "STOP": [(1500, 120)] * 3,
          "DISCIPLINE": [(350, 500)], "LAB": [(784, 150), (988, 150)], "STALE": [(300, 400)], "BOOK": [(300, 400)], "DOWN": [(300, 400)], "OK": [(700, 100)]}


SIM_TAG = ""        # "SIM " while `live --sim` runs, so test lines in alerts.log never look like real ones


def emit(t: pd.Timestamp, kind: str, text: str, log: bool = True, sound: bool = True, tag: str = "ALERT"):
    tag = SIM_TAG + tag
    line = f"{t:%Y-%m-%d %H:%M} {tag} {kind} {text}"
    print("\n>>> " + line + "\n", flush=True)
    if log:
        OUT.mkdir(parents=True, exist_ok=True)
        with open(ALERTS, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
    if sound:
        toast(f"Nifty {kind}", text)
        try:
            import winsound
            for f, d in _SOUND.get(kind, [(800, 150)]):
                winsound.Beep(f, d)
        except Exception:
            pass


_TOAST_PS = (
    "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing;"
    "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true;"
    "$n.ShowBalloonTip(8000, $env:ASSIST_TITLE, $env:ASSIST_TEXT, 'Info'); Start-Sleep -Seconds 9; $n.Dispose()")


def toast(title: str, text: str):
    """Windows notification without blocking (a hidden PowerShell per alert; failures ignored).
    The text goes through environment variables, so no quoting problems."""
    if os.name != "nt" or os.environ.get("ASSIST_NO_TOAST"):
        return
    try:
        import subprocess
        env = {**os.environ, "ASSIST_TITLE": title[:60], "ASSIST_TEXT": text[:250]}
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", _TOAST_PS], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    except Exception:
        pass


def write_state(obj: dict):
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, default=str, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)


def one_line(t, r, det: Detector, cl: dict, b: dict) -> str:
    v = lambda s: cl[s]["verdict"] + ("" if cl[s]["verdict"] == "GO" else
                                      " (" + ",".join(i["check"] for i in cl[s]["items"] if i["status"] in ("FAIL", "WAIT")) + ")")
    pos = f" | in {b['open'][0]['symbol'][-7:]} {b['open'][0]['mtm']:+.0f}" if b["open"] else ""
    return (f"{t:%H:%M} Nifty {r['Close']:.1f} ema9 {r['ema9']:.0f} | {det.regime.upper():7} | CE {v('CE')} | PE {v('PE')} "
            f"| trades {b['entries']}/{CFG['max_trades']} net Rs {b['net']:+.0f}{pos}")


def frame(rows: list, cols=("t", "Open", "High", "Low", "Close")) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(cols) + [None] * 0)
    df.index = pd.to_datetime(df.pop("t"))
    return df.astype(float)


def state_obj(t, snap, r, det, b, cl, recent) -> dict:
    keys = ["Close", "ema9", "ema21", "atr", "net30", "er30", "or_high", "or_low", "day_open", "low_n", "high_n"]
    return {"as_of": str(t), "wall": time.time(), "regime": det.regime, "prev_close": snap.get("prev_close"),
            "vix": snap.get("vix"), "expiry": snap.get("expiry"),
            "nifty": {k: (round(float(r[k]), 2) if np.isfinite(r[k]) else None) for k in keys},
            "day_high": float(max(r["day_hi_prev"], r["High"])) if np.isfinite(r["day_hi_prev"]) else float(r["High"]),
            "day_low": float(min(r["day_lo_prev"], r["Low"])) if np.isfinite(r["day_lo_prev"]) else float(r["Low"]),
            "book": b, "checklist": cl, "recent_alerts": recent[-10:], "rules": {k: CFG[k] for k in ("max_trades", "loss_cap", "setup_until")}}


# ------------------------------------------------------------------ modes
def get_snapshot(expiry=None) -> dict:
    url = f"{SERVER}/api/assist" + (f"?expiry={expiry}" if expiry else "")
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


class _Sim:
    """`live --sim 2026-10-16T09:20`: feed run_live a past session (history files, no server) at --sim-speed real
    seconds per poll, with fills from --trades as the trade book - tests the whole live path when the market is shut."""
    def __init__(self, args):
        import assist_data
        self.snap_at, self.t = assist_data.snapshot, pd.Timestamp(args.sim)
        self.every, self.speed, self.trades = args.every, args.sim_speed, []
        if args.trades:
            df = pd.read_csv(args.trades, dtype=str)
            self.trades = [{"tradingsymbol": x["symbol"], "transactiontype": x["side"].upper(), "fillsize": x["qty"],
                            "fillprice": x["price"], "filltime": x["time"], "orderid": f"sim{i}"} for i, x in df.iterrows()]

    def __call__(self, expiry=None) -> dict:
        self.t += pd.Timedelta(seconds=self.every)
        if self.t - self.t.normalize() > hm("15:30"):
            raise SystemExit("sim: session over")
        now = f"{self.t:%H:%M:%S}"
        acct = {"trades": [x for x in self.trades if x["filltime"] <= now], "positions": []}
        return self.snap_at(self.t, expiry=expiry, live_mode=False, acct=acct) | {"account": acct}

    def sleep(self):
        time.sleep(self.speed)


def make_lab(day: pd.Timestamp, mode: str):
    """The paper-traded lab rules (lab_alerts.py) for one session; None (with a note) if their inputs are missing."""
    try:
        import lab_alerts
        pC, ppC = (lab_alerts.prev_closes(day) + [None, None])[:2]
        sigma, hivol = lab_alerts.trendband_inputs(day)
        print(f"lab rules armed (paper): GAPFADE/REVFADE vs prev close {pC:.0f}; TRENDBAND "
              f"{'ON (high-volatility day)' if hivol else 'off (normal-volatility day)'}", flush=True)
        return lab_alerts.LabRules(day, pC, ppC, mode=mode, tb_sigma=sigma, tb_hivol=hivol)
    except Exception as e:
        print(f"lab rules unavailable today: {type(e).__name__}: {e}", flush=True)
        return None


def make_pullback(day: pd.Timestamp, mode: str):
    """The practice-setup alert (pullback.SETUP): BASEBREAK (basebreak.py, base -> break -> retest) since 21-Oct,
    before that PULLBACK (pullback.py); paper outcome -> basebreak_journal.csv / pullback_journal.csv."""
    try:
        import pullback
        return pullback.make_rule(day, mode)
    except Exception as e:
        print(f"practice-setup rule unavailable today: {type(e).__name__}: {e}", flush=True)
        return None


def run_live(args):
    global SIM_TAG
    sim = _Sim(args) if args.sim else None
    SIM_TAG = "SIM " if sim else ""
    fetch = sim or get_snapshot
    pause = sim.sleep if sim else lambda: time.sleep(args.every)
    print("assist live - DATA ONLY, no orders. " + (f"SIMULATING {args.sim}" if sim else f"Reading {SERVER}/api/assist every {args.every:.0f} s.")
          + " Ctrl+C to stop.", flush=True)
    det = tw = lab = pb = None
    day, last_min, down, stale, recent = None, None, False, False, []
    b_last, fills_ok_since, book_down, tw_ready = None, None, False, False
    while True:
        try:
            snap = fetch(args.expiry)
            if down:
                emit(pd.Timestamp.now(), "OK", "server reachable again", tag="DATA")
                down = False
        except Exception as e:
            if not down:
                emit(pd.Timestamp.now(), "DOWN", f"ChartReplay server not reachable ({type(e).__name__}) - start: cd ChartReplay && python server.py", tag="DATA")
                down = True
            pause()
            continue
        t = pd.Timestamp(snap["now"])
        today = t.normalize()
        nb = frame(snap["nifty"])
        feats = features(nb, today)
        tod = t - today
        if not len(feats):
            if hm("09:15") <= tod <= hm("15:30") and t.weekday() < 5:
                print(f"{t:%H:%M:%S} waiting for the first Nifty bar...", flush=True)
                if tod >= hm("09:18") and not stale:       # silence must never look like "all fine"
                    stale = True
                    emit(t, "STALE", f"no Nifty bars for today yet. ChartReplay live error: {snap.get('live_error')}", tag="DATA")
            elif not getattr(run_live, "_idle", False):
                run_live._idle = True
                print(f"{t:%a %H:%M} market closed - waiting for the next session (starts alerting from 09:15).", flush=True)
            pause()
            continue
        if day != today:
            day, last_min = today, None
            det, tw = Detector(today, snap.get("prev_close")), TradeWatch()
            lab = make_lab(today, "sim" if sim else "live")
            pb = make_pullback(today, "sim" if sim else "live")
            b_last, fills_ok_since, book_down, tw_ready = None, None, False, False
        age = (t - (feats.index[-1] + pd.Timedelta("1min"))).total_seconds()
        if hm("09:17") <= tod <= hm("15:15") and age > CFG["stale_s"] and not stale:
            stale = True
            emit(t, "STALE", f"last Nifty bar {feats.index[-1]:%H:%M}, {age:.0f}s old. ChartReplay live error: {snap.get('live_error')}", tag="DATA")
        elif stale and age <= CFG["stale_s"]:
            stale = False
            emit(t, "OK", "Nifty data fresh again", tag="DATA")
        acct = snap.get("account")
        ltp_of = lambda s: (snap["options"].get(s) or {}).get("ltp")
        if acct is not None:
            b = book(fills_from_tradebook(acct["trades"], today), t, ltp_of) | {"ok": True}
            fills_ok_since, b_last = t, b
            if book_down:
                book_down = False
                emit(t, "OK", "trade book available again", tag="DATA")
        else:       # never treat a missing trade book as "no trades": keep the last known one and say so
            b = (b_last or book([], t, ltp_of)) | {"ok": False}
            if hm("09:15") <= tod <= hm("15:30") and t.weekday() < 5 and not book_down \
                    and (t - (fills_ok_since or t.normalize() + hm("09:15"))) > pd.Timedelta(minutes=2):
                book_down = True
                emit(t, "BOOK", f"trade book unavailable from Angel One - trade count and loss cap can't be checked. "
                                f"Last known: {b['entries']} trades, net Rs {b['net']:+.0f}.", tag="DATA")
        new = feats[feats.index > last_min] if last_min is not None else feats
        warm = last_min is None
        if pb is not None:
            pb.snap = snap          # the ATM contract's bars/ltp price the practice-setup paper trade
        for ts, r in new.iterrows():
            done = ts + pd.Timedelta("1min")
            lab_alerts = lab.step(ts, r, snap) if lab is not None else []
            det_alerts = det.step(done, r, snap)
            pb_alerts = pb.step(ts, r, det.regime) if pb is not None else []
            for kind, text in det_alerts + lab_alerts + pb_alerts:
                # on (re)start, earlier minutes only rebuild state; alert only on the latest 2 minutes
                if not warm or (t - done) <= pd.Timedelta(minutes=2):
                    emit(done, kind, text)
                    recent.append(f"{done:%H:%M} {kind} {text}")
        r = feats.iloc[-1]
        if acct is None:
            if warm:
                print("(no trade book from the server yet - trade/discipline alerts start once it arrives)", flush=True)
        elif not tw_ready:              # first trade book seen = baseline (no alerts for what already happened)
            tw.baseline(b, feats)
            tw_ready = True
        else:
            for kind, text in tw.step(b, r, feats):
                emit(t, kind, text)
                recent.append(f"{t:%H:%M} {kind} {text}")
        cl = {s: checklist(s, r, det, snap, b, t) for s in ("CE", "PE")}
        if last_min is None or feats.index[-1] > last_min:
            print(one_line(feats.index[-1] + pd.Timedelta("1min"), r, det, cl, b), flush=True)
        last_min = feats.index[-1]
        write_state(state_obj(t, snap, r, det, b, cl, recent) | {"account_ok": acct is not None, "data_age_s": age})
        pause()


def run_check(args):
    if not STATE.exists():
        raise SystemExit("no state.json - is `python assist.py live` running?")
    print_check(json.loads(STATE.read_text(encoding="utf-8")), args.side.upper())


def print_check(s: dict, side: str):
    age = time.time() - s["wall"]
    cl = s["checklist"][side]
    n = s["nifty"]
    stale = age > 60 and not s.get("replay")
    print(f"as of {s['as_of']}" + ("" if s.get("replay") else f" (written {age:.0f}s ago{' - STALE, is assist live running?' if stale else ''})"))
    print(f"Nifty {n['Close']}  9-EMA {n['ema9']}  21-EMA {n['ema21']}  ATR {n['atr']}  regime {s['regime'].upper()}  "
          f"day {s['day_low']:.0f}-{s['day_high']:.0f}  OR {n['or_low']}-{n['or_high']}  prev close {s['prev_close']}  VIX {s['vix']}")
    print(f"\n{side}: {cl['verdict']}")
    for i in cl["items"]:
        print(f"  [{i['status']:4}] {i['check']:9} {i['text']}")
    p = cl["plan"]
    print(f"\nplan: {p.get('symbol')}  ltp {p.get('ltp')}  bid/ask {p.get('bid')}/{p.get('ask')}  limit ~{p.get('limit')}")
    print(f"      Nifty stop {p.get('nifty_stop')} ({p.get('stop_pts')} pts) ~ premium {p.get('premium_stop')}  risk ~Rs {p.get('risk_rs')}")
    print(f"      {p.get('exit_rule')}")
    b = s["book"]
    print(f"\nday: {b['entries']}/{s['rules']['max_trades']} trades, net Rs {b['net']:+.0f} (gross {b['gross']:+.0f}, charges ~{b['charges_est']:.0f})"
          + (f", open {b['open'][0]['symbol']} @ {b['open'][0]['avg']} ltp {b['open'][0]['ltp']}" if b["open"] else ""))
    if s.get("recent_alerts"):
        print("recent alerts:\n  " + "\n  ".join(s["recent_alerts"][-5:]))


def run_replay(args):
    import assist_data
    import data_source
    import options
    day = pd.Timestamp(args.date)
    spot = data_source.load(options.SPOT, "1m")
    fills = fills_from_csv(args.trades, day) if args.trades else []
    det, tw = None, TradeWatch()
    lab = make_lab(day, "replay")
    pb = make_pullback(day, "replay")
    first, last = int(spot.index.searchsorted(day + hm("09:15"))), int(spot.index.searchsorted(day + hm("15:30")))
    if first >= last:
        raise SystemExit(f"no Nifty 1m data for {day.date()}")
    for ts in spot.index[first:last]:
        t = ts + pd.Timedelta("1min")
        snap = assist_data.snapshot(t, expiry=args.expiry, live_mode=False)
        if det is None:
            det = Detector(day, snap.get("prev_close"))
        feats = features(frame(snap["nifty"]), day)
        r = feats.iloc[-1]
        b = book(fills, t, lambda s: _replay_ltp(s, t, snap))
        alerts = det.step(t, r, snap) + tw.step(b, r, feats) + (lab.step(ts, r, snap) if lab is not None else [])
        alerts += pb.step(ts, r, det.regime) if pb is not None else []
        for kind, text in alerts:
            emit(t, kind, text, log=args.log, sound=args.speed > 0, tag="ALERT REPLAY" if args.log else "ALERT")
        if f"{t:%H:%M}" in (args.at or []):
            cl = {s: checklist(s, r, det, snap, b, t) for s in ("CE", "PE")}
            st = json.loads(json.dumps(state_obj(t, snap, r, det, b, cl, []) | {"replay": True}, default=str))
            for side in ("CE", "PE"):
                print(f"\n---------------- check {side} at {t:%H:%M} (replay)")
                print_check(st, side)
            print()
        if args.verbose or args.speed > 0 or (t.minute % 15 == 0):
            cl = {s: checklist(s, r, det, snap, b, t) for s in ("CE", "PE")}
            print(one_line(t, r, det, cl, b), flush=True)
            if args.speed > 0:
                write_state(state_obj(t, snap, r, det, b, cl, []) | {"replay": True})
        if args.speed > 0:
            time.sleep(args.speed)


def _replay_ltp(sym: str, t, snap):
    if sym in snap["options"]:
        return snap["options"][sym]["ltp"]
    import options
    raw = options.real_raw(sym)
    if raw is None:
        return None
    done = raw[raw.index + pd.Timedelta("1min") <= t]
    return float(done["Close"].iloc[-1]) if len(done) else None


def run_calibrate(args):
    import data_source
    import options
    spot = data_source.load(options.SPOT, "1m")
    days = pd.DatetimeIndex(spot.index.normalize().unique())
    days = days[days <= pd.Timestamp(args.until)] if args.until else days
    days = days[-args.days:]
    rows = []
    for d in days:
        i0 = int(spot.index.searchsorted(d))
        i1 = int(spot.index.searchsorted(d + pd.Timedelta(days=1)))
        nb = spot.iloc[max(0, i0 - 60):i1]
        prev = spot.iloc[i0 - 1]["Close"] if i0 else None
        feats = features(nb, d)
        det = Detector(d, prev)
        cnt = {}
        for ts, r in feats.iterrows():
            for kind, _ in det.step(ts + pd.Timedelta("1min"), r, {}):
                cnt[kind] = cnt.get(kind, 0) + 1
        rows.append({"day": d.date(), **cnt})
    df = pd.DataFrame(rows).fillna(0).set_index("day")
    setup = [k for k in ("GAP", "TREND", "RANGE", "BREAKOUT") if k in df]
    df["setup"] = df[setup].sum(axis=1)
    print(df.astype(int).tail(args.show).to_string())
    print(f"\n{len(df)} days: setup alerts/day median {df['setup'].median():.1f}, mean {df['setup'].mean():.1f}, max {df['setup'].max():.0f}")
    print("per kind (mean/day):", {k: round(df[k].mean(), 2) for k in setup})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("live")
    p.add_argument("--every", type=float, default=10)
    p.add_argument("--expiry", help="YYYY-MM-DD (default: nearest; on expiry day the next one)")
    p.add_argument("--sim", help="test without the market: replay a past session through the live loop, e.g. 2026-10-16T09:20")
    p.add_argument("--sim-speed", type=float, default=0.05, help="real seconds per simulated poll")
    p.add_argument("--trades", help="sim only: CSV of fills used as the trade book")
    p = sub.add_parser("check")
    p.add_argument("side", choices=["CE", "PE", "ce", "pe"])
    p = sub.add_parser("replay")
    p.add_argument("date")
    p.add_argument("--trades", help="CSV: time,symbol,side,qty,price")
    p.add_argument("--expiry")
    p.add_argument("--log", action="store_true", help="also append the alerts to alerts.log (tests the Monitor wiring)")
    p.add_argument("--speed", type=float, default=0, help="seconds per minute (0 = instant, no sound)")
    p.add_argument("--verbose", action="store_true", help="checklist line every minute")
    p.add_argument("--at", nargs="*", help="HH:MM times (bar close) to print the full CE/PE check at")
    p = sub.add_parser("calibrate")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--until")
    p.add_argument("--show", type=int, default=15)
    a = ap.parse_args()
    {"live": run_live, "check": run_check, "replay": run_replay, "calibrate": run_calibrate}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
