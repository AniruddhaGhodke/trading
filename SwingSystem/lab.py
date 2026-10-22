"""
Strategy research lab - fast sweeps on a frozen snapshot, with holdouts.

  python lab.py freeze                    # snapshot prices into data/lab (do once; re-freeze rarely)
  python lab.py families                  # list entry/exit families and their grids
  python lab.py sweep                     # stage 1 (entries) -> stage 2 (exits) -> finalists + holdouts
  python lab.py sweep --entries rsi_dip,bb_lower --top-entries 10
  python lab.py test "rsi_dip(n=2,level=10,trend=sma200)" "fixed_pct(target=5,stop=5,hold=10)"
  python lab.py test "<entry>" "<exit>" --holdout      # also reveal holdouts (logged!)

Splits (config.py): train = 10 LAB_STOCKS 2016-2023 | A = same stocks 2024+ |
B = other 90 stocks 2016+ | B24 = other 90 stocks 2024+.
Every holdout look is logged; once you iterate against a holdout it is no longer out-of-sample.
"""

import argparse
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import pandas as pd

import config
from lab import data as lab_data
from lab.families import ENTRIES, EXITS, ENTRY_BY_NAME, EXIT_BY_NAME, expand, neighbours, parse_spec, spec

warnings.filterwarnings("ignore")
HOLDOUT_LOG = config.LAB_DIR / "holdout_log.csv"
HOLDOUTS = ["A", "B", "B24"]


# ---------------------------------------------------------------------------
# parallel evaluation
# ---------------------------------------------------------------------------
def _eval_job(job):
    warnings.filterwarnings("ignore")
    from lab.engine import evaluate
    entry, exit_, ds = job
    try:
        return {"entry": spec(*entry), "exit": spec(*exit_), "ds": ds, **evaluate(entry, exit_, ds)}
    except Exception as e:  # keep the sweep alive; surface the error in results
        return {"entry": spec(*entry), "exit": spec(*exit_), "ds": ds, "trades": 0, "error": str(e)[:80]}


def run_jobs(jobs, n_jobs, label):
    print(f"  {label}: {len(jobs)} runs...", end="", flush=True)
    t0 = datetime.now()
    if n_jobs > 1:
        with ProcessPoolExecutor(n_jobs) as ex:
            res = list(ex.map(_eval_job, jobs, chunksize=8))
    else:
        res = [_eval_job(j) for j in jobs]
    print(f" done in {(datetime.now() - t0).seconds}s")
    return pd.DataFrame(res)


def log_holdout_view(entries: list[str], exits: list[str], source: str):
    rows = pd.DataFrame({"when": datetime.now().isoformat(timespec="seconds"), "source": source,
                         "entry": entries, "exit": exits})
    rows.to_csv(HOLDOUT_LOG, mode="a", header=not HOLDOUT_LOG.exists(), index=False)
    n = len(pd.read_csv(HOLDOUT_LOG))
    print(f"\n[holdout views logged so far: {n}. The more you tune after looking, the less these holdouts mean.]")


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
COLS = ["trades", "trades/stock/yr", "win%", "avg%", "hold", "pf", "edge%", "edge_trade%", "t", "beat%"]


def fmt(df: pd.DataFrame, cols=COLS) -> str:
    show = df.copy()
    for c in cols:
        if c in show:
            show[c] = show[c].map(lambda v: "" if pd.isna(v) else (f"{v:.0f}" if c in ("trades", "beat%")
                                  else f"{v:+.2f}" if c in ("avg%", "edge%", "edge_trade%") else f"{v:.2f}"))
    return show.to_string(index=False)


def good(r) -> bool:
    return (r.get("trades", 0) >= config.MIN_TRAIN_TRADES and r.get("edge%", 0) > 0
            and r.get("edge_trade%", 0) > 0)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_sweep(args):
    m = lab_data.manifest()
    fams = [ENTRY_BY_NAME[n] for n in args.entries.split(",")] if args.entries else ENTRIES
    print(f"Snapshot to {m['last_bar']} | train: {len(config.LAB_STOCKS)} stocks "
          f"{config.TRAIN_START}..{config.TRAIN_END} | jobs={args.jobs}")

    # Stage 1: screen entries vs random entries with neutral exits: time exits from 3 to 250 days
    # (short-term to long-term) plus two trailing-MA exits so trend/breakout entries get a fair test
    screen_exits = [("time", x) for x in expand(EXIT_BY_NAME["time"])] + [
        ("rule", {"rule": "close_lt_sma20", "stop_atr": None, "hold": 60}),
        ("rule", {"rule": "close_lt_sma50", "stop_atr": None, "hold": 120})]
    jobs = [((f.name, p), x, "train") for f in fams for p in expand(f) for x in screen_exits]
    s1 = run_jobs(jobs, args.jobs, "Stage 1 - entry screen")
    s1["ok"] = s1.apply(good, axis=1)
    s1["family"] = s1["entry"].str.split("(").str[0]
    best = (s1[s1["ok"]].sort_values("t", ascending=False).drop_duplicates("entry"))
    print_family_summary(s1, fams)
    top_entries = best.groupby("family").head(args.per_family).head(args.top_entries)
    print(f"\nTop entries (train, best screening exit; max {args.per_family} per family), "
          f"{len(best)} of {s1['entry'].nunique()} entries beat control:")
    print(fmt(top_entries[["entry", "exit"] + COLS]))
    if top_entries.empty:
        return

    # Stage 2: sweep every exit on the surviving entries
    jobs = [(parse_spec(e), (xf.name, xp), "train") for e in top_entries["entry"]
            for xf in EXITS for xp in expand(xf)]
    s2 = run_jobs(jobs, args.jobs, "Stage 2 - exit sweep")
    s2["ok"] = s2.apply(good, axis=1)
    n_configs = len(s1) + len(s2)

    ranked = s2[s2["ok"]].sort_values("t", ascending=False)
    finalists = ranked.groupby("entry").head(2).head(args.finalists).reset_index(drop=True)
    print(f"\nFinalists (train only, max 2 per entry):")
    print(fmt(finalists[["entry", "exit"] + COLS]))
    print(f"\nNOTE: {n_configs} configs tested. With that many tries, a train t of ~3 can appear by pure luck,"
          f"\nso train t is only a RANKING. The holdouts and neighbour stability below are the evidence.")

    report = finalist_report(finalists, args.jobs)
    out = config.LAB_DIR / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    s1.to_csv(out / "stage1.csv", index=False)
    s2.to_csv(out / "stage2.csv", index=False)
    report.to_csv(out / "finalists.csv", index=False)
    print(f"\nSaved run to {out}")


def print_family_summary(s1: pd.DataFrame, fams):
    """One line per entry family: how many of its configs beat random entries, and its best one."""
    print(f"\n{'Family':<16} {'configs':>7} {'beat ctrl':>9} {'best t':>7}  best config (train)")
    rows = []
    for f in fams:
        g = s1[s1["family"] == f.name]
        ok = g[g["ok"]]
        n_cfg = g["entry"].nunique()
        n_ok = ok["entry"].nunique()
        b = ok.sort_values("t", ascending=False).head(1)
        rows.append((n_ok / max(n_cfg, 1), f.name, n_cfg, n_ok,
                     b["t"].iloc[0] if len(b) else float("nan"),
                     f"{b['entry'].iloc[0]} + {b['exit'].iloc[0]}" if len(b) else "-"))
    for share, name, n_cfg, n_ok, bt, desc in sorted(rows, reverse=True):
        print(f"{name:<16} {n_cfg:>7} {n_ok:>4} ({share * 100:>3.0f}%) {bt:>7.2f}  {desc}")


def finalist_report(finalists: pd.DataFrame, n_jobs: int) -> pd.DataFrame:
    # Neighbour stability (train) - is this a plateau or a lone spike?
    jobs, owner = [], []
    for k, r in finalists.iterrows():
        (en, ep), (xn, xp) = parse_spec(r["entry"]), parse_spec(r["exit"])
        for nb in neighbours(ENTRY_BY_NAME[en], ep):
            jobs.append(((en, nb), (xn, xp), "train")); owner.append(k)
        for nb in neighbours(EXIT_BY_NAME[xn], xp):
            jobs.append(((en, ep), (xn, nb), "train")); owner.append(k)
    nb = run_jobs(jobs, n_jobs, "Neighbour stability") if jobs else pd.DataFrame()
    if len(nb):
        nb["owner"] = owner
        # neighbours too thin to test (e.g. RSI5<5 fires ~once a year) are skipped, not scored as t=0
        nb = nb[nb["trades"] >= config.MIN_TRAIN_TRADES]
        nbs = nb.groupby("owner")["t"].agg(["median", "min", "count"])
    # Market-regime breakdown (train only): does the edge depend on the Nifty regime?
    jobs = []
    for _, r in finalists.iterrows():
        (en, ep), x = parse_spec(r["entry"]), parse_spec(r["exit"])
        jobs += [((en, {**ep, "regime": g}), x, "train") for g in ("bull", "neutral", "bear")]
    rg = run_jobs(jobs, n_jobs, "Regime breakdown (train)")
    # Holdouts
    jobs = [(parse_spec(r["entry"]), parse_spec(r["exit"]), h) for _, r in finalists.iterrows() for h in HOLDOUTS]
    ho = run_jobs(jobs, n_jobs, "Holdouts A / B / B24")
    log_holdout_view(list(finalists["entry"]), list(finalists["exit"]), "sweep")

    rows = []
    for k, r in finalists.iterrows():
        row = {"#": k + 1, "entry": r["entry"], "exit": r["exit"], "train_t": r["t"], "train_edge%": r["edge%"],
               "avg%": r["avg%"], "win%": r["win%"], "hold": r["hold"]}
        if len(nb) and k in nbs.index:
            row["nbr_t_med"], row["nbr_t_min"] = nbs.loc[k, "median"], nbs.loc[k, "min"]
        for h in HOLDOUTS:
            hr = ho[(ho["entry"] == r["entry"]) & (ho["exit"] == r["exit"]) & (ho["ds"] == h)].iloc[0]
            row[f"{h}_n"], row[f"{h}_avg%"], row[f"{h}_edge%"], row[f"{h}_t"] = (
                hr.get("trades", 0), hr.get("avg%"), hr.get("edge%"), hr.get("t"))
        for g in ("bull", "neutral", "bear"):
            e, ep = parse_spec(r["entry"])
            m = rg[rg["entry"] == spec(e, {**ep, "regime": g})]
            m = m[m["exit"] == r["exit"]]
            row[f"{g}_n"] = m["trades"].iloc[0] if len(m) else 0
            row[f"{g}_avg%"] = m["avg%"].iloc[0] if len(m) and m["trades"].iloc[0] else float("nan")
        row["verdict"] = verdict(row)
        rows.append(row)
    rep = pd.DataFrame(rows)

    print("\n" + "=" * 100 + "\nFINALISTS vs HOLDOUTS (edge% = mean monthly excess over random entries, net of costs)\n" + "=" * 100)
    for _, r in rep.iterrows():
        print(f"\n#{r['#']} {r['entry']}  +  {r['exit']}")
        print(f"   train: t={r['train_t']:.2f} edge={r['train_edge%']:+.2f}% avg={r['avg%']:+.2f}% "
              f"win={r['win%']:.0f}% hold={r['hold']:.1f}d | neighbours t median={r.get('nbr_t_med', float('nan')):.2f} "
              f"min={r.get('nbr_t_min', float('nan')):.2f}")
        print("   " + " | ".join(
            f"{h}: n={r[f'{h}_n']:.0f} avg={r[f'{h}_avg%']:+.2f}% edge={r[f'{h}_edge%']:+.2f}% t={r[f'{h}_t']:.2f}"
            if r[f"{h}_n"] else f"{h}: no trades" for h in HOLDOUTS))
        print("   train by Nifty regime: " + " | ".join(
            f"{g.upper()} n={r[f'{g}_n']:.0f} avg={r[f'{g}_avg%']:+.2f}%" for g in ("bull", "neutral", "bear")))
        if r["hold"] > 40:
            print("   (long hold: survivorship bias is largest here - today's index members recovered by definition)")
        print(f"   -> {r['verdict']}")
    return rep


def verdict(r: dict) -> str:
    fails = []
    if not (r.get("A_n", 0) and r["A_edge%"] > 0):
        fails.append("A edge<=0")
    if not (r.get("B_n", 0) and r["B_edge%"] > 0 and r["B_t"] > 2):
        fails.append("B not significant")
    if not (r.get("B24_n", 0) and r["B24_edge%"] > 0):
        fails.append("B24 edge<=0")
    if r.get("B_n", 0) and r["B_avg%"] <= 0:
        fails.append("loses money after costs")
    if pd.isna(r.get("nbr_t_med")) or r["nbr_t_med"] < 1.5:
        fails.append("unstable neighbours")
    return "CANDIDATE -> forward test in journal" if not fails else "REJECT: " + ", ".join(fails)


def cmd_test(args):
    from lab.engine import evaluate
    entry, exit_ = parse_spec(args.entry), parse_spec(args.exit)
    names = ["train"] + (HOLDOUTS if args.holdout else [])
    rows = [{"dataset": n, **evaluate(entry, exit_, n)} for n in names]
    print(f"{spec(*entry)}  +  {spec(*exit_)}\n")
    print(fmt(pd.DataFrame(rows)[["dataset"] + COLS]))
    if args.holdout:
        log_holdout_view([spec(*entry)], [spec(*exit_)], "test")


def cmd_families(args):
    for title, fams in (("ENTRIES", ENTRIES), ("EXITS", EXITS)):
        print(f"\n{title}")
        for f in fams:
            print(f"  {f.name:<16} {len(expand(f)):>3} configs  {f.desc}")
            for k, v in f.grid.items():
                print(f"      {k}: {v}")


def main():
    p = argparse.ArgumentParser(description="Strategy research lab")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze"); f.add_argument("--refresh", action="store_true")
    sub.add_parser("families")
    s = sub.add_parser("sweep")
    s.add_argument("--entries", help="comma-separated entry families (default: all)")
    s.add_argument("--top-entries", type=int, default=20)
    s.add_argument("--per-family", type=int, default=3, help="max entries per family into stage 2")
    s.add_argument("--finalists", type=int, default=12)
    s.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    t = sub.add_parser("test")
    t.add_argument("entry"); t.add_argument("exit")
    t.add_argument("--holdout", action="store_true", help="also evaluate holdouts A, B, B24 (logged)")
    args = p.parse_args()
    {"freeze": lambda a: lab_data.freeze(a.refresh), "families": cmd_families,
     "sweep": cmd_sweep, "test": cmd_test}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
