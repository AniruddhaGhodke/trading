"""
Live watcher for journal calls during market hours (Angel One quotes, polled once a minute).

  python watch.py            # ranked calls only, refresh every 60 s until 15:31
  python watch.py --all      # include research-only calls
  python watch.py --once     # print one snapshot and exit

What it checks (informational - the official outcome is still decided by `python main.py daily`
after the close, using the same rules as the backtest):
  PENDING calls (signal yesterday, entry at today's open):
      SKIP if today's open is at/below the stop, or gapped up more than 1 ATR above yesterday's close.
      Otherwise ENTERED at the open; then tracked like an open position.
  OPEN calls: STOP HIT if today's low touched the stop, TARGET HIT if today's high touched the target.
  Rule exits (e.g. RSI14 > 50) and time exits are decided on the CLOSE, so they show up after `daily`.
New alerts beep once and are appended to data/watch_alerts.log.
"""

import argparse
import os
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone

import pandas as pd

import config
from core import journal
from core.indicators import atr as atr_fn

warnings.filterwarnings("ignore")
IST = timezone(timedelta(hours=5, minutes=30))
ALERT_LOG = config.DATA_DIR / "watch_alerts.log"


def _plans():
    from strategies import REGISTRY
    return {name: s.exit_plan for name, s in REGISTRY.items()}


def _atr_last(symbol: str) -> float:
    p = config.CACHE_DIR / f"{symbol}.csv"
    if not p.exists():
        return float("nan")
    df = pd.read_csv(p, index_col=0, parse_dates=True).tail(40)
    return float(atr_fn(df).iloc[-1])


def evaluate(calls: pd.DataFrame, q: pd.DataFrame, plans: dict, market_open: bool) -> list[dict]:
    """market_open = a session is trading TODAY (not just 'after 9:15' - weekends/holidays return stale quotes)."""
    rows = []
    for _, c in calls.iterrows():
        s = c["symbol"]
        if s not in q.index:
            rows.append({"symbol": s, "strategy": c["strategy"], "status": "no quote"})
            continue
        x = q.loc[s]
        plan = plans.get(c["strategy"])
        row = {"symbol": s, "strategy": c["strategy"], "ltp": x["ltp"]}
        if c["status"] == "PENDING":
            # The entry session is the first one AFTER the signal day; a quote from the signal day
            # itself (evening run, weekend, holiday) is not an entry.
            fresh = pd.notna(x.get("session")) and x["session"] > pd.Timestamp(c["signal_date"])
            if not market_open or not fresh or x["open"] <= 0:
                rows.append({**row, "status": "waiting for open", "stop": c["stop"]})
                continue
            entry, stop = x["open"], c["stop"]
            atr = _atr_last(s)
            if entry <= stop:
                rows.append({**row, "status": "SKIP: opened below stop", "entry": entry, "stop": stop})
                continue
            if entry - c["ref_close"] > config.MAX_ENTRY_GAP_ATR * atr:
                rows.append({**row, "status": "SKIP: gap-up > 1 ATR", "entry": entry, "stop": stop})
                continue
            risk = entry - stop
            target = (entry * (1 + plan.target_pct) if plan and plan.target_pct else
                      entry + plan.target_r * risk if plan and plan.target_r else None)
            day_low, day_high = x["low"], x["high"]
            started = "entered today"
        else:   # OPEN
            entry, stop, target = c["entry_price"], c["current_stop"], c["target"]
            day_low, day_high = x["low"], x["high"]
            started = f"day {int(c['bars_held'] or 0) + 1}"
        if day_low <= stop:
            status = "STOP HIT"
        elif target and not pd.isna(target) and day_high >= target:
            status = "TARGET HIT"
        else:
            status = f"open ({started})"
            due = c.get("exit_reason")
            if c["status"] == "OPEN" and isinstance(due, str) and due.startswith("DUE"):
                status += " - " + due   # rule/time exit decided at yesterday's close
        rows.append({**row, "status": status, "entry": entry, "stop": stop, "target": target,
                     "pnl%": (x["ltp"] / entry - 1) * 100,
                     "to_stop%": (x["ltp"] / stop - 1) * 100,
                     "to_tgt%": (target / x["ltp"] - 1) * 100 if target and not pd.isna(target) else None})
    return rows


def show(rows: list[dict], now: datetime, nifty: str):
    os.system("cls" if os.name == "nt" else "clear")
    print(f"WATCH {now:%Y-%m-%d %H:%M:%S} IST | {nifty} | refresh 60s, Ctrl+C to stop\n")
    print(f"{'Symbol':<14} {'Strategy':<16} {'LTP':>9} {'Entry':>9} {'Stop':>9} {'Target':>9} "
          f"{'P&L%':>7} {'toStop':>7} {'toTgt':>7}  Status")
    print("-" * 115)
    order = {"STOP HIT": 0, "TARGET HIT": 1}
    for r in sorted(rows, key=lambda r: (order.get(r["status"], 2), r["symbol"])):
        f = lambda k, w, p=2: f"{r[k]:>{w}.{p}f}" if r.get(k) is not None and not pd.isna(r.get(k)) else " " * w
        print(f"{r['symbol']:<14} {r['strategy']:<16} {f('ltp', 9)} {f('entry', 9)} {f('stop', 9)} {f('target', 9)} "
              f"{f('pnl%', 7)} {f('to_stop%', 7)} {f('to_tgt%', 7)}  {r['status']}")
    print("\nRule/time exits are decided at the close by `python main.py daily`.")


def alert(rows, seen: set, now: datetime):
    new = [r for r in rows if r["status"] in ("STOP HIT", "TARGET HIT") or r["status"].startswith("SKIP")]
    new = [r for r in new if (r["symbol"], r["strategy"], r["status"]) not in seen]
    for r in new:
        seen.add((r["symbol"], r["strategy"], r["status"]))
        with open(ALERT_LOG, "a") as fh:
            fh.write(f"{now:%Y-%m-%d %H:%M:%S} {r['symbol']} {r['strategy']} {r['status']} ltp={r.get('ltp')}\n")
    if new:
        try:
            import winsound
            winsound.Beep(1000, 300)
        except Exception:
            print("\a", end="")
        print("\nNEW ALERTS: " + "; ".join(f"{r['symbol']} {r['status']}" for r in new))


def main():
    ap = argparse.ArgumentParser(description="Live watcher for journal calls")
    ap.add_argument("--all", action="store_true", help="include research-only (unranked) calls")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=60, help="seconds between refreshes")
    args = ap.parse_args()

    con = journal.connect()
    calls = journal.active_calls(con)
    con.close()
    if not args.all:   # only strategies that are live NOW (demoted ones keep being tracked by `daily`)
        from strategies import REGISTRY
        live = {n for n, s in REGISTRY.items() if not s.research_only}
        calls = calls[calls["rank"].notna() & calls["strategy"].isin(live)]
    if calls.empty:
        print("No active calls in the journal. Run `python main.py daily` after the close first.")
        return

    from core.angel_data import Angel
    a = Angel()
    plans = _plans()
    syms = sorted(set(calls["symbol"]))
    seen = set()
    print(f"Watching {len(calls)} calls on {len(syms)} stocks...")
    while True:
        now = datetime.now(IST)
        q = a.quotes(syms + ["^NSEI"])
        today = pd.Timestamp(now.date())
        market_open = (now.hour, now.minute) >= (9, 15) and "session" in q and (q["session"] == today).any()
        nifty = ""
        if "^NSEI" in q.index:
            n = q.loc["^NSEI"]
            nifty = f"Nifty {n['ltp']:.1f} ({(n['ltp'] / n['prev_close'] - 1) * 100:+.2f}%)"
        rows = evaluate(calls, q.drop(index="^NSEI", errors="ignore"), plans, market_open)
        show(rows, now, nifty)
        if market_open:   # stale quotes (closed market) must not raise STOP/TARGET/SKIP alerts
            alert(rows, seen, now)
        elif now.weekday() >= 5 or (now.hour, now.minute) >= (9, 30):   # weekend / holiday
            print("\nMarket closed today - last session's prices shown, no alerts.")
            break
        else:
            print("\nPre-open - waiting for the 9:15 session.")
        if args.once or (now.hour, now.minute) >= (15, 31):
            break
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
