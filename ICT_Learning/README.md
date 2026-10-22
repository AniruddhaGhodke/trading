# ICT (Inner Circle Trader) — Learning Notes

Syllabus taken from a whiteboard photo. The terms and drawings on it are listed in
[00_terms_from_board.md](00_terms_from_board.md).

**Which "ICT"?** None of the terms on the board belong to Information & Communication Technology.
HTF, premium/discount, liquidity pools and sweeps, FVG/IFVG, displacement and a "London 2–5am"
session note all come from **Inner Circle Trader**. That is a discretionary price-action method taught by
Michael J. Huddleston, and its followers often call it "smart money concepts". This is an
inference from the terms; nothing on the board names it.

> Definitions change from one ICT educator to the next, and ICT himself has changed some over the years.
> These notes use the most common definitions and point out where people disagree.

## Reading order

The board's order is kept, with two changes:
1. **Prerequisites** are added at the start. Every later topic assumes you can read candles and find swing highs and lows.
2. **Displacement is taught before FVG/IFVG.** An FVG is created *by* displacement, so you need displacement first.

```mermaid
flowchart LR
    P[1 Prerequisites<br/>candles, swings, structure] --> H[2 HTF bias]
    H --> PD[3 Premium / Discount]
    PD --> LP[4 Liquidity Pool]
    LP --> LS[5 Liquidity Sweep]
    LS --> D[6 Displacement + Speed]
    D --> F[7 FVG / IFVG]
    F --> L[8 London 2-5am ET]
    L --> M[9 Putting it together<br/>the whiteboard V]
```

| # | File | Board term | Main picture |
|---|------|-----------|--------------|
| 0 | [00_terms_from_board.md](00_terms_from_board.md) | what was read from the photo | — |
| 1 | [01_prerequisites.md](01_prerequisites.md) | *(not on board)* candles, swings, BOS / MSS | `images/00_market_structure.png` |
| 2 | [02_htf.md](02_htf.md) | 1. HTF | `images/01_htf_bias.png` |
| 3 | [03_premium_discount.md](03_premium_discount.md) | 2. Premium / Discount | `images/02_premium_discount.png` |
| 4 | [04_liquidity_pool.md](04_liquidity_pool.md) | 3. Liquidity Pool | `images/03_liquidity_pools.png` |
| 5 | [05_liquidity_sweep.md](05_liquidity_sweep.md) | 4. Liquidity Sweep | `images/04_liquidity_sweep.png` |
| 6 | [06_displacement_speed.md](06_displacement_speed.md) | 6. Displacement + Speed | `images/05_displacement.png` |
| 7 | [07_fvg_ifvg.md](07_fvg_ifvg.md) | 5. IFVG / FVG | `images/06_fvg.png`, `images/07_ifvg.png` |
| 8 | [08_london_session.md](08_london_session.md) | red note: London 2am–5am EST | `images/08_sessions.png` |
| 9 | [09_putting_it_together.md](09_putting_it_together.md) | the two drawings: the whole sequence | `images/09_full_model.png` |

Topics 2–8 follow the same layout: **Definition → Why it matters → Example (with a picture) → Common
mistakes → Check yourself**. Topic 1 (prerequisites) and topic 9 (the capstone) are laid out differently.

Image numbers are one behind the note numbers (note 01 uses `images/00_…`). The table above shows which image goes with which note.

The four flowcharts are Mermaid code. **Read these notes in Markdown Preview Enhanced's preview**
(`Ctrl+Shift+P` → *Markdown Preview Enhanced: Open Preview to the Side*). On this machine VS Code's built-in
preview (`Ctrl+Shift+V`) shows Mermaid diagrams as blank boxes. PNG copies are kept in `images/flow_*.png`,
with their sources in `images/diagrams/*.mmd`. `python export_mermaid.py --apply` switches the notes to the PNGs
and `--restore` switches them back.

All diagrams come from `make_charts.py`, which uses hand-written prices, not market data.
The script asserts that every pattern it draws really is that pattern; for example, an FVG requires candle 1's high to be
below candle 3's low. Run `python make_charts.py` to regenerate them.

## A reality check before you start

ICT is a **discretionary** method. Two traders can look at the same chart and mark different
"liquidity" or "FVGs", and the method's claims about "smart money" are not independently verified.
In your own research under `D:\python` (see the root `CLAUDE.md`), every intraday strategy you
tested, across 883 configurations on 5-minute data, failed holdout tests after 0.10% round-trip costs.
Learn these concepts because they are a useful way to *describe* price action. Treat any trading rule
built from them as a hypothesis. Each concept here can be turned into a mechanical rule and tested with
`SwingSystem/lab_intraday.py` against a random-entry control before you risk money. Section 9 shows how.
