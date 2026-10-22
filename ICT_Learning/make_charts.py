"""Generate every teaching diagram for the ICT notes.

All prices are hand-coded / deterministic (no market data, no randomness) and each
pattern a chart claims to show is asserted before it is drawn.

    python make_charts.py        -> writes PNGs into ./images
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

OUT = Path(__file__).parent / "images"
OUT.mkdir(exist_ok=True)

UP, DOWN = "#2e9d6b", "#d1495b"
INK, MUTED, LINE = "#222222", "#666666", "#555555"
BULL_ZONE, BEAR_ZONE, NEUTRAL_ZONE = "#2e9d6b", "#d1495b", "#4c78a8"

plt.rcParams.update({
    "figure.dpi": 110, "savefig.dpi": 110, "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#999999", "axes.grid": True, "grid.color": "#eeeeee",
    "axes.titleweight": "bold", "axes.titlesize": 12,
})


# ---------------------------------------------------------------- helpers
def candle(o, h, l, c):
    assert l <= min(o, c) and h >= max(o, c), (o, h, l, c)
    return dict(o=o, h=h, l=l, c=c)


def path_candles(waypoints, wick=0.5):
    """Candles that walk linearly between (index, price) waypoints."""
    closes = []
    for (i0, p0), (i1, p1) in zip(waypoints, waypoints[1:]):
        for k in range(i0, i1):
            closes.append(p0 + (p1 - p0) * (k - i0) / (i1 - i0))
    closes.append(waypoints[-1][1])
    # small deterministic wiggle so candles don't look identical
    wiggle = [0.0, 0.6, -0.4, 0.3, -0.5, 0.2]
    out = []
    for i in range(1, len(closes)):
        o = closes[i - 1] + wiggle[i % 6] * 0.3
        c = closes[i]
        out.append(candle(o, max(o, c) + wick, min(o, c) - wick, c))
    return out


def draw(ax, candles, x0=0, width=0.6):
    for i, k in enumerate(candles):
        x = x0 + i
        col = UP if k["c"] >= k["o"] else DOWN
        ax.vlines(x, k["l"], k["h"], color=col, lw=1.2, zorder=3)
        lo, hi = sorted((k["o"], k["c"]))
        ax.add_patch(Rectangle((x - width / 2, lo), width, max(hi - lo, 0.05),
                               facecolor=col, edgecolor=col, zorder=4))
    ax.set_xlim(x0 - 1, x0 + len(candles))


def hline(ax, y, x0, x1, label=None, color=LINE, ls="--", side="right", va="bottom"):
    ax.plot([x0, x1], [y, y], ls=ls, color=color, lw=1.3, zorder=2)
    if label:
        x = {"right": x1, "left": x0, "mid": (x0 + x1) / 2}[side]
        ax.text(x, y, " " + label + " ", color=color, fontsize=9, va=va,
                ha={"right": "right", "left": "left", "mid": "center"}[side])


def zone(ax, x0, x1, y0, y1, color, label=None, alpha=0.15, label_y="top"):
    ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=color,
                           edgecolor=color, alpha=alpha, lw=0, zorder=1))
    if label:
        y = y1 if label_y == "top" else y0
        ax.text(x1, y, label + " ", ha="right", va="top" if label_y == "top" else "bottom",
                fontsize=9, color=color, fontweight="bold")


def fvg_bull(c1, c3):
    return c3["l"] > c1["h"]


def fvg_bear(c1, c3):
    return c3["h"] < c1["l"]


def finish(fig, name):
    fig.tight_layout()
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("wrote", name)


def noaxis_x(ax, label="time  →"):
    ax.set_xticks([])
    ax.set_xlabel(label, color=MUTED)


# ---------------------------------------------------------------- 00 market structure
def chart_structure():
    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.6))

    wp = [(0, 100), (6, 110), (10, 104), (16, 116), (20, 109), (27, 124)]
    cs = path_candles(wp)
    draw(a, cs)
    labels = {5: ("swing high", "h"), 9: ("HL", "l"), 15: ("HH", "h"), 19: ("HL", "l")}
    for i, (t, side) in labels.items():
        y = cs[i][side]
        a.annotate(t, (i, y), xytext=(0, 8 if side == "h" else -14), textcoords="offset points",
                   ha="center", fontsize=9, fontweight="bold", color=INK)
    brk = next(i for i in range(10, len(cs)) if cs[i]["c"] > cs[5]["h"])
    hline(a, cs[5]["h"], 5, brk, "BOS", color=UP, side="mid")
    brk2 = next(i for i in range(20, len(cs)) if cs[i]["c"] > cs[15]["h"])
    hline(a, cs[15]["h"], 15, brk2, "BOS", color=UP, side="mid")
    a.set_title("Bullish structure: higher highs + higher lows")
    noaxis_x(a)

    wp = [(0, 100), (6, 110), (10, 105), (16, 114), (24, 100)]
    cs = path_candles(wp)
    draw(b, cs)
    hl = cs[9]["l"]
    brk = next(i for i in range(16, len(cs)) if cs[i]["c"] < hl)
    assert brk > 15
    b.annotate("last higher low", (9, hl), xytext=(0, -14), textcoords="offset points",
               ha="center", fontsize=9, fontweight="bold")
    b.annotate("HH", (15, cs[15]["h"]), xytext=(0, 8), textcoords="offset points",
               ha="center", fontsize=9, fontweight="bold")
    hline(b, hl, 9, brk, "MSS", color=DOWN, side="mid")
    b.set_title("Market Structure Shift: the last higher low breaks")
    noaxis_x(b)
    finish(fig, "00_market_structure.png")


# ---------------------------------------------------------------- 01 HTF
def chart_htf():
    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.6), gridspec_kw={"width_ratios": [1, 1.3]})
    cs = path_candles([(0, 100), (8, 118), (12, 110), (20, 130), (24, 122)], wick=1.0)
    draw(a, cs)
    zone(a, 19.5, 23.6, 119.5, 132, NEUTRAL_ZONE, alpha=0.12)
    a.text(21.5, 132.3, "zoom in", ha="center", va="bottom", color=NEUTRAL_ZONE, fontsize=9,
           fontweight="bold")
    a.annotate("", xy=(20, 129), xytext=(1, 101),
               arrowprops=dict(arrowstyle="->", color=UP, lw=2, alpha=0.6))
    a.text(12, 101, "Daily bias:\nBULLISH", color=UP, fontweight="bold", fontsize=11)
    a.set_title("HTF (Daily): decides the direction")
    noaxis_x(a, "days  →")

    cs = path_candles([(0, 130), (10, 121.5), (13, 123.5), (16, 121), (19, 122.5), (32, 131)], wick=0.35)
    draw(b, cs)
    b.annotate("only look for BUYS here\n(pullback in a daily uptrend)", (16, 120.6),
               xytext=(20, 122), fontsize=9, color=INK,
               arrowprops=dict(arrowstyle="->", color=MUTED))
    b.text(1, 121, "shorting this dip =\ntrading against the HTF", color=DOWN, fontsize=9)
    b.set_title("LTF (15-min): decides the entry")
    noaxis_x(b, "15-min candles  →")
    finish(fig, "01_htf_bias.png")


# ---------------------------------------------------------------- 02 premium / discount
def chart_premium_discount():
    fig, ax = plt.subplots(figsize=(11, 5.2))
    cs = path_candles([(0, 104), (2, 100), (14, 140), (22, 111.5), (30, 134)], wick=0.7)
    draw(ax, cs)
    lo, hi = cs[1]["l"], cs[13]["h"]
    eq = (lo + hi) / 2
    zone(ax, 1, 30, eq, hi, BEAR_ZONE, "PREMIUM  (expensive - look to sell)", alpha=0.10)
    zone(ax, 1, 30, lo, eq, BULL_ZONE, "DISCOUNT  (cheap - look to buy)", alpha=0.10, label_y="bottom")
    hline(ax, hi, 1, 30, f"range high {hi:.0f} (100%)", color=INK, ls="-", side="left")
    hline(ax, lo, 1, 30, f"range low {lo:.0f} (0%)", color=INK, ls="-", side="left", va="top")
    hline(ax, eq, 1, 30, f"equilibrium 50% = {eq:.1f}", color=LINE, side="left")
    ote_hi, ote_lo = hi - 0.62 * (hi - lo), hi - 0.79 * (hi - lo)
    ax.add_patch(Rectangle((19.5, ote_lo), 4, ote_hi - ote_lo, fill=False, ls=":", ec=INK, lw=1.2))
    ax.text(23.7, (ote_lo + ote_hi) / 2, "OTE\n62-79%\nretrace", fontsize=8, va="center", color=INK)
    pull = min(k["l"] for k in cs[14:24])
    assert pull < eq, "pullback must reach discount"
    ax.set_title("Premium / Discount: where is price inside the dealing range?")
    noaxis_x(ax)
    finish(fig, "02_premium_discount.png")


# ---------------------------------------------------------------- 03 liquidity pools
def chart_liquidity_pools():
    fig, ax = plt.subplots(figsize=(11, 5))
    wp = [(0, 108), (5, 120), (8, 100.6), (11, 120.2), (14, 100.3), (17, 119.9), (21, 110)]
    cs = path_candles(wp, wick=0.4)
    draw(ax, cs)
    highs = [cs[i]["h"] for i in (4, 10, 16)]
    lows = [cs[i]["l"] for i in (7, 13)]
    assert max(highs) - min(highs) < 1.0 and max(lows) - min(lows) < 1.0
    top, bot = max(highs) + 0.6, min(lows) - 0.6
    zone(ax, 3, 21, top, top + 3, NEUTRAL_ZONE, alpha=0.12)
    ax.text(12, top + 1.5, "BUY-SIDE LIQUIDITY (BSL)\nbuy-stops of shorts + breakout buy orders",
            ha="center", va="center", fontsize=9, color=NEUTRAL_ZONE, fontweight="bold")
    zone(ax, 3, 21, bot - 3, bot, NEUTRAL_ZONE, alpha=0.12)
    ax.text(12, bot - 1.5, "SELL-SIDE LIQUIDITY (SSL)\nsell-stops of longs + breakdown sell orders",
            ha="center", va="center", fontsize=9, color=NEUTRAL_ZONE, fontweight="bold")
    for i in (4, 10, 16):
        ax.annotate("=", (i, cs[i]["h"]), xytext=(0, 4), textcoords="offset points", ha="center",
                    fontweight="bold")
    for i in (7, 13):
        ax.annotate("=", (i, cs[i]["l"]), xytext=(0, -12), textcoords="offset points", ha="center",
                    fontweight="bold")
    ax.text(0, 121, "equal highs", fontsize=9, color=INK)
    ax.text(0, 99.3, "equal lows", fontsize=9, color=INK)
    ax.set_ylim(bot - 4, top + 4)
    ax.set_title("Liquidity pools: clusters of resting stop orders above highs / below lows")
    noaxis_x(ax)
    finish(fig, "03_liquidity_pools.png")


# ---------------------------------------------------------------- 04 sweep vs breakout
def chart_sweep():
    lvl = 100.0
    base = [candle(103.5, 104, 102.5, 102.8), candle(102.8, 103.2, 101.2, 101.6),
            candle(101.6, 102.3, 100.6, 101.9), candle(101.9, 102.6, 101.0, 101.3),
            candle(101.3, 101.8, 100.4, 101.5)]
    sweep = base + [candle(101.5, 101.8, 98.4, 101.1),   # wick below, close back above
                    candle(101.1, 103.6, 100.9, 103.4), candle(103.4, 105.4, 103.1, 105.1),
                    candle(105.1, 106.2, 104.6, 105.9)]
    k = sweep[5]
    assert k["l"] < lvl < k["c"], "sweep = trades through the level, closes back inside"
    brk = base + [candle(101.5, 101.7, 98.6, 98.9),       # closes beyond the level
                  candle(98.9, 99.9, 98.1, 99.6),          # retest from below
                  candle(99.6, 99.9, 96.8, 97.1), candle(97.1, 97.5, 95.4, 95.8)]
    assert brk[5]["c"] < lvl and brk[6]["h"] < lvl

    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)
    for ax, cs, title in ((a, sweep, "SWEEP: wick through, close back inside -> reversal"),
                          (b, brk, "BREAKOUT: candle CLOSES beyond -> continuation")):
        draw(ax, cs)
        hline(ax, lvl, -0.5, len(cs) - 0.5, "sell-side liquidity (old lows)", side="left", va="top")
        ax.set_title(title, fontsize=11)
        noaxis_x(ax)
    a.annotate("stops triggered,\nthen price snaps back", (5, 98.4), xytext=(6.2, 97.0), fontsize=9,
               arrowprops=dict(arrowstyle="->", color=MUTED))
    b.annotate("acceptance below\nthe level", (5, 98.9), xytext=(6.3, 101.5), fontsize=9,
               arrowprops=dict(arrowstyle="->", color=MUTED))
    finish(fig, "04_liquidity_sweep.png")


# ---------------------------------------------------------------- 05 displacement
def chart_displacement():
    chop_c = [100.8, 101.4, 100.6, 101.2, 100.9, 101.6, 101.0, 101.5]
    chop = []
    prev = 101.0
    for c in chop_c:
        chop.append(candle(prev, max(prev, c) + 0.3, min(prev, c) - 0.3, c))
        prev = c
    disp = chop + [candle(101.5, 104.4, 101.3, 104.2), candle(104.2, 108.2, 104.0, 108.0),
                   candle(108.0, 111.6, 107.4, 111.3), candle(111.3, 111.9, 110.4, 110.8)]
    assert fvg_bull(disp[8], disp[10]) or fvg_bull(disp[7], disp[9])
    grind_c = [101.5 + i * 0.65 for i in range(1, 16)]
    grind = chop[:]
    prev = chop[-1]["c"]
    for i, c in enumerate(grind_c):
        o = prev + (0.35 if i % 2 else -0.15)
        grind.append(candle(o, max(o, c) + 0.9, min(o, c) - 0.9, c))
        prev = c
    assert not any(fvg_bull(grind[i], grind[i + 2]) for i in range(len(grind) - 2))

    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)
    draw(a, disp)
    gi = 7 if fvg_bull(disp[7], disp[9]) else 8
    zone(a, gi, len(disp) - 0.5, disp[gi]["h"], disp[gi + 2]["l"], BULL_ZONE, alpha=0.25)
    a.annotate("big bodies, small wicks,\n3 candles = 10 points", (9, 106), xytext=(0.5, 108),
               fontsize=9, arrowprops=dict(arrowstyle="->", color=MUTED))
    a.text(len(disp) - 0.5, disp[gi]["h"] - 0.2, "FVG left behind ", ha="right", va="top",
           fontsize=9, color=UP, fontweight="bold")
    a.set_title("DISPLACEMENT + SPEED (institutional)")
    noaxis_x(a)
    draw(b, grind)
    b.annotate("overlapping candles,\n15 candles for the same distance\n-> no FVG, weak signal",
               (16, 106), xytext=(0.5, 108), fontsize=9, arrowprops=dict(arrowstyle="->", color=MUTED))
    b.set_title("SLOW GRIND (not displacement)")
    noaxis_x(b)
    finish(fig, "05_displacement.png")


# ---------------------------------------------------------------- 06 FVG
def chart_fvg():
    bull = [candle(99.2, 100.4, 98.9, 100.0),
            candle(100.0, 101.5, 99.5, 101.0),   # c1
            candle(101.0, 106.0, 100.8, 105.6),  # c2 displacement
            candle(105.6, 107.0, 103.0, 106.5),  # c3
            candle(106.5, 106.8, 104.0, 104.3),
            candle(104.3, 104.6, 102.1, 103.8),  # returns into the gap
            candle(103.8, 107.5, 103.5, 107.2),
            candle(107.2, 109.3, 106.9, 109.0)]
    assert fvg_bull(bull[1], bull[3])
    K = 210.0
    bear = [candle(K - k["o"], K - k["l"], K - k["h"], K - k["c"]) for k in bull]
    assert fvg_bear(bear[1], bear[3])

    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.8))
    for ax, cs, bullish in ((a, bull, True), (b, bear, False)):
        draw(ax, cs)
        y0, y1 = (cs[1]["h"], cs[3]["l"]) if bullish else (cs[3]["h"], cs[1]["l"])
        col = BULL_ZONE if bullish else BEAR_ZONE
        zone(ax, 1, len(cs) - 0.5, y0, y1, col, alpha=0.22)
        ce = (y0 + y1) / 2
        ax.plot([1, len(cs) - 0.5], [ce, ce], ls=":", color=col, lw=1.2)
        ax.text(len(cs) - 0.4, ce, " CE (50%)", va="center", fontsize=8, color=col)
        for i, t in ((1, "1"), (2, "2"), (3, "3")):
            yy = cs[i]["l"] if bullish else cs[i]["h"]
            ax.annotate(t, (i, yy), xytext=(0, -14 if bullish else 6), textcoords="offset points",
                        ha="center", fontweight="bold", color=INK)
        ax.set_xlim(-1, len(cs) + 1.2)
        noaxis_x(ax)
    a.annotate("gap = candle 1 HIGH\nto candle 3 LOW", (1.6, 102.2), xytext=(-0.8, 106),
               fontsize=9, arrowprops=dict(arrowstyle="->", color=MUTED))
    a.annotate("price returns,\nbuyers defend", (5, 102.1), xytext=(5.6, 100.0), fontsize=9,
               arrowprops=dict(arrowstyle="->", color=MUTED))
    a.set_title("Bullish FVG -> acts as SUPPORT")
    b.annotate("gap = candle 1 LOW\nto candle 3 HIGH", (1.6, K - 102.2), xytext=(-0.8, K - 106.5),
               fontsize=9, arrowprops=dict(arrowstyle="->", color=MUTED))
    b.set_title("Bearish FVG -> acts as RESISTANCE")
    finish(fig, "06_fvg.png")


# ---------------------------------------------------------------- 07 IFVG
def chart_ifvg():
    cs = [candle(110.0, 110.5, 108.8, 109.0),
          candle(109.0, 109.4, 107.8, 108.0),   # c1
          candle(108.0, 108.2, 103.5, 104.0),   # c2
          candle(104.0, 105.5, 102.8, 103.2),   # c3 -> bearish FVG 105.5..107.8
          candle(103.2, 103.6, 101.5, 102.0),
          candle(102.0, 102.4, 100.8, 102.2),
          candle(102.2, 106.5, 102.0, 106.2),
          candle(106.2, 109.6, 106.0, 109.2),   # CLOSES above the gap -> inversion
          candle(109.2, 109.8, 108.4, 108.6),
          candle(108.6, 108.9, 106.9, 107.3),   # retest from above, holds
          candle(107.3, 110.5, 107.0, 110.2),
          candle(110.2, 112.0, 109.8, 111.8)]
    assert fvg_bear(cs[1], cs[3])
    top, bot = cs[1]["l"], cs[3]["h"]
    inv = next(i for i in range(4, len(cs)) if cs[i]["c"] > top)
    assert inv == 7 and bot <= cs[9]["l"] <= top and cs[9]["c"] > bot

    fig, ax = plt.subplots(figsize=(11, 5))
    draw(ax, cs)
    zone(ax, 1, inv, bot, top, BEAR_ZONE, alpha=0.22)
    zone(ax, inv, len(cs) - 0.5, bot, top, BULL_ZONE, alpha=0.22)
    ax.text(-0.95, (top + bot) / 2, "1) bearish\nFVG\n(resistance)", fontsize=9, color=DOWN, va="center",
            fontweight="bold")
    ax.annotate("2) candle CLOSES above the gap\n    = the FVG failed -> inverted", (inv, cs[inv]["c"]),
                xytext=(1.2, 111.3), fontsize=9, arrowprops=dict(arrowstyle="->", color=MUTED))
    ax.annotate("3) retest from above:\n    old resistance now SUPPORT (IFVG)", (9, 106.9),
                xytext=(8.3, 103.3), fontsize=9, color=UP,
                arrowprops=dict(arrowstyle="->", color=UP))
    ax.set_xlim(-1, len(cs) + 0.5)
    ax.set_title("Inverse FVG (IFVG): a gap that gets closed through flips its role")
    noaxis_x(ax)
    finish(fig, "07_ifvg.png")


# ---------------------------------------------------------------- 08 sessions timeline
def chart_sessions():
    fig, ax = plt.subplots(figsize=(12, 3.6))
    rows = [  # (label, start ET, end ET, color)  - hours relative to midnight ET
        ("Asian range (ICT)", -4, 0, "#9e9e9e"),
        ("London kill zone", 2, 5, NEUTRAL_ZONE),
        ("New York AM kill zone", 7, 10, "#e08a2e"),
        ("NSE cash (in EDT months)", -0.25, 6, UP),
    ]
    for y, (lab, s, e, col) in enumerate(rows):
        ax.barh(y, e - s, left=s, color=col, alpha=0.75, height=0.55)
        ax.text(s - 0.15, y, lab, ha="right", va="center", fontsize=9)
    ax.set_yticks([])
    ax.set_ylim(-0.7, len(rows) - 0.3)
    ax.invert_yaxis()
    ticks = list(range(-6, 13, 2))
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t % 24:02d}:00" for t in ticks])
    ax.set_xlabel("New York time (ET)")
    ax.set_xlim(-10.5, 12.5)
    sec = ax.secondary_xaxis("top", functions=(lambda x: x + 9.5, lambda x: x - 9.5))
    sec.set_xticks([t + 9.5 for t in ticks])
    sec.set_xticklabels([f"{int((t + 9.5) % 24):02d}:{30 if (t + 9.5) % 1 else 0:02d}" for t in ticks])
    sec.set_xlabel("India time (IST) while US is on daylight time (EDT, ~Mar-Nov). In winter add 1 hour.")
    ax.axvline(0, color="#bbbbbb", lw=1)
    ax.set_title("ICT sessions on one clock: London 2-5am ET = 11:30-14:30 IST (summer) / 12:30-15:30 IST (winter)",
                 fontsize=11, pad=34)
    ax.grid(axis="y", visible=False)
    finish(fig, "08_sessions.png")


# ---------------------------------------------------------------- 09 the full model (the "V")
def chart_model():
    closes = [101.0, 101.5, 100.8, 101.6, 101.0, 101.8, 101.2, 100.6, 101.3, 101.9, 101.1, 100.7]
    cs, prev = [], 101.2
    for c in closes:
        cs.append(candle(prev, max(prev, c) + 0.3, min(prev, c) - 0.3, c))
        prev = c
    asia_lo, asia_hi = min(k["l"] for k in cs), max(k["h"] for k in cs)
    cs += [candle(100.7, 100.9, 99.6, 99.9),
           candle(99.9, 100.2, 98.8, 99.4),     # sweep low
           candle(99.4, 100.9, 99.1, 100.7),    # reclaim
           candle(100.7, 103.2, 100.5, 103.0),  # displacement
           candle(103.0, 104.6, 102.4, 104.3),
           candle(104.3, 104.5, 102.9, 103.1),
           candle(103.1, 103.3, 102.0, 102.6),  # back into FVG -> entry
           candle(102.6, 104.8, 102.4, 104.6),
           candle(104.6, 106.4, 104.3, 106.1),
           candle(106.1, 108.9, 105.9, 108.5),  # takes buy-side liquidity
           candle(108.5, 108.7, 107.6, 107.9)]
    sweep_i = 13
    assert cs[sweep_i]["l"] < asia_lo and cs[14]["c"] > asia_lo
    assert fvg_bull(cs[14], cs[16])
    fvg_lo, fvg_hi = cs[14]["h"], cs[16]["l"]
    assert fvg_lo <= cs[18]["l"] <= fvg_hi
    bsl = 108.6
    assert cs[21]["h"] > bsl

    fig, ax = plt.subplots(figsize=(12, 6))
    draw(ax, cs)
    zone(ax, -0.5, 11.5, 97.2, 110, "#9e9e9e", alpha=0.08)
    ax.text(5.5, 109.6, "Asian session (range)", ha="center", fontsize=9, color=MUTED)
    zone(ax, 11.5, len(cs) - 0.5, 97.2, 110, NEUTRAL_ZONE, alpha=0.06)
    ax.text(17, 109.6, "London kill zone 2-5am ET", ha="center", fontsize=9, color=NEUTRAL_ZONE,
            fontweight="bold")
    hline(ax, asia_lo, -0.5, 15, "SSL: Asian low", color=MUTED, side="left", va="top")
    hline(ax, bsl, 4, len(cs) - 0.5, "BSL: previous-day high (target)", color=MUTED, side="left")
    zone(ax, 14, len(cs) - 0.5, fvg_lo, fvg_hi, BULL_ZONE, alpha=0.25)
    steps = [
        ((sweep_i, cs[sweep_i]["l"]), (14.6, 97.6), "2) SWEEP below Asian low"),
        ((15, 102.0), (16.0, 99.6), "3) DISPLACEMENT up, leaves FVG"),
        ((18, 102.2), (12.6, 105.0), "4) ENTRY: price returns to FVG\n    stop below sweep low"),
        ((21, cs[21]["h"]), (13.8, 107.6), "5) TARGET: buy-side liquidity"),
    ]
    for xy, txt_xy, t in steps:
        ax.annotate(t, xy, xytext=txt_xy, fontsize=9, fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color=INK))
    ax.text(0, 107.6, "1) HTF bias BULLISH and price in DISCOUNT\n    of the daily range",
            fontsize=9, fontweight="bold")
    ax.set_ylim(97.2, 110.2)
    ax.set_xlim(-1, len(cs) + 0.5)
    ax.set_title('The whiteboard "V": sweep sell-side -> displace -> FVG entry -> run buy-side')
    noaxis_x(ax, "15-minute candles  →")
    finish(fig, "09_full_model.png")


if __name__ == "__main__":
    chart_structure()
    chart_htf()
    chart_premium_discount()
    chart_liquidity_pools()
    chart_sweep()
    chart_displacement()
    chart_fvg()
    chart_ifvg()
    chart_sessions()
    chart_model()
