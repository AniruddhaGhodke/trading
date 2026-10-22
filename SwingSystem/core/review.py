"""
Daily post-trade review: WHY did each call work or fail, and is each strategy behaving
the way its backtest said it would?

Three layers, all written to the journal by `main.py track` and read by `main.py review`:
  1. features   - snapshot of the setup at signal time (RSI, drop, ATR%, regime, sector, ...).
                  Strictly backward-looking, so analysing them later is not hindsight.
  2. excursions - MFE/MAE during the trade, Nifty over the same window, and the stock's move in
                  the 5 sessions after the exit. These separate "entry was wrong" from "stop too
                  tight" from "exit too late" from "the whole market fell".
  3. drift      - forward results per strategy vs its backtest baseline (2024+, mostly out-of-sample
                  for the lab strategies), measured per signal DAY because calls cluster in selloffs.

What the review is NOT for: tuning parameters from live results. A pattern seen here becomes a
hypothesis for the lab (train -> holdouts), never a direct edit to a live strategy.
"""

import hashlib
import json
from datetime import datetime

import numpy as np
import pandas as pd

import config
from core.indicators import atr as atr_fn, rsi, sma

REVIEW_DIR = config.DATA_DIR / "reviews"
BASELINE_FILE = REVIEW_DIR / "baseline.json"
BASELINE_START = "2024-01-01"
POST_EXIT_BARS = 5
MIN_DAYS_FOR_VERDICT = 10      # distinct signal days before forward-vs-backtest is judged
MIN_TRADES_FOR_FEATURES = 20   # closed trades before feature splits are shown


# ---------------------------------------------------------------------------
# 1. features at signal time
# ---------------------------------------------------------------------------
def strategy_version(strat) -> str:
    """Short hash of everything that defines a strategy's trades (not research_only/score)."""
    spec = f"{strat.name}|{strat.description}|{strat.exit_plan!r}"
    return hashlib.sha1(spec.encode()).hexdigest()[:8]


def signal_features(raw: pd.DataFrame, i: int, actx: pd.DataFrame, sector: str | None) -> dict:
    """Setup description at bar i (signal close). Uses bars <= i only."""
    df = raw.iloc[: i + 1]
    c = df["Close"]
    last = float(c.iloc[-1])

    def pct(a, b):
        return round((a / b - 1) * 100, 2) if b and not pd.isna(b) else None

    ctx = actx.iloc[: i + 1]
    idx = ctx["idx_close"] if "idx_close" in ctx else None
    vol20 = df["Volume"].iloc[-21:-1].mean()
    return {
        "rsi2": round(float(rsi(c, 2).iloc[-1]), 1),
        "rsi14": round(float(rsi(c, 14).iloc[-1]), 1),
        "atr_pct": round(float(atr_fn(df).iloc[-1]) / last * 100, 2),
        "drop_20d_high": round((1 - last / float(c.iloc[-20:].max())) * 100, 2),
        "ret_1d": pct(last, c.iloc[-2]) if len(c) > 1 else None,
        "ret_5d": pct(last, c.iloc[-6]) if len(c) > 5 else None,
        "dist_sma50": pct(last, sma(c, 50).iloc[-1]),
        "dist_sma200": pct(last, sma(c, 200).iloc[-1]),
        "vol_ratio": round(float(df["Volume"].iloc[-1] / vol20), 2) if vol20 else None,
        "nifty_ret_5d": pct(idx.iloc[-1], idx.iloc[-6]) if idx is not None and len(idx) > 5 else None,
        "regime": str(ctx["regime"].iloc[-1]) if "regime" in ctx else None,
        "sector": sector,
    }


# ---------------------------------------------------------------------------
# 2. excursions / benchmark / post-exit, plus a plain-English diagnosis
# ---------------------------------------------------------------------------
def excursions(df: pd.DataFrame, ctx: pd.DataFrame, call: pd.Series) -> dict:
    """
    df = prepared/adjusted price frame of the symbol, call = journal row with outcome filled.
    Two price bases: the logged stop is in signal-day prices (rescale by `adj`), while the stored
    entry/exit prices are in the basis of the last `track` update (rescale by `k`).
    """
    sd = pd.Timestamp(call["signal_date"])
    if sd not in df.index or pd.isna(call.get("entry_date")) or pd.isna(call.get("entry_price")):
        return {}
    adj = float(df.loc[sd, "Close"]) / call["ref_close"]
    ie = df.index.get_loc(pd.Timestamp(call["entry_date"]))
    entry = float(df["Open"].iloc[ie])          # the simulator always enters at the entry bar's open
    k = entry / call["entry_price"]
    stop0 = call["stop"] * adj
    risk = entry - stop0
    closed = call["status"] == "CLOSED" and not pd.isna(call.get("exit_date"))
    ix = df.index.get_loc(pd.Timestamp(call["exit_date"])) if closed else len(df) - 1
    win = df.iloc[ie: ix + 1]
    hi, lo = float(win["High"].max()), float(win["Low"].min())
    out = {
        "mfe_pct": round((hi / entry - 1) * 100, 2),
        "mae_pct": round((lo / entry - 1) * 100, 2),
        "mfe_r": round((hi - entry) / risk, 2) if risk > 0 else None,
        "days_to_mfe": int(np.argmax(win["High"].to_numpy())) + 1,
    }
    idx = ctx["idx_close"].reindex(df.index).ffill()
    out["nifty_ret_pct"] = round((idx.iloc[ix] / idx.loc[sd] - 1) * 100, 2)
    if closed and ix + POST_EXIT_BARS < len(df):
        out["post_exit_5d_pct"] = round((df["Close"].iloc[ix + POST_EXIT_BARS] / (call["exit_price"] * k) - 1) * 100, 2)
    if closed:
        out["diagnosis"] = diagnose({**call.to_dict(), **out})
    return out


def diagnose(t: dict) -> str:
    """One label per closed trade. Order matters: the first matching explanation wins."""
    r, mfe_r = t.get("r_multiple") or 0.0, t.get("mfe_r")
    reason, nifty, post = t.get("exit_reason"), t.get("nifty_ret_pct"), t.get("post_exit_5d_pct")
    ret = t.get("return_pct") or 0.0
    if r > 0:
        if reason in ("target", "target_gap"):
            return "WIN target hit"
        if mfe_r is not None and mfe_r >= 1.0 and r < 0.5 * mfe_r:
            return "WIN but gave back most of the move (exit late?)"
        if nifty is not None and nifty > 0 and ret - nifty < 0:
            return "WIN but lagged Nifty (market carried it)"
        return "WIN rule/time exit"
    if mfe_r is not None and mfe_r >= 1.0:
        return "LOSS after being +1R (no profit protection)"
    if reason in ("stop", "stop_gap") and post is not None and post > 0 and \
            (t["exit_price"] * (1 + post / 100)) > t["entry_price"]:
        return "LOSS stopped then recovered above entry (stop too tight / noise)"
    if nifty is not None and nifty <= -2.0 and ret - nifty > -2.0:
        return "LOSS market fell (beta, not the setup)"
    if mfe_r is not None and mfe_r < 0.25:
        return "LOSS never worked (entry/setup wrong)"
    if reason == "time":
        return "LOSS time exit, no follow-through"
    return "LOSS other"


# ---------------------------------------------------------------------------
# 3. baseline (backtest expectation) and drift
# ---------------------------------------------------------------------------
def build_baseline(prices: dict, ctx: pd.DataFrame, strategies, existing: dict | None = None) -> dict:
    """Per-strategy backtest stats since BASELINE_START. Rebuilt only for new/changed versions."""
    from core.backtest import backtest_strategy
    base = dict(existing or {})
    for s in strategies:
        v = strategy_version(s)
        if base.get(s.name, {}).get("version") == v and "avg_excess" in base[s.name]:
            continue
        print(f"  baseline: backtesting {s.name} since {BASELINE_START} ...", flush=True)
        t = backtest_strategy(s, prices, ctx, start=BASELINE_START)
        c = t[t["status"] == "CLOSED"] if len(t) else t
        if len(c) < 5:
            base[s.name] = {"version": v, "trades": int(len(c))}
            continue
        idx = ctx["idx_close"]
        idx = idx.reindex(idx.index.union(pd.DatetimeIndex(pd.concat([c["signal_date"], c["exit_date"]]).unique()))).ffill()
        c = c.assign(excess=c["return_pct"] - (idx.reindex(c["exit_date"]).to_numpy()
                                               / idx.reindex(c["signal_date"]).to_numpy() - 1) * 100)
        day = c.groupby("signal_date")["excess"].mean()
        base[s.name] = {
            "version": v, "since": BASELINE_START, "trades": int(len(c)), "days": int(len(day)),
            "avg_ret": round(float(c["return_pct"].mean()), 3),
            "std_ret": round(float(c["return_pct"].std()), 3),
            "avg_excess": round(float(c["excess"].mean()), 3),
            "day_std": round(float(day.std()), 3),      # std of per-day mean EXCESS return
            "win_rate": round(float((c["return_pct"] > 0).mean() * 100), 1),
            "avg_hold": round(float(c["bars_held"].mean()), 1),
            "stop_rate": round(float(c["exit_reason"].isin(["stop", "stop_gap"]).mean() * 100), 1),
            "built": datetime.now().strftime("%Y-%m-%d"),
        }
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(base, indent=1))
    return base


def load_baseline() -> dict:
    return json.loads(BASELINE_FILE.read_text()) if BASELINE_FILE.exists() else {}


def drift(calls: pd.DataFrame, b: dict) -> dict:
    """
    Forward vs backtest for one strategy, on EXCESS return (call minus Nifty over the same window),
    so a market-wide selloff isn't read as a broken strategy. Only MATURE signal days count (every
    call from that day closed or skipped): stop-outs close first, so the early closed set is biased
    towards losers. Per-day means, so one selloff with 20 calls counts once.
    """
    done = calls.groupby("signal_date")["status"].transform(lambda x: x.isin(["CLOSED", "SKIPPED"]).all())
    m = calls[done & (calls["status"] == "CLOSED")]
    m = m.assign(excess=m["return_pct"] - m["nifty_ret_pct"])
    day = m.groupby("signal_date")["excess"].mean().dropna()
    n_days = len(day)
    out = {"trades": len(m), "days": n_days, "fwd_avg": m["return_pct"].mean() if len(m) else np.nan,
           "fwd_excess": day.mean() if n_days else np.nan,
           "fwd_win": (m["return_pct"] > 0).mean() * 100 if len(m) else np.nan,
           "fwd_stop": m["exit_reason"].isin(["stop", "stop_gap"]).mean() * 100 if len(m) else np.nan}
    if not b.get("day_std") or "avg_excess" not in b:
        out["verdict"] = "no baseline"
        return out
    if n_days == 0:
        out["verdict"] = "no mature signal days yet"
        return out
    z = (day.mean() - b["avg_excess"]) / (b["day_std"] / np.sqrt(n_days))
    out["z"] = z
    if n_days < MIN_DAYS_FOR_VERDICT:
        out["verdict"] = f"too early ({n_days}/{MIN_DAYS_FOR_VERDICT} mature signal days)"
    elif z < -2:
        out["verdict"] = "WORSE than backtest -> demotion candidate (confirm in lab first)"
    elif z > 2:
        out["verdict"] = "better than backtest (luck or regime - don't size up yet)"
    else:
        out["verdict"] = "consistent with backtest"
    return out


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def report(calls: pd.DataFrame, baseline: dict, live: set, asof: str) -> str:
    L = [f"# Daily review {asof}", ""]
    closed = calls[calls["status"] == "CLOSED"].copy()
    new = closed[closed["reviewed_on"].isna() & closed["diagnosis"].notna()]

    L += [f"## Newly closed calls ({len(new)})", ""]
    if len(new):
        L += ["| Signal | Symbol | Strategy | Exit | Ret% | R | MFE R | MAE% | Nifty% | +5d after% | Why |",
              "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
        for _, r in new.sort_values("return_pct").iterrows():
            L.append(f"| {r['signal_date']:%Y-%m-%d} | {r['symbol']} | {r['strategy']} | {r['exit_reason']} | "
                     f"{r['return_pct']:+.2f} | {r['r_multiple']:+.2f} | {_f(r['mfe_r'])} | {_f(r['mae_pct'])} | "
                     f"{_f(r['nifty_ret_pct'])} | {_f(r['post_exit_5d_pct'])} | {r['diagnosis']} |")
    else:
        L.append("None.")

    final = closed[closed["final_reviewed_on"].isna() & closed["post_exit_5d_pct"].notna()]
    L += ["", f"## Final verdicts, {POST_EXIT_BARS} sessions after exit ({len(final)})", "",
          "The post-exit move shows whether a stop was just noise (price came straight back).", ""]
    if len(final):
        L += ["| Symbol | Strategy | Exit | Ret% | +5d after% | Final diagnosis |", "|---|---|---|---:|---:|---|"]
        for _, r in final.iterrows():
            L.append(f"| {r['symbol']} | {r['strategy']} | {r['exit_reason']} | {r['return_pct']:+.2f} | "
                     f"{r['post_exit_5d_pct']:+.2f} | {r['diagnosis']} |")
    else:
        L.append("None.")

    L += ["", "## Forward vs backtest (per strategy)", "",
          f"Baseline = same rules on the Nifty 100 since {BASELINE_START}. Compared on EXCESS return vs Nifty "
          "over each trade, using only mature signal days (all calls of that day closed), one data point per day.", "",
          "| Strategy | Live? | Closed (mature) | Days | Fwd avg% | Fwd excess% | BT excess% | Fwd win% | BT win% "
          "| Fwd stop% | BT stop% | z | Verdict |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for s, g in calls[calls["status"] != "PENDING"].groupby("strategy"):
        b = baseline.get(s, {})
        d = drift(g, b)
        L.append(f"| {s} | {'yes' if s in live else 'log'} | {d['trades']} | {d['days']} | {_f(d['fwd_avg'])} | "
                 f"{_f(d['fwd_excess'])} | {_f(b.get('avg_excess'))} | {_f(d['fwd_win'], 0)} | {_f(b.get('win_rate'), 0)} | "
                 f"{_f(d['fwd_stop'], 0)} | {_f(b.get('stop_rate'), 0)} | {_f(d.get('z'))} | {d['verdict']} |")

    if len(closed):
        L += ["", "## Why trades ended (all closed calls)", ""]
        tab = closed.pivot_table(index="diagnosis", columns="strategy", values="id", aggfunc="count", fill_value=0)
        L += ["| Diagnosis | " + " | ".join(tab.columns) + " |", "|---|" + "---:|" * len(tab.columns)]
        for diag, row in tab.iterrows():
            L.append(f"| {diag} | " + " | ".join(str(v) for v in row) + " |")

    opn = calls[calls["status"] == "OPEN"]
    if len(opn):
        L += ["", f"## Open calls ({len(opn)})", "",
              f"Avg return {opn['return_pct'].mean():+.2f}% | Nifty since signal (avg) {_f(opn['nifty_ret_pct'].mean())}% | "
              f"in profit {int((opn['return_pct'] > 0).sum())}/{len(opn)}"]
        lv = opn[opn["strategy"].isin(live) & opn["rank"].notna()]
        if len(lv):
            L.append(f"Ranked calls of live strategies: {len(lv)}, avg return {lv['return_pct'].mean():+.2f}%, "
                 f"Nifty {_f(lv['nifty_ret_pct'].mean())}% (the other {len(opn) - len(lv)} are log-only research calls)")
        near = opn[(opn["mark_price"] - opn["current_stop"]) / opn["mark_price"] < 0.02]
        if len(near):
            L.append("Within 2% of stop: " + ", ".join(f"{r.symbol} ({r.strategy})" for r in near.itertuples()))

    L += ["", "## Feature splits (hypotheses for the lab, NOT rules)", ""]
    shown = False
    for s, g in closed.groupby("strategy"):
        if len(g) < MIN_TRADES_FOR_FEATURES:
            continue
        f = pd.json_normalize(g["features"].map(lambda x: json.loads(x) if isinstance(x, str) else {}))
        f.index = g.index
        rows = []
        for col in f.select_dtypes("number").columns:
            x = f[col]
            if x.notna().sum() < MIN_TRADES_FOR_FEATURES or x.nunique() < 4:
                continue
            hi = x > x.median()
            a, b_ = g.loc[hi, "return_pct"].mean(), g.loc[~hi & x.notna(), "return_pct"].mean()
            rows.append((abs(a - b_), col, x.median(), a, b_))
        if rows:
            shown = True
            L += [f"**{s}** ({len(g)} closed): avg return% above vs below the median of each feature", ""]
            for _, col, med, a, b_ in sorted(rows, reverse=True)[:5]:
                L.append(f"- {col} > {med:.2f}: {a:+.2f}%  vs  <= {med:.2f}: {b_:+.2f}%")
            L.append("")
    if not shown:
        L.append(f"Not enough closed calls yet (need {MIN_TRADES_FOR_FEATURES} per strategy).")
    L += ["", "_Rules: a strategy is only demoted when z < -2 over at least "
          f"{MIN_DAYS_FOR_VERDICT} signal days; any parameter idea from this page is tested in "
          "`lab.py` on train first, then holdouts. Never edit a live strategy because of one bad week._"]
    return "\n".join(L)


def _f(v, p: int = 2) -> str:
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:+.{p}f}" if p else f"{v:.0f}"
