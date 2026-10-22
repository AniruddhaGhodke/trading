"""
Swing Signal System - console entry point.

  python main.py scan                 # today's ranked calls (logged to journal)
  python main.py track                # update outcomes of logged calls + scorecard
  python main.py review               # why calls worked/failed + forward vs backtest drift
  python main.py daily                # track, review, then scan (run once after market close)
  python main.py backtest             # all strategies, full history
  python main.py backtest --strategy trend_pullback --start 2020-01-01
  python main.py portfolio            # live strategies as ONE account: top 3/day, equity curve, drawdown
  python main.py portfolio --capital 1000000 --top-n 5 --start 2020-01-01
  python main.py calls                # list journal calls
Add --refresh to force re-download of price data.
"""

import argparse
import json
import sys
import warnings

import numpy as np
import pandas as pd

import config
from core import data, journal, portfolio, review
from core.backtest import backtest_strategy, print_report
from core.execution import simulate_trade
from core.ranking import final_scores
from core.regime import align_context, market_context
from core.stats import format_row, header, summarize
from strategies import ALL_STRATEGIES, REGISTRY

warnings.filterwarnings("ignore")
pd.set_option("display.width", 200)

# Regime is handled inside strategies now (lab: dip strategies do best in BEAR, worst in NEUTRAL),
# so the ranker no longer adjusts for it.
REGIME_ADJ = {"BULL": 0, "NEUTRAL": 0, "BEAR": 0}


def load_all(refresh: bool):
    prices = data.load_prices(data.load_universe(), refresh=refresh)
    ctx = market_context(data.load_index(refresh=refresh))
    return prices, ctx


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------
def cmd_scan(args):
    prices, ctx = load_all(args.refresh)
    asof = max(df.index[-1] for df in prices.values())
    regime = ctx["regime"].iloc[-1]

    sectors = data.load_sectors()
    cands = []
    for symbol, raw in prices.items():
        if len(raw) < 260 or raw.index[-1] != asof:
            continue
        actx = align_context(ctx, raw.index)
        for strat in ALL_STRATEGIES:
            df = strat.prepare(raw, actx)
            last = df.iloc[-1]
            if bool(last["signal"]):
                cands.append({"symbol": symbol, "strategy": strat.name, "score": float(last["score"]),
                              "ref_close": float(last["Close"]), "stop": float(last["stop"]),
                              "reason": last["reason"], "regime": regime,
                              "signal_date": asof.strftime("%Y-%m-%d"),
                              "strategy_version": review.strategy_version(strat),
                              "features": review.signal_features(raw, len(raw) - 1, actx, sectors.get(symbol))})

    print(f"\nScan as of {asof.date()} close | Nifty regime: {regime} | universe: {len(prices)} stocks")
    if not cands:
        print("No setups today.")
        return

    c = pd.DataFrame(cands)
    c["research"] = c["strategy"].map(lambda s: REGISTRY[s].research_only)
    c["group"] = c["strategy"].map(lambda s: REGISTRY[s].group or s)
    # Confluence = number of DIFFERENT ideas (groups) agreeing on a symbol, not variants of one idea
    c["final_score"] = final_scores(c) + REGIME_ADJ.get(regime, 0)
    c = c.sort_values(["research", "final_score"], ascending=[True, False]).reset_index(drop=True)
    con = journal.connect()
    # Re-signal on a position that is already pending/open from an earlier day: no new call, never ranked
    # (otherwise held positions crowd new calls out of the top N)
    c["active"] = [journal.has_active(con, s, st, before=c["signal_date"].iloc[0])
                   for s, st in zip(c["symbol"], c["strategy"])]
    # Unranked (logged only): research-only strategies, and extra variants of the same idea on one symbol
    # (a variant of an idea already HELD on that symbol counts too, e.g. crsi5_t5 while crsi10_nn is open)
    live = ~c["research"] & ~c["active"]
    held = journal.active_calls(con)
    held = held[(held["signal_date"] < c["signal_date"].iloc[0]) & held["strategy"].isin(REGISTRY)]
    held_ideas = {(s, REGISTRY[st].group or st) for s, st in zip(held["symbol"], held["strategy"])
                  if not REGISTRY[st].research_only}
    c["variant"] = live & (c[live].duplicated(["symbol", "group"]).reindex(c.index, fill_value=False)
                           | pd.Series([k in held_ideas for k in zip(c["symbol"], c["group"])], index=c.index))
    unranked = ~live | c["variant"]
    c = pd.concat([c[~unranked], c[unranked]], ignore_index=True)
    c["rank"] = [i + 1 if not u else None for i, u in enumerate(c["research"] | c["active"] | c["variant"])]
    c["risk_pct"] = (c["ref_close"] - c["stop"]) / c["ref_close"] * 100
    # How crowded the day was (dip signals cluster in selloffs) - part of the signal-time snapshot
    n_day = c.groupby("strategy")["symbol"].transform("nunique")
    c["features"] = [json.dumps({**f, "n_signals_strategy": int(n), "n_signals_all": int(c["symbol"].nunique())})
                     for f, n in zip(c["features"], n_day)]

    status = []
    for _, row in c.iterrows():
        if row["active"]:
            status.append("already active")
        else:
            status.append("logged" if journal.insert_call(con, row.to_dict()) else "dup")
    c["journal"] = status

    ranked = c[c["rank"].notna()]
    # One row per stock: best-scoring strategy shown, the others that agree listed in the reason
    also = ranked.groupby("symbol")["strategy"].apply(list)
    ranked = ranked.drop_duplicates("symbol").copy()
    ranked["reason"] = [r + ("" if len(also[sym]) == 1 else f" | also: {', '.join(also[sym][1:])}")
                        for sym, r in zip(ranked["symbol"], ranked["reason"])]
    held_by = {s: [st for s2, st in zip(held["symbol"], held["strategy"]) if s2 == s and not REGISTRY[st].research_only]
               for s in ranked["symbol"]}
    ranked["reason"] = [r + (f" | HOLDING via {', '.join(held_by[s])}" if held_by[s] else "")
                        for s, r in zip(ranked["symbol"], ranked["reason"])]
    top = ranked.head(config.TOP_N_CALLS)
    print(f"{len(ranked)} stocks with setups, top {len(top)} shown. Entry = next open (skip if it gaps > 1 ATR up).")
    print(f"({int(c['research'].sum())} research-only + {int(c['variant'].sum())} same-idea variant signals "
          f"logged to journal, not shown)")
    held = c[c["active"] & ~c["research"]]
    if len(held):
        print(f"Signal again on {len(held)} live position(s) already held (no new call): "
              + ", ".join(f"{r.symbol} ({r.strategy})" for r in held.itertuples()))
    print()
    if top.empty:
        con.close()
        return
    risk_rs = config.CAPITAL * config.RISK_PER_TRADE
    print(f"{'#':>2} {'Symbol':<14} {'Strategy':<18} {'Score':>5} {'Close':>9} {'Stop':>9} {'Risk%':>6} "
          f"{'Qty':>6} {'Value':>9}  Reason")
    print("-" * 125)
    for n, (_, r) in enumerate(top.iterrows(), 1):
        plan = REGISTRY[r["strategy"]].exit_plan
        tgt = (f" | tgt {plan.target_r:.0f}R" if plan.target_r else
               f" | tgt +{plan.target_pct * 100:.0f}%" if plan.target_pct else "") + f" | max {plan.max_hold}d"
        per_share = r["ref_close"] - r["stop"]
        qty = int(min(risk_rs / per_share, config.CAPITAL * config.MAX_POSITION_PCT / r["ref_close"])) if per_share > 0 else 0
        print(f"{n:>2} {r['symbol']:<14} {r['strategy']:<18} {r['final_score']:>5.0f} "
              f"{r['ref_close']:>9.2f} {r['stop']:>9.2f} {r['risk_pct']:>5.1f}% {qty:>6} {qty * r['ref_close']:>9.0f}  "
              f"{r['reason']}{tgt}" + ("" if r["journal"] == "logged" else f"  [{r['journal']}]"))
    n_new = int(top["journal"].isin(["logged", "dup"]).sum())
    print(f"\nSizing: capital Rs {config.CAPITAL:,.0f}, risk {config.RISK_PER_TRADE * 100:.1f}% (Rs {risk_rs:,.0f}) "
          f"per trade, max {config.MAX_POSITION_PCT * 100:.0f}% of capital per position (config.py).")
    if n_new > config.MAX_NEW_POSITIONS:
        print(f"WARNING: {n_new} new calls today. Dip signals fire together in selloffs = one correlated bet. "
              f"Take at most the top {config.MAX_NEW_POSITIONS}.")
    con.close()


# ---------------------------------------------------------------------------
# track
# ---------------------------------------------------------------------------
def cmd_track(args):
    con = journal.connect()
    active = journal.active_calls(con)
    todo = journal.calls_to_enrich(con)
    if len(active) or len(todo):
        prices, ctx = load_all(args.refresh)
        # stocks dropped from the Nifty 100 list at a rebalance still need their calls tracked
        gone = sorted((set(active["symbol"]) | set(todo["symbol"])) - set(prices))
        if gone:
            prices.update(data.load_prices(gone, refresh=args.refresh))
        prepared = {}

        def prep(call):
            key = (call["symbol"], call["strategy"])
            if key not in prepared:
                raw, strat = prices.get(call["symbol"]), REGISTRY.get(call["strategy"])
                prepared[key] = None if raw is None or strat is None else                     (strat, raw, strat.prepare(raw, align_context(ctx, raw.index)))
            return prepared[key]

        for _, call in active.iterrows():
            p = prep(call)
            if p is None:
                continue
            strat, _, df = p
            sd = pd.Timestamp(call["signal_date"])
            if sd not in df.index:
                continue
            i = df.index.get_loc(sd)
            # Use the stop as logged, rescaled for any dividend/split adjustment yfinance
            # has applied to past prices since the call was made.
            adj = float(df["Close"].iloc[i]) / call["ref_close"]
            df = df.copy()
            df.iloc[i, df.columns.get_loc("stop")] = call["stop"] * adj
            journal.update_outcome(con, int(call["id"]), simulate_trade(df, i, strat.exit_plan))
        enrich(con, prep, ctx)
    print_scorecard(con)
    con.close()


def enrich(con, prep, ctx):
    """Fill review fields (signal-time features for old calls, MFE/MAE, Nifty, post-exit, diagnosis)."""
    sectors = data.load_sectors()
    for _, call in journal.calls_to_enrich(con).iterrows():
        p = prep(call)
        if p is None:
            continue
        strat, raw, df = p
        fields = {}
        sd = pd.Timestamp(call["signal_date"])
        if pd.isna(call["features"]) and sd in raw.index:
            actx = align_context(ctx, raw.index)
            fields["features"] = json.dumps(review.signal_features(raw, raw.index.get_loc(sd), actx,
                                                                   sectors.get(call["symbol"])))
            fields["strategy_version"] = review.strategy_version(strat)
        if call["status"] in ("OPEN", "CLOSED"):
            fields.update(review.excursions(df, ctx, call))
        journal.set_fields(con, int(call["id"]), fields)


def print_scorecard(con):
    calls = journal.all_calls(con)
    print(f"\n{'=' * 78}\nLIVE SCORECARD (forward-tested calls from the journal)\n{'=' * 78}")
    if calls.empty:
        print("Journal is empty - run `python main.py scan` after market close.")
        return
    counts = calls["status"].value_counts().to_dict()
    print("Calls: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    closed = calls[calls["status"] == "CLOSED"]
    print("\n" + header("Closed, by strategy"))
    for s, g in closed.groupby("strategy"):
        print(format_row(s, summarize(g)))
    if len(closed):
        print(format_row("ALL", summarize(closed)))
        top = closed[closed["rank"] <= config.TOP_N_CALLS]
        print(format_row(f"Top-{config.TOP_N_CALLS} ranked only", summarize(top)))

    opn = calls[calls["status"] == "OPEN"]
    if len(opn):
        print(f"\nOPEN positions ({len(opn)}):")
        print(f"{'Symbol':<14} {'Strategy':<18} {'Entry':>9} {'Last':>9} {'Stop':>9} {'Ret%':>7} {'R':>6} {'Days':>5}")
        for _, r in opn.sort_values("return_pct", ascending=False).iterrows():
            due = r["exit_reason"] if isinstance(r["exit_reason"], str) and r["exit_reason"].startswith("DUE") else ""
            print(f"{r['symbol']:<14} {r['strategy']:<18} {r['entry_price']:>9.2f} {r['mark_price']:>9.2f} "
                  f"{r['current_stop']:>9.2f} {r['return_pct']:>+6.2f}% {r['r_multiple']:>+6.2f} {r['bars_held']:>5}  {due}")
        due = opn[opn["exit_reason"].fillna("").str.startswith("DUE")]
        if len(due):
            print(f"EXIT NEXT SESSION ({len(due)}): " + ", ".join(
                f"{r.symbol} ({r.strategy}, {r.exit_reason[5:]})" for r in due.itertuples()))
    pend = calls[calls["status"] == "PENDING"]
    if len(pend):
        print(f"\nPENDING entry next session: {', '.join(pend['symbol'] + ' (' + pend['strategy'] + ')')}")


# ---------------------------------------------------------------------------
# backtest / calls
# ---------------------------------------------------------------------------
def cmd_backtest(args):
    prices, ctx = load_all(args.refresh)
    strats = [REGISTRY[args.strategy]] if args.strategy else ALL_STRATEGIES
    print(f"Universe: {len(prices)} stocks | cost {config.COST_PER_SIDE * 100:.2f}%/side | "
          f"entry next open | NOTE: today's Nifty 100 list => survivorship bias in early years")
    for s in strats:
        trades = backtest_strategy(s, prices, ctx, start=args.start)
        if args.save:
            out = config.DATA_DIR / f"backtest_{s.name}.csv"
            trades.to_csv(out, index=False)
            print(f"Saved {out}")
        holds = trades.loc[trades["status"] == "CLOSED", "bars_held"].to_numpy() if len(trades) else np.array([])
        if len(holds) == 0:   # nothing closed yet (e.g. --start a few days ago): no holds to match
            print_report(trades, f"{s.name}: {s.description}")
            continue
        control = pd.concat([backtest_strategy(s, prices, ctx, start=args.start, random_seed=seed,
                                               control_holds=holds)
                             for seed in (1, 2, 3)], ignore_index=True)
        print_report(trades, f"{s.name}: {s.description}", control, args.min_edge)


def cmd_portfolio(args):
    prices, ctx = load_all(args.refresh)
    if args.strategies:
        strats = [REGISTRY[n] for n in args.strategies.split(",")]
    else:
        strats = [s for s in ALL_STRATEGIES if args.include_research or not s.research_only]
    cfg = portfolio.PortfolioConfig(capital=args.capital, top_n=args.top_n, tie_seed=args.tie_seed,
                                    rank=args.rank, tie_break=args.tie_break, select=args.select,
                                    risk_budget=args.risk_budget, sizing=args.sizing,
                                    position_pct=args.position_pct)
    print(f"Backtesting {len(strats)} strategies for candidates...", flush=True)
    trades = portfolio.candidate_trades(strats, prices, ctx, start=args.start)
    res = portfolio.simulate_portfolio(trades, prices, cfg)
    s = portfolio.summary(res, ctx["idx_close"])
    portfolio.print_report(res, s, [st.name for st in strats])
    if len(res.equity):
        out = config.DATA_DIR / "portfolio"
        out.mkdir(parents=True, exist_ok=True)
        tag = f"top{cfg.top_n}_{int(cfg.capital)}"
        eq = res.equity.assign(drawdown_pct=portfolio.drawdown(res.equity["equity"]) * 100)
        eq.to_csv(out / f"equity_{tag}.csv")
        res.ledger.to_csv(out / f"ledger_{tag}.csv", index=False)
        print(f"Saved equity curve + drawdown to {out / f'equity_{tag}.csv'} (ledger alongside)")


def cmd_review(args):
    from strategies import ALL_STRATEGIES
    base = review.load_baseline()
    stale = [st for st in ALL_STRATEGIES if base.get(st.name, {}).get("version") != review.strategy_version(st)
             or "avg_excess" not in base.get(st.name, {})]
    if stale or args.baseline:
        prices, ctx = load_all(args.refresh)
        base = review.build_baseline(prices, ctx, ALL_STRATEGIES if args.baseline else stale,
                                     None if args.baseline else base)
    con = journal.connect()
    calls = journal.all_calls(con)
    asof = pd.Timestamp.now().strftime("%Y-%m-%d")
    live = {n for n, st in REGISTRY.items() if not st.research_only}
    text = review.report(calls, base, live, asof)
    review.REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = review.REVIEW_DIR / f"{asof}.md"
    out.write_text(text, encoding="utf-8")
    print(f"\n{'=' * 78}\n{text}\n\nSaved {out}")
    closed = calls[(calls["status"] == "CLOSED") & calls["diagnosis"].notna()]
    journal.mark_reviewed(con, closed.loc[closed["reviewed_on"].isna(), "id"], asof)
    journal.mark_reviewed(con, closed.loc[closed["final_reviewed_on"].isna() & closed["post_exit_5d_pct"].notna(), "id"],
                          asof, "final_reviewed_on")
    con.close()


def cmd_calls(args):
    con = journal.connect()
    calls = journal.all_calls(con)
    cols = ["signal_date", "rank", "symbol", "strategy", "final_score", "status", "entry_price",
            "exit_price", "exit_reason", "return_pct", "r_multiple"]
    print(calls[cols].tail(args.n).to_string(index=False) if len(calls) else "Journal is empty.")
    con.close()


def main():
    p = argparse.ArgumentParser(description="Swing signal system")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "track", "review", "daily", "backtest", "portfolio", "calls"):
        sp = sub.add_parser(name)
        sp.add_argument("--refresh", action="store_true", help="force re-download of prices")
        if name == "backtest":
            sp.add_argument("--strategy", choices=list(REGISTRY))
            sp.add_argument("--start", help="only signals on/after this date, e.g. 2020-01-01")
            sp.add_argument("--save", action="store_true", help="save trades CSV to data/")
            sp.add_argument("--min-edge", type=float, default=config.MIN_EDGE_PER_TRADE,
                            help="per-trade edge floor over control, %% (default %(default)s)")
        if name == "portfolio":
            sp.add_argument("--capital", type=float, default=config.CAPITAL, help="starting capital, Rs")
            sp.add_argument("--top-n", type=int, default=config.MAX_NEW_POSITIONS, help="max new positions per day")
            sp.add_argument("--start", help="only signals on/after this date")
            sp.add_argument("--strategies", help="comma-separated (default: all live, non-research strategies)")
            sp.add_argument("--include-research", action="store_true", help="also use research-only strategies")
            sp.add_argument("--tie-seed", type=int, help="random order for equal scores (default: symbol A-Z)")
            sp.add_argument("--rank", choices=["score", "ev"], default="score",
                            help="score = scanner score (default); ev = walk-forward strategy expected return")
            sp.add_argument("--tie-break", choices=["symbol", "atr"], default="symbol",
                            help="atr = higher ATR%% first among equal ranks")
            sp.add_argument("--select", choices=["top_n", "budget", "all"], default="top_n",
                            help="budget = every signal, sharing a daily risk budget; all = every signal "
                                 "at full size until cash runs out")
            sp.add_argument("--sizing", choices=["risk", "equal"], default="risk",
                            help="risk = scanner sizing (1%% risk to the stop); equal = fixed %% of equity")
            sp.add_argument("--position-pct", type=float, default=0.10,
                            help="equal sizing: position size as a fraction of equity (default 0.10)")
            sp.add_argument("--risk-budget", type=float,
                            help="budget mode: new risk per day as a fraction (default top-n x RISK_PER_TRADE)")
        if name in ("review", "daily"):
            sp.add_argument("--baseline", action="store_true", help="rebuild all backtest baselines")
        if name == "calls":
            sp.add_argument("-n", type=int, default=50)
    args = p.parse_args()

    if args.cmd == "daily":
        if args.refresh:
            load_all(refresh=True)   # refresh once up front; track and scan then reuse the cache
            args.refresh = False
        cmd_track(args)
        cmd_review(args)
        cmd_scan(args)
        try:   # grow the 5-minute history (yfinance only serves ~60 days) for future intraday research
            from core import intraday_data
            intraday_data.collect(("5m", "1h"))
        except Exception as e:
            print(f"(intraday collection skipped: {e})")
        if (config.ROOT / ".env").exists() and (config.DATA_DIR / "angel" / "5m").exists():
            try:   # top up Angel One 5-minute history (resumable; ~1 request per symbol per day)
                from core import angel_data
                angel_data.backfill("5m", quiet=True)
            except BaseException as e:   # never let a broker hiccup break the daily scan
                print(f"(Angel One update skipped: {e})")
            try:   # Nifty options: Angel One drops a contract's history once it expires - record it daily
                from core import angel_data, angel_options
                angel_options.record(quiet=True)
                angel_data.backfill("1m", symbols=["^NSEI", "^INDIAVIX"], quiet=True)
            except BaseException as e:
                print(f"(Nifty option recording skipped: {e})")
    else:
        {"scan": cmd_scan, "track": cmd_track, "review": cmd_review, "backtest": cmd_backtest,
         "portfolio": cmd_portfolio, "calls": cmd_calls}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
