"""
Lab configs promoted to live forward-testing.

A promoted config is just an (entry spec, exit spec) pair from `python lab.py`.
It runs through the same lab family code, so the journal tests exactly what
the lab measured. Record why each one was promoted - the journal decides
whether it stays.
"""

import pandas as pd

from lab.families import ENTRY_BY_NAME, EXIT_BY_NAME, Ind, entry_signal, parse_spec
from strategies.base import Strategy


class LabStrategy(Strategy):
    def __init__(self, name: str, entry: str, exit_: str, research_only: bool, note: str, score: float = 50.0):
        self.name = name
        self.entry_spec, self.exit_spec = entry, exit_
        self.description = f"{entry} + {exit_}"
        self.research_only = research_only
        self.note = note
        self.base_score = score
        en, self.eparams = parse_spec(entry)
        xn, self.xparams = parse_spec(exit_)
        self.efam, self.xfam = ENTRY_BY_NAME[en], EXIT_BY_NAME[xn]
        self.group = en   # e.g. all drop_from_high variants are one idea
        _, _, self.exit_plan = self.xfam.fn(Ind(_dummy()), **self.xparams)

    def prepare(self, df: pd.DataFrame, ctx: pd.DataFrame) -> pd.DataFrame:
        I = Ind(df, regime=ctx["regime"] if "regime" in ctx else None)
        stop, exit_sig, _ = self.xfam.fn(I, **self.xparams)
        out = df.copy()
        out["atr"] = I.atr()
        out["signal"] = entry_signal(self.efam, I, self.eparams) & out["atr"].notna() & (stop < df["Close"])
        out["stop"] = stop
        out["exit_signal"] = exit_sig.fillna(False)
        out["score"] = self.base_score
        drop20 = (1 - df["Close"] / df["Close"].rolling(20).max()) * 100
        out["reason"] = ("RSI14 " + I.rsi(14).round(0).astype(str) + ", "
                         + drop20.round(1).astype(str) + "% off 20d high")
        if self.efam.name == "connors_rsi":
            out["reason"] = "CRSI " + I.connors_rsi().round(1).astype(str) + ", " + out["reason"]
        return out


def _dummy() -> pd.DataFrame:
    idx = pd.date_range("2020-01-01", periods=30)
    return pd.DataFrame({c: 1.0 for c in ("Open", "High", "Low", "Close", "Volume")}, index=idx)


PROMOTED = [
    LabStrategy(
        "dip12_rsi50",
        "drop_from_high(lookback=20,drop=12,trend=none)",
        "rule(rule=rsi14_gt_50,stop_atr=3.0,hold=10)",
        research_only=True, score=60,   # superseded 2026-10-08 by dip12_nn (regime-filtered)
        note="2026-10-07 sweep: entry robust across all exits (plateau). Train t=4.1; "
             "B t=4.7 (+0.38%/trade); B24 +1.03%/trade t=4.0; A NEGATIVE (-0.85%, 41 trades). "
             "3xATR stop chosen on train for tail risk (no-stop version lost up to 57% on a trade).",
    ),
    LabStrategy(
        "dip8_rsi2",
        "drop_from_high(lookback=10,drop=8,trend=none)",
        "rule(rule=rsi2_gt_70,stop_atr=3.0,hold=60)",
        research_only=True, score=55,   # demoted 2026-10-08: weaker than connors/dip12, negative in 2026
        note="2026-10-07 sweep #10. Train t=4.2; B t=5.5 (+0.37%); B24 +0.89% t=4.0; A +0.03%. "
             "8/11 years positive on B, 2026 slightly negative.",
    ),
    LabStrategy(
        "rsi5_dip",
        "rsi_dip(n=5,level=20,trend=none)",
        "rule(rule=rsi2_gt_70,stop_atr=2.0,hold=10)",
        research_only=True,
        note="2026-10-07 sweep #9. Passed lab gates but only 6/11 years positive on B - log only.",
    ),
    LabStrategy(
        "rsi2_atr_1r",
        "rsi_dip(n=2,level=20,trend=none)",
        "atr_r(stop_atr=3.0,target_r=1.0,hold=10)",
        research_only=True,
        note="2026-10-07 sweep #4. Passed lab gates but A negative, B24 only +0.06%/trade - log only.",
    ),
    # --- sweep #2 (2026-10-08): 6,172 configs, 17 entry families. Every dip strategy lost money in the
    # NEUTRAL Nifty regime on train; skipping NEUTRAL improved avg/trade on A, B and B24 (tested once).
    LabStrategy(
        "dip12_nn",
        "drop_from_high(lookback=20,drop=12,trend=none,regime=not_neutral)",
        "rule(rule=rsi14_gt_50,stop_atr=3.0,hold=10)",
        research_only=False, score=60,
        note="dip12_rsi50 skipping NEUTRAL regime. avg/trade: A -0.36% (34), B +0.65% (2742), B24 +1.01% (699).",
    ),
    LabStrategy(
        "crsi10_nn",
        "connors_rsi(level=10,trend=none,regime=not_neutral)",
        "rule(rule=rsi2_gt_70,stop_atr=2.0,hold=10)",
        research_only=False, score=60,
        note="Sweep #2 #2, most stable finalist (neighbour t min 2.59). With regime filter avg/trade: "
             "A +0.15% (104), B +0.44% (3090), B24 +0.48% (909). 2xATR stop.",
    ),
    LabStrategy(
        "crsi5_t5",
        "connors_rsi(level=5,trend=none,regime=not_neutral)",
        "fixed_pct(target=5,stop=8,hold=5)",
        research_only=False, score=55,
        note="'Take +5% within 5 days' style. Sweep #2 #12. With regime filter avg/trade: A +0.53% (21), "
             "B +0.19% (735), B24 +0.57% (208). Rare signal (~1/stock/yr). 8% stop.",
    ),
]
