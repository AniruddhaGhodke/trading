"""
Intraday research lab.

  python lab_intraday.py freeze                  # snapshot Angel One 5m history into data/lab_intraday/5m
  python lab_intraday.py freeze --source yahoo   # snapshot Yahoo 1h (~3y) + 5m (60d)
  python lab_intraday.py families [--interval 5m]
  python lab_intraday.py sweep    [--interval 5m]  # default 1h (Yahoo); 5m = Angel One 2018+
  python lab_intraday.py test "orb(or_min=15,dir=long,max_range=99.0,trend=any)" "ipct(stop=1.0,target=1.0)" --interval 5m [--holdout]

Splits (config.py):
  1h: train = 10 LAB_STOCKS Dec-2023..Jun-2025 | A = same stocks Jul-2025+ | B = other 90 | B25 = other 90 Jul-2025+
  5m: train = 10 LAB_STOCKS Mar-2018..Dec-2023 | A = same stocks 2024+    | B = other 90 | B25 = other 90 2024+
Costs: base 0.05%/side, stress 0.10%/side. Every holdout look is logged per interval.
"""

import argparse
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime

import pandas as pd

import config
from core import intraday_data as idd
from lab.families import expand, neighbours, parse_spec, spec
from lab.intraday import registry

warnings.filterwarnings("ignore")
HOLDOUTS = ["A", "B", "B25"]
MIN_TRADES = 50
COLS = ["trades", "trades/day", "win%", "avg%", "avg%_stress", "ctrl%", "edge%", "t", "beat%",
        "long_n", "long_avg%", "short_n", "short_avg%"]


def _job(job):
    warnings.filterwarnings("ignore")
    from lab.intraday import evaluate
    entry, exit_, ds, iv = job
    try:
        return {"entry": spec(*entry), "exit": spec(*exit_), "ds": ds, **evaluate(entry, exit_, ds, iv)}
    except Exception as e:
        return {"entry": spec(*entry), "exit": spec(*exit_), "ds": ds, "trades": 0, "error": str(e)[:100]}


def run_jobs(jobs, n_jobs, label):
    print(f"  {label}: {len(jobs)} runs...", end="", flush=True)
    t0 = datetime.now()
    with ProcessPoolExecutor(n_jobs) as ex:
        res = pd.DataFrame(list(ex.map(_job, jobs, chunksize=4)))
    print(f" done in {(datetime.now() - t0).seconds}s")
    if "error" in res and res["error"].notna().any():
        print(f"  !! {res['error'].notna().sum()} runs errored, e.g.: {res['error'].dropna().iloc[0]}")
    return res


def fmt(df, cols=COLS):
    show = df.copy()
    for c in cols:
        if c in show:
            show[c] = show[c].map(lambda v: "" if pd.isna(v) else
                                  f"{v:.0f}" if c in ("trades", "beat%", "long_n", "short_n") else
                                  f"{v:+.3f}" if "%" in c else f"{v:.2f}")
    return show.to_string(index=False)


def good(r):
    return r.get("trades", 0) >= MIN_TRADES and r.get("edge%", 0) > 0 and r.get("avg%", 0) > -0.05


def log_holdout(entries, exits, iv):
    log = idd.LAB_DIR / f"holdout_log_{iv}.csv"
    rows = pd.DataFrame({"when": datetime.now().isoformat(timespec="seconds"), "entry": entries, "exit": exits})
    rows.to_csv(log, mode="a", header=not log.exists(), index=False)
    print(f"\n[{iv} intraday holdout views logged so far: {len(pd.read_csv(log))}]")


def cmd_sweep(args):
    iv = args.interval
    ENTRIES, EXITS, EBN, XBN = registry(iv)
    REG = {**EBN, **XBN}
    jobs_n = args.jobs if iv == "1h" else min(args.jobs, 3)   # 5m frames are large; bound memory
    print(f"Interval {iv} | train: {len(config.LAB_STOCKS)} lab stocks | workers={jobs_n}")
    jobs = [((f.name, p), ("itime", x), "train", iv) for f in ENTRIES for p in expand(f) for x in expand(XBN["itime"])]
    s1 = run_jobs(jobs, jobs_n, "Stage 1 - entry screen")
    s1["family"] = s1["entry"].str.split("(").str[0]
    s1["ok"] = s1.apply(good, axis=1)

    print(f"\n{'Family':<10} {'configs':>7} {'pass':>5} {'best t':>7} {'best avg%':>9}  best config (train, 0.05%/side)")
    for f in ENTRIES:
        g = s1[s1["family"] == f.name]
        b = g.sort_values("t", ascending=False).head(1)
        print(f"{f.name:<10} {g['entry'].nunique():>7} {g[g['ok']]['entry'].nunique():>5} "
              f"{b['t'].iloc[0]:>7.2f} {b['avg%'].iloc[0]:>+9.3f}  {b['entry'].iloc[0]} + {b['exit'].iloc[0]}")

    best = s1[s1["ok"]].sort_values("t", ascending=False).drop_duplicates("entry")
    top = best.groupby("family").head(3).head(args.top_entries)
    print(f"\nTop entries (train): {len(best)} of {s1['entry'].nunique()} pass the screen")
    if top.empty:
        print("Nothing passes the train screen.")
        return
    print(fmt(top[["entry", "exit"] + COLS]))

    jobs = [(parse_spec(e, REG), (xf.name, xp), "train", iv) for e in top["entry"] for xf in EXITS for xp in expand(xf)]
    s2 = run_jobs(jobs, jobs_n, "Stage 2 - exit sweep")
    s2["ok"] = s2.apply(good, axis=1)
    fin = s2[s2["ok"]].sort_values("t", ascending=False).groupby("entry").head(2).head(args.finalists).reset_index(drop=True)
    print(f"\nFinalists (train only):\n{fmt(fin[['entry', 'exit'] + COLS])}")
    print(f"\nNOTE: {len(s1) + len(s2)} configs tested; train t is only a ranking.")
    if fin.empty:
        return
    rep = report(fin, jobs_n, iv)
    out = idd.LAB_DIR / "runs" / f"{iv}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    s1.to_csv(out / "stage1.csv", index=False)
    s2.to_csv(out / "stage2.csv", index=False)
    rep.to_csv(out / "finalists.csv", index=False)
    print(f"\nSaved run to {out}")


def report(fin, n_jobs, iv):
    _, _, EBN, XBN = registry(iv)
    REG = {**EBN, **XBN}
    jobs, owner = [], []
    for k, r in fin.iterrows():
        (en, ep), (xn, xp) = parse_spec(r["entry"], REG), parse_spec(r["exit"], REG)
        for nb in neighbours(EBN[en], ep):
            jobs.append(((en, nb), (xn, xp), "train", iv)); owner.append(k)
        for nb in neighbours(XBN[xn], xp):
            jobs.append(((en, ep), (xn, nb), "train", iv)); owner.append(k)
    nbs = None
    if jobs:
        nb = run_jobs(jobs, n_jobs, "Neighbour stability")
        nb["owner"] = owner
        nb = nb[nb["trades"] >= MIN_TRADES]
        nbs = nb.groupby("owner")["t"].agg(["median", "min"])
    # Holdouts: stream each stock once and evaluate every finalist on it (memory-safe on 5m data)
    from lab.intraday import holdout_batch
    print(f"  Holdouts A / B / B25 for {len(fin)} finalists (streaming stocks)...", flush=True)
    t0 = datetime.now()
    hb = holdout_batch([(parse_spec(r["entry"], REG), parse_spec(r["exit"], REG)) for _, r in fin.iterrows()],
                       iv, progress=True)
    print(f"  done in {(datetime.now() - t0).seconds}s")
    ho = pd.DataFrame([{"entry": r["entry"], "exit": r["exit"], "ds": h, **hb[k][h]}
                       for k, (_, r) in enumerate(fin.iterrows()) for h in HOLDOUTS])
    log_holdout(list(fin["entry"]), list(fin["exit"]), iv)

    rows = []
    print("\n" + "=" * 100 + f"\n[{iv}] INTRADAY FINALISTS vs HOLDOUTS (avg% per trade net of 0.05%/side; stress = 0.10%/side)\n" + "=" * 100)
    for k, r in fin.iterrows():
        row = {"entry": r["entry"], "exit": r["exit"], "train_t": r["t"], "train_avg%": r["avg%"]}
        if nbs is not None and k in nbs.index:
            row["nbr_t_med"], row["nbr_t_min"] = nbs.loc[k, "median"], nbs.loc[k, "min"]
        for h in HOLDOUTS:
            x = ho[(ho["entry"] == r["entry"]) & (ho["exit"] == r["exit"]) & (ho["ds"] == h)].iloc[0].to_dict()
            for c in ("trades", "avg%", "avg%_stress", "edge%", "t", "long_avg%", "short_avg%"):
                row[f"{h}_{c}"] = x.get(c)
        fails = []
        if not (row["A_trades"] and row["A_avg%"] > 0):
            fails.append("A avg<=0")
        if not (row["B_trades"] and row["B_avg%"] > 0 and row["B_t"] > 2):
            fails.append("B not profitable+significant")
        if not (row["B25_trades"] and row["B25_avg%"] > 0):
            fails.append("B25 avg<=0")
        if not (row.get("B_avg%_stress") or -1) > 0:
            fails.append("dies at 0.10%/side costs")
        if pd.isna(row.get("nbr_t_med")) or row["nbr_t_med"] < 1.5:
            fails.append("unstable neighbours")
        row["verdict"] = "CANDIDATE" if not fails else "REJECT: " + ", ".join(fails)
        rows.append(row)
        print(f"\n#{k + 1} {r['entry']} + {r['exit']}")
        print(f"   train t={r['t']:.2f} avg={r['avg%']:+.3f}% win={r['win%']:.0f}% {r['trades/day']:.1f} trades/day | "
              f"neighbours t med={row.get('nbr_t_med', float('nan')):.2f} min={row.get('nbr_t_min', float('nan')):.2f}")
        for h in HOLDOUTS:
            if not row[f"{h}_trades"]:
                print(f"   {h:<4} no trades")
                continue
            print(f"   {h:<4} n={row[f'{h}_trades']:.0f} avg={row[f'{h}_avg%']:+.3f}% stress={row[f'{h}_avg%_stress']:+.3f}% "
                  f"edge={row[f'{h}_edge%']:+.3f}% t={row[f'{h}_t']:.2f} | long {row[f'{h}_long_avg%']:+.3f}% "
                  f"short {row[f'{h}_short_avg%']:+.3f}%")
        print(f"   -> {row['verdict']}")
    return pd.DataFrame(rows)


def cmd_test(args):
    from lab.intraday import evaluate
    _, _, EBN, XBN = registry(args.interval)
    REG = {**EBN, **XBN}
    e, x = parse_spec(args.entry, REG), parse_spec(args.exit, REG)
    names = ["train"] + (HOLDOUTS if args.holdout else [])
    print(f"[{args.interval}] {spec(*e)} + {spec(*x)}\n")
    print(fmt(pd.DataFrame([{"dataset": n, **evaluate(e, x, n, args.interval)} for n in names])[["dataset"] + COLS]))
    if args.holdout:
        log_holdout([spec(*e)], [spec(*x)], args.interval)


def cmd_families(args):
    ENTRIES, EXITS, _, _ = registry(args.interval)
    for title, fams in (("ENTRIES", ENTRIES), ("EXITS", EXITS)):
        print(f"\n{title}")
        for f in fams:
            print(f"  {f.name:<10} {len(expand(f)):>3} configs  {f.desc}")
            for k, v in f.grid.items():
                print(f"      {k}: {v}")


def main():
    p = argparse.ArgumentParser(description="Intraday research lab")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--source", choices=["yahoo", "angel"], default="angel")
    fa = sub.add_parser("families")
    s = sub.add_parser("sweep")
    s.add_argument("--top-entries", type=int, default=15)
    s.add_argument("--finalists", type=int, default=10)
    s.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    t = sub.add_parser("test")
    t.add_argument("entry")
    t.add_argument("exit")
    t.add_argument("--holdout", action="store_true")
    for sp in (fa, s, t):
        sp.add_argument("--interval", choices=["1h", "5m"], default="1h")
    a = p.parse_args()
    if a.cmd == "freeze":
        idd.freeze_angel() if a.source == "angel" else idd.freeze()
        return
    {"families": cmd_families, "sweep": cmd_sweep, "test": cmd_test}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
