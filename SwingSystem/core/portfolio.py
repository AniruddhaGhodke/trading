"""
Portfolio simulation: what the calls would have done to ONE account with limited capital,
taking only the top N new signals per day (the scanner's "take at most the top N" advice).

Trades come from core.backtest.backtest_strategy (the same simulator, fills and costs as the
live tracker), ranked per signal day with core.ranking.final_scores (the same ranking as the
scanner). The per-strategy backtest measures edge per trade; this measures the account:
equity curve, drawdown, exposure, and how often the N / capital limits bind.

Assumptions (all parameters in PortfolioConfig):
  - Selection at the signal day's close: candidates ordered by core.ranking.order_candidates
    (rank = scanner score or walk-forward strategy EV; tie_break = symbol or ATR%; remaining ties
    symbol A-Z or seeded random with tie_seed - check several seeds), one row per stock, stocks
    already held are skipped. select="top_n": the top N are ordered at full risk. Strict top N:
    an order later skipped (gap filter at the open, no cash) is NOT replaced by #N+1, because that
    is only known at the next open. select="budget": every eligible signal is ordered, each with
    risk = min(risk_per_trade, risk_budget / number of eligible signals that day). select="all":
    every eligible signal at full size, in rank order; cash at the open decides how many fill.
  - Sizing "risk" (as the scanner): shares = min(equity x risk_per_trade / (signal close - stop),
    equity x max_position_pct / signal close). Sizing "equal": equity x position_pct / signal
    close for every position (risk sizing gives low-volatility trades the biggest positions, and
    those did worst). Whole shares, equity = mark-to-market at the signal close (sizes compound). If cash is short at the open, buy what cash allows
    (>= 1 share), else skip.
  - Cash from exits at the OPEN (rule exits, gapped stops/targets) is usable by the same day's
    entries; intraday stop/target and time exits at the close only free cash afterwards.
  - Candidates come from backtest_strategy, so its rules carry over: one trade per symbol per
    strategy at a time, and no new signal on a trade's exit day. --include-research lets research
    strategies compete for slots (the scanner never ranks them). An order of < 1 share uses its slot.
  - Costs: config.COST_PER_SIDE on every buy and sell (same as the trade simulator).
    No interest on idle cash. Equity marks open positions at the close, before exit costs.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
from core.indicators import atr
from core.ranking import final_scores, order_candidates, walk_forward_ev

OPEN_EXITS = ("exit_rule", "stop_gap", "target_gap")   # filled at the bar's open


@dataclass
class PortfolioConfig:
    capital: float = config.CAPITAL
    top_n: int = config.MAX_NEW_POSITIONS
    risk_per_trade: float = config.RISK_PER_TRADE
    max_position_pct: float = config.MAX_POSITION_PCT
    cost: float = config.COST_PER_SIDE
    tie_seed: int | None = None    # remaining ties: None = symbol A-Z, int = seeded random order
    rank: str = "score"            # "score" (scanner's final_score) | "ev" (walk-forward strategy EV)
    tie_break: str = "symbol"      # "symbol" | "atr" (higher ATR% first among equal ranks)
    select: str = "top_n"          # "top_n" (strict top N/day) | "budget" (all signals, shared risk budget)
                                   # | "all" (every signal at full size, in rank order, until cash runs out)
    sizing: str = "risk"           # "risk" (scanner: risk_per_trade / stop distance) | "equal" (position_pct)
    position_pct: float = 0.10     # equal sizing: each position = this fraction of equity
    risk_budget: float | None = None   # budget mode: new risk per day; None = top_n x risk_per_trade


@dataclass
class PortfolioResult:
    equity: pd.DataFrame                       # date, cash, invested, equity, n_positions
    ledger: pd.DataFrame                       # every candidate with its action / fill / P&L
    cfg: PortfolioConfig = field(default_factory=PortfolioConfig)


def candidate_trades(strategies, prices: dict, ctx: pd.DataFrame, start: str | None = None) -> pd.DataFrame:
    """All strategies' backtest trades (one per symbol per strategy at a time, like the journal), ranked."""
    from core.backtest import backtest_strategy
    parts = [backtest_strategy(s, prices, ctx, start=start) for s in strategies]
    t = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else pd.DataFrame()
    if t.empty:
        return t
    meta = {s.name: s for s in strategies}
    t["group"] = t["strategy"].map(lambda n: meta[n].group or n)
    t["research"] = t["strategy"].map(lambda n: meta[n].research_only)
    t["final_score"] = final_scores(t, by=["signal_date"])
    feats = pd.concat([pd.DataFrame({"symbol": s, "signal_date": df.index,
                                     "atr_pct": (atr(df) / df["Close"] * 100).to_numpy()})
                       for s, df in prices.items() if s in set(t["symbol"])])
    t = t.merge(feats, on=["symbol", "signal_date"], how="left")
    t["n_day"] = t.groupby("signal_date")["symbol"].transform("nunique")   # stocks signalling that day
    t["ev"] = walk_forward_ev(t)   # on the FULL history, so a later slice still knows earlier outcomes
    return t[t["status"] != "PENDING"].reset_index(drop=True)


def simulate_portfolio(trades: pd.DataFrame, prices: dict, cfg: PortfolioConfig | None = None) -> PortfolioResult:
    """
    trades: output of candidate_trades (needs symbol, strategy, signal_date, status, ref_close,
    initial_stop, final_score, and entry/exit fields). prices: {symbol: daily OHLCV} for marking.
    """
    cfg = cfg or PortfolioConfig()
    if trades.empty:
        return PortfolioResult(pd.DataFrame(), pd.DataFrame(), cfg)
    syms = sorted(trades["symbol"].unique())
    first = trades["signal_date"].min()
    closes = pd.concat({s: prices[s]["Close"] for s in syms}, axis=1)
    closes = closes[closes.index >= first].ffill()
    dates = closes.index

    ordered = order_candidates(trades, cfg.rank, cfg.tie_break, cfg.tie_seed)
    by_signal = {d: g for d, g in ordered.groupby("signal_date", sort=False)}
    budget = cfg.risk_budget if cfg.risk_budget is not None else cfg.top_n * cfg.risk_per_trade
    c = cfg.cost
    cash = cfg.capital
    positions = {}          # ledger row id -> dict(symbol, qty, exit_date, exit_price, exit_reason)
    orders = {}             # entry_date -> list of (row id, qty)
    ledger = []             # dicts, one per candidate considered
    eq_rows = []

    def close_position(pid):
        nonlocal cash
        p = positions.pop(pid)
        proceeds = p["qty"] * p["exit_price"] * (1 - c)
        cash += proceeds
        ledger[pid]["pnl"] = proceeds - ledger[pid]["cost_basis"]
        ledger[pid]["pnl_pct"] = (proceeds / ledger[pid]["cost_basis"] - 1) * 100

    for d in dates:
        # 1) exits filled at the open
        for pid in [k for k, p in positions.items() if p["exit_date"] == d and p["exit_reason"] in OPEN_EXITS]:
            close_position(pid)
        # 2) entries ordered at yesterday's close
        for pid, qty in orders.pop(d, []):
            row = ledger[pid]
            if row["status"] == "SKIPPED":
                row["action"] = f"skipped at open ({row['exit_reason']})"
                continue
            px = row["entry_price"]
            qty = min(qty, int(cash // (px * (1 + c))))
            if qty < 1:
                row["action"] = "skipped: no cash"
                continue
            row.update(action="taken", qty=qty, cost_basis=qty * px * (1 + c))
            cash -= row["cost_basis"]
            positions[pid] = {"symbol": row["symbol"], "qty": qty, "exit_date": row["exit_date"],
                              "exit_price": row["exit_price"], "exit_reason": row["exit_reason"]}
        # 3) intraday / close exits (incl. a stop hit on the entry day)
        for pid in [k for k, p in positions.items() if p["exit_date"] == d]:
            close_position(pid)
        # 4) mark to market
        invested = sum(p["qty"] * closes.at[d, p["symbol"]] for p in positions.values())
        equity = cash + invested
        eq_rows.append({"date": d, "cash": cash, "invested": invested, "equity": equity,
                        "n_positions": len(positions)})
        # 5) today's signals -> orders for the next open
        cands = by_signal.get(d)
        if cands is None:
            continue
        held = {p["symbol"] for p in positions.values()}
        taken_syms, eligible = set(), []
        for row in cands.to_dict("records"):
            pid = len(ledger)
            ledger.append(row)
            if row["symbol"] in taken_syms:
                row["action"] = "dup: same stock, lower score"
                continue
            taken_syms.add(row["symbol"])
            if row["symbol"] in held:
                row["action"] = "skipped: already held"
                continue
            if pd.isna(row.get("entry_date")):
                row["action"] = "no next bar"
                continue
            eligible.append((pid, row))
        scale = 1.0                  # "all": every eligible signal at full size (cash decides at the open)
        if cfg.select == "budget":   # every signal, the day's risk budget shared equally
            scale = min(1.0, budget / (len(eligible) * cfg.risk_per_trade)) if eligible else 0.0
        elif cfg.select == "top_n":  # strict top N at full size
            for _, row in eligible[cfg.top_n:]:
                row["action"] = f"skipped: beyond top {cfg.top_n}"
            eligible = eligible[:cfg.top_n]
        for pid, row in eligible:
            if cfg.sizing == "equal":
                value = equity * min(cfg.position_pct, cfg.max_position_pct) * scale
            else:
                per_share = row["ref_close"] - row["initial_stop"]
                value = min(equity * cfg.risk_per_trade * scale / per_share * row["ref_close"] if per_share > 0 else 0,
                            equity * cfg.max_position_pct)
            qty = int(value / row["ref_close"])
            if qty < 1:
                row["action"] = "skipped: size < 1 share"
                continue
            row["action"] = "ordered"
            orders.setdefault(row["entry_date"], []).append((pid, qty))

    for p in ledger:   # still open at the end of the data
        if p.get("action") == "taken" and pd.isna(p.get("pnl")):
            last = closes[p["symbol"]].iloc[-1]
            p["pnl"] = p["qty"] * last - p["cost_basis"]
            p["pnl_pct"] = (p["qty"] * last / p["cost_basis"] - 1) * 100
            p["action"] = "taken (open)"
    return PortfolioResult(pd.DataFrame(eq_rows).set_index("date"), pd.DataFrame(ledger), cfg)


def drawdown(equity: pd.Series) -> pd.Series:
    """Drawdown from the running peak, as a fraction (0 = at a new high, -0.2 = 20% below it)."""
    return equity / equity.cummax() - 1


def summary(res: PortfolioResult, index_close: pd.Series | None = None) -> dict:
    eq = res.equity["equity"]
    if eq.empty:
        return {}
    dd = drawdown(eq)
    trough = dd.idxmin()
    peak = eq.loc[:trough].idxmax()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    daily = eq.pct_change().dropna()
    led = res.ledger
    taken = led[led["action"].astype(str).str.startswith("taken")] if len(led) else led
    # longest time under water
    under = (dd < 0).astype(int)
    runs = under.groupby((under == 0).cumsum()).sum()
    out = {
        "start": eq.index[0].date(), "end": eq.index[-1].date(), "years": years,
        "capital": res.cfg.capital, "final": eq.iloc[-1],
        "total_ret%": (eq.iloc[-1] / res.cfg.capital - 1) * 100,
        "cagr%": ((eq.iloc[-1] / res.cfg.capital) ** (1 / years) - 1) * 100,
        "max_dd%": dd.min() * 100, "dd_peak": peak.date(), "dd_trough": trough.date(),
        "longest_underwater_days": int(runs.max()) if len(runs) else 0,
        "sharpe": daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0.0,
        "trades": len(taken),
        "win%": (taken["pnl"] > 0).mean() * 100 if len(taken) else np.nan,
        "avg_trade%": taken["pnl_pct"].mean() if len(taken) else np.nan,
        "avg_positions": res.equity["n_positions"].mean(),
        "avg_exposure%": (res.equity["invested"] / res.equity["equity"]).mean() * 100,
        "actions": led["action"].astype(str).str.replace(r"\(.*\)", "", regex=True).str.strip()
                   .value_counts().to_dict() if len(led) else {},
    }
    if index_close is not None:
        ix = index_close.reindex(eq.index).ffill().dropna()
        if len(ix) > 1:
            out["nifty_cagr%"] = ((ix.iloc[-1] / ix.iloc[0]) ** (1 / years) - 1) * 100
            out["nifty_max_dd%"] = drawdown(ix).min() * 100
    return out


def yearly(res: PortfolioResult) -> pd.DataFrame:
    eq = res.equity["equity"]
    start_eq = pd.concat([pd.Series([res.cfg.capital], index=[eq.index[0] - pd.Timedelta(days=1)]), eq])
    rows = []
    for y, g in eq.groupby(eq.index.year):
        prev = start_eq[start_eq.index < g.index[0]].iloc[-1]
        path = pd.concat([pd.Series([prev]), g])
        led = res.ledger
        n = (led["action"].astype(str).str.startswith("taken") & (pd.to_datetime(led["entry_date"]).dt.year == y)).sum()
        rows.append({"year": y, "end_equity": g.iloc[-1], "ret%": (g.iloc[-1] / prev - 1) * 100,
                     "max_dd%": drawdown(path).min() * 100, "trades": int(n),
                     "avg_positions": res.equity.loc[g.index, "n_positions"].mean()})
    return pd.DataFrame(rows)


def print_report(res: PortfolioResult, s: dict, names: list[str]):
    cfg = res.cfg
    print(f"\n{'=' * 78}\nPORTFOLIO: {', '.join(names)}\n{'=' * 78}")
    print(f"Capital Rs {cfg.capital:,.0f} | top {cfg.top_n} new signals/day | risk {cfg.risk_per_trade * 100:.1f}%/trade, "
          f"max {cfg.max_position_pct * 100:.0f}%/position | cost {cfg.cost * 100:.2f}%/side")
    if not s:
        print("No trades.")
        return
    print(f"{s['start']} .. {s['end']} ({s['years']:.1f} y): Rs {s['capital']:,.0f} -> Rs {s['final']:,.0f} "
          f"({s['total_ret%']:+.1f}%) | CAGR {s['cagr%']:+.2f}% | max drawdown {s['max_dd%']:.1f}% "
          f"({s['dd_peak']} -> {s['dd_trough']}) | longest under water {s['longest_underwater_days']} sessions | "
          f"Sharpe {s['sharpe']:.2f}")
    if "nifty_cagr%" in s:
        print(f"Nifty 50 buy & hold, same period: CAGR {s['nifty_cagr%']:+.2f}% | max drawdown {s['nifty_max_dd%']:.1f}%")
    print(f"Trades {s['trades']} | win {s['win%']:.1f}% | avg {s['avg_trade%']:+.2f}%/trade | "
          f"avg {s['avg_positions']:.1f} positions, {s['avg_exposure%']:.0f}% of equity invested")
    print("Candidates: " + ", ".join(f"{k} {v}" for k, v in s["actions"].items()))
    print(f"\n{'Year':<6} {'End equity':>12} {'Return':>8} {'MaxDD':>7} {'Trades':>7} {'AvgPos':>7}")
    for _, r in yearly(res).iterrows():
        print(f"{int(r['year']):<6} {r['end_equity']:>12,.0f} {r['ret%']:>+7.1f}% {r['max_dd%']:>6.1f}% "
              f"{int(r['trades']):>7} {r['avg_positions']:>7.1f}")
    print("\nNOTE: today's Nifty 100 list for all years => survivorship bias flatters early years.")
