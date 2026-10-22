# Stock Trading Strategy Project

## Project Goal
Build two trading systems:
1. **Intraday System** - 1-2 high-confidence trades/day, 1% max risk
2. **Long-term Investment System** - Smart monthly investing, 5% risk tolerance

## Status: COMPLETE - All Systems Built, Tested, and CORRECTED

---

## IMPORTANT: Honest Backtest Results (Updated 2026-01-21)

### Previous Results Were WRONG
The original backtests showed amazing results (78% win rate, 7.77 profit factor) due to bugs:
- **Bug 1**: Entry at breakout level instead of actual close price (unrealistic fills)
- **Bug 2**: Capital tracking errors in short positions
- **Bug 3**: Missing slippage and commission costs

### Corrected Results - The Hard Truth

| Strategy | Avg Return | Win Rate | Profitable Stocks | Verdict |
|----------|------------|----------|-------------------|---------|
| ORB (Correct) | **-1.06%** | 25.8% | 0/15 | LOSING |
| Mean Reversion | -0.22% | 49.2% | 3/15 | LOSING |
| Trend Pullback | -0.76% | 37.8% | 2/15 | LOSING |
| First Candle | -3.44% | 32.2% | 0/10 | LOSING |
| Gap Continuation | -0.85% | 24.4% | 2/10 | LOSING |
| Extreme Moves (2%+) | 0.00% | 13.3% | 2/15 | BREAK-EVEN |
| VWAP Bounce | +0.07% | 55.6% | 1/1 | MARGINAL |
| **Ichimoku Cloud** | **+0.57%** | 38.3% | 7/15 | **BEST INTRADAY** |

### Why Intraday Trading Loses Money

1. **Transaction costs** - 0.1% slippage + 0.1% commission = 0.2% per round trip
2. **Gaps don't continue** - 50%+ of gaps reverse during the day
3. **Stops get hit on noise** - Markets are choppy
4. **Trend days are rare** - Only ~20% of days trend strongly

---

## What Actually Works

### 1. Long-term Investment (Smart SIP v2) - UPDATED 2026-01-21

**CRITICAL**: Previous "19.89% return" was TOTAL return over 5 years = only 3.7% annualized!
Now using proper XIRR (annualized) calculation:

| Strategy | Total Return | **XIRR (Annualized)** | Verdict |
|----------|-------------:|----------------------:|---------|
| Simple SIP | 20.76% | 7.53% | Baseline |
| Smart SIP v2 | 35.40% | **11.42%** | Good |
| **Aggressive Crash SIP** | 66.72% | **18.15%** | **BEST** |
| Buy & Hold (RELIANCE) | 58.16% | 9.62% | Reference |

**Aggressive Crash SIP Strategy:**
- Save 50% during bull markets (crash_score < 0.2)
- Invest 100% during normal markets
- Invest 150% + reserves during opportunities (crash_score 0.5-0.7)
- Go ALL IN during crashes (crash_score > 0.7)

### 2. Only Trade Extreme Moves (If You Must)
- Gap > 2% with confirmation = break-even
- Very few trades (3 in 60 days)
- Not worth the effort

---

## Files (Updated)

| File | Purpose | Status |
|------|---------|--------|
| `orb_backtest_correct.py` | Correct ORB backtest | **USE THIS** |
| `extreme_moves_strategy.py` | Extreme moves only | Best intraday |
| `ichimoku_strategy.py` | Ichimoku Cloud + EMA | **BEST INTRADAY** |
| `simple_intraday_strategy.py` | Simple strategies | For testing |
| `mean_reversion_strategy.py` | Mean reversion | For testing |
| `momentum_trend_strategy.py` | Momentum strategies | For testing |
| `intraday_system.py` | Original (FIXED) | Entry bug fixed |
| `intraday_system_fixed.py` | Fixed version | Capital bug fixed |
| `longterm_system.py` | Long-term investment | **RECOMMENDED** |
| `smart_sip_v2.py` | **Smart SIP v2** | **BEST - 18.15% XIRR** |

---

## Key Learnings (Updated)

1. **Most intraday strategies lose money** after realistic costs
2. **Previous amazing results were due to bugs** - always verify backtests!
3. **Gaps frequently reverse** - IDEA.NS had +7% gap that reversed -8%!
4. **Smart SIP works** - 19.89% return by investing more in dips
5. **Long-term > Intraday** - Buy and hold beats active trading
6. **Be skeptical of high win rates** - Usually means lookahead bias

---

## Recommendations

### For Intraday Trading
1. **Don't do it** - The math doesn't work after costs
2. If you must, use `ichimoku_strategy.py` (best performer: +0.57% avg)
3. Second choice: `extreme_moves_strategy.py` with 2%+ gaps
4. Expect marginal returns at best
5. Paper trade first for 3+ months

### For Long-term Investment
1. Use **Smart SIP** - Invest more when market is down
2. Use `longterm_system.py` for stock selection
3. Focus on quality stocks with good fundamentals
4. Don't try to time the market

---

## How to Run

```bash
# Correct ORB backtest (shows reality)
python orb_backtest_correct.py

# Extreme moves strategy (best intraday)
python extreme_moves_strategy.py

# Ichimoku strategy (new best intraday)
python ichimoku_strategy.py

# Long-term system (recommended)
python longterm_system.py
```

---

## Walk-Forward Analysis (WFA) Results

Walk-Forward Analysis is the **gold standard** for validating trading strategies. It prevents overfitting by testing on truly out-of-sample data.

### WFA Results (Train: 180 days, Test: 60 days)

| Strategy | In-Sample | Out-of-Sample | WF Efficiency | Verdict |
|----------|-----------|---------------|---------------|---------|
| RSI Reversal | +2.80% | **-0.07%** | -10.8% | OVERFIT |
| Breakout | -0.00% | **-0.34%** | 1.4% | NO EDGE |
| EMA Crossover | +0.98% | **-0.35%** | 74.6% | FAILS OOS |

### Key WFA Findings

1. **All strategies have negative OOS returns** - No real edge exists
2. **High IS + Negative OOS = Overfitting** (RSI Reversal: +2.8% IS -> -0.07% OOS)
3. **High WFE doesn't guarantee profits** (EMA: 74.6% WFE but still -0.35% OOS)
4. **Parameter instability** - Optimal parameters change every window

### Parameter Stability Analysis

| Strategy | Parameter | Most Common | Stability |
|----------|-----------|-------------|-----------|
| EMA Crossover | fast_period | 5 | 62% |
| EMA Crossover | slow_period | 20 | 48% |
| RSI Reversal | rsi_period | 14 | 35% |
| Breakout | lookback | 30 | 40% |

Low stability (<50%) indicates parameters that work historically don't persist.

---

## The Bottom Line

**Walk-Forward Analysis CONFIRMS: Simple strategies don't work after realistic costs.**

- In-sample performance is always better than out-of-sample
- Optimization creates the illusion of edge (overfitting)
- Even with adaptive parameters, OOS returns are negative

**Stick with long-term investing using Smart SIP.**

---

## Files (Updated with WFA)

| File | Purpose | Status |
|------|---------|--------|
| `SwingTrading/walk_forward_fixed.py` | Walk-Forward Analysis | **NEW** |
| `SwingTrading/advanced_strategies.py` | Advanced swing strategies | Tested |
| `longterm_system.py` | Long-term investment | **RECOMMENDED** |

---

## MACD 3-System Swing Trading Strategy (NEW - 2026-01-21)

### Overview
Implemented a comprehensive 3-system MACD strategy based on a professional trading methodology.

### The Three Systems

**System 1: Zero Line Rule (Trend Trading)**
- MACD above zero = only look for BUY trades
- MACD below zero = only look for SELL trades
- Avoid "chop zone" near zero line
- Only take crossovers FAR from zero line
- Wait 2-3 candles after crossover for confirmation

**System 2: Divergence Detection (Reversal Trading)**
- Bearish Divergence: Price higher high + MACD lower high = bearish reversal
- Bullish Divergence: Price lower low + MACD higher low = bullish reversal
- Confirmed with histogram signals

**System 3: Multi-Timeframe Analysis**
- Daily Chart: Overall trend bias (above/below zero)
- 4-Hour Chart: Setup identification (crossover/divergence)
- 1-Hour Chart: Entry trigger (histogram flip)
- ALL three timeframes must agree

### Histogram Confirmation
- **The Flip**: First bar of opposite color = momentum shift
- **Shrinking Tower**: Bars getting smaller = losing momentum
- **Zero Bounce**: Histogram bounces off zero = trend continuation

### Backtest Results (2 years daily data)

| Version | Avg Return | Profitable | Win Rate | Profit Factor | Key Changes |
|---------|------------|------------|----------|---------------|-------------|
| V1 (Original) | **-1.80%** | 5/15 (33%) | 32.2% | 0.90 | All signals |
| V2 (Strict) | **-1.02%** | 4/15 (27%) | 32.7% | 0.84 | Too strict, few trades |
| **V3 (Long-Only)** | **+0.39%** | 8/15 (53%) | 37.9% | **1.35** | **BEST VERSION** |

### V3 Configuration (Best Results)
- Long-only mode (no shorts - better for equities)
- Relaxed price structure threshold (5%)
- Partial profit taking at 1.5R (50%), trail rest
- Move stop to breakeven after partial
- 2R target, 2% risk per trade

### Signal Types Breakdown (V3)
| Signal Type | Count | % |
|-------------|-------|---|
| S2_Div+Level | 15 | 33% |
| S1_Trend+Hist+Level | 15 | 33% |
| S1_Trend+Hist | 14 | 31% |
| S2_Div | 1 | 2% |

### Exit Reasons (V3)
| Exit Reason | Count | % |
|-------------|-------|---|
| Stop Loss | 33 | 73% |
| Take Profit | 12 | 27% |

### Key Findings

1. **Long-only works better** for equities (53% profitable vs 33%)
2. **Partial profit taking** helps lock in gains
3. **Too strict = too few trades** (V2 had only 55 trades vs V3's 45)
4. **Divergences at key levels** are most reliable signals
5. **Still marginal edge** (+0.39% avg) - use with other confirmations

### Files

| File | Purpose |
|------|---------|
| `SwingTrading/macd_3system_strategy.py` | V1 - Original implementation |
| `SwingTrading/macd_3system_v2.py` | V2 - Strict dual-MACD version |
| `SwingTrading/macd_3system_v3.py` | **V3 - BEST (Long-Only with partials)** |

### How to Run

```bash
cd SwingTrading

# Run V3 (recommended)
python macd_3system_v3.py

# Run V1 (original)
python macd_3system_strategy.py

# Run V2 (strict)
python macd_3system_v2.py
```

### Recommendations for MACD Strategy

1. **Use as a FILTER** rather than standalone signal generator
2. Combine with price action and candlestick patterns
3. **Paper trade** for 3+ months before going live
4. Consider weekly timeframe for better signals
5. Focus on stocks with clear trends, avoid choppy stocks
6. **Expect losing streaks** - even best version has 37.9% win rate
7. Edge comes from discipline, not the indicator

---

## MACD 3-System INTRADAY Results (NEW - 2026-01-21)

### The Hard Truth: Intraday MACD Does NOT Work

Adapted the MACD 3-System strategy for intraday trading with:
- Faster MACD: 8/17/5 (vs 12/26/9 for swing)
- Trend MACD: 12/26/9 (simulates higher timeframe)
- 1-candle confirmation (faster entries)
- ATR-based stops: 1.5 ATR
- Max 3 trades per day
- Mandatory EOD exit at 3:15 PM

### Intraday Backtest Results (60 days)

| Timeframe | Avg Return | Profitable | Win Rate | Trades | Stop Loss % |
|-----------|------------|------------|----------|--------|-------------|
| **15-min** | **-0.46%** | 0/15 (0%) | 17.2% | 89 | 80.9% |
| **5-min** | **-0.94%** | 0/15 (0%) | 11.3% | 187 | 89.3% |
| Swing (Daily) | +0.39% | 8/15 (53%) | 37.9% | 45 | 73.3% |

### Why Intraday MACD Fails

1. **More noise = more false signals**
   - 5-min bars have too much random movement
   - MACD crossovers happen frequently but mean nothing

2. **Transaction costs kill profits**
   - 187 trades vs 45 trades for swing
   - 0.2% round-trip cost x 187 = 37.4% in costs alone!

3. **Stops get hit constantly**
   - 89% of 5-min trades exit via stop loss
   - Intraday volatility triggers even reasonable stops

4. **No time for trades to work**
   - Must exit by 3:15 PM
   - Can't hold winners overnight

5. **Win rate too low**
   - Need >50% win rate for 1:1.33 R:R to be profitable
   - Getting 11-17% win rate = guaranteed losses

### Intraday vs Swing Comparison

| Metric | Intraday (5m) | Swing (Daily) | Winner |
|--------|---------------|---------------|--------|
| Avg Return | -0.94% | +0.39% | **Swing** |
| Win Rate | 11.3% | 37.9% | **Swing** |
| Profitable | 0% | 53% | **Swing** |
| Stop Loss % | 89% | 73% | **Swing** |
| Trade Count | 187 | 45 | **Swing** (quality over quantity) |

### File

| File | Purpose |
|------|---------|
| `Intraday/macd_3system_intraday.py` | Intraday MACD implementation |

### Conclusion

**DO NOT use MACD for intraday trading.** The strategy:
- Works marginally for swing trading (+0.39% avg)
- Completely fails for intraday (-0.94% avg)
- Higher timeframes = better signals
- If you must trade intraday, use price action, not indicators

---

## MACD Multi-Timeframe (MTF) Strategy - BEST RESULTS (Updated 2026-01-22)

> **SUPERSEDED (2026-10-07):** MACD MTF V2 has no edge vs random entries and had a weekly lookahead bug. See the SwingSystem section at the bottom.

### The Winning Approach: Weekly + Daily Alignment

Added weekly timeframe as a trend filter on top of the daily MACD strategy.

**Hierarchy:**
- **Weekly Chart**: Trend bias filter (MACD above/below zero)
- **Daily Chart**: Setup + Entry (crossover/divergence + histogram)

### MTF V2 Backtest Results

| Version | Avg Return | Profitable | Trades | Description |
|---------|------------|------------|--------|-------------|
| Daily-Only (V3) | +0.39% | 8/15 (53%) | 45 | Baseline |
| **Strict MTF** | **+0.75%** | 9/15 (60%) | 43 | Weekly required |
| **Relaxed MTF** | **+1.64%** | **12/15 (80%)** | 83 | **BEST - Weekly optional** |

### Why MTF Works Better

1. **Weekly filter reduces false signals** - Only trade when higher TF agrees
2. **More context = better decisions** - Weekly shows the "big picture"
3. **Relaxed approach captures more setups** - Don't miss good daily signals
4. **Quality scoring** - MTF-aligned trades get higher position size

### Quality Levels (Relaxed MTF)
- **5+**: MTF aligned + Strong weekly trend + Histogram confirm
- **4**: MTF aligned + Weekly momentum aligned
- **3**: MTF aligned (weekly + daily same direction)
- **2**: Daily signal only (no MTF alignment) - smaller position

### Stock-by-Stock Results (Relaxed MTF)

| Stock | Return | Trades | Win Rate | MTF | Non-MTF |
|-------|--------|--------|----------|-----|---------|
| ICICBANK | +4.46% | 3 | 66.7% | 3 | 0 |
| TITAN | +4.97% | 4 | 50.0% | 0 | 4 |
| WIPRO | +4.36% | 4 | 50.0% | 1 | 3 |
| HDFCBANK | +3.81% | 4 | 50.0% | 4 | 0 |
| AXISBANK | +3.75% | 7 | 42.9% | 4 | 3 |
| BHARTIARTL | +3.62% | 2 | 50.0% | 2 | 0 |
| LT | +3.41% | 4 | 50.0% | 2 | 2 |
| BAJFINANCE | +2.77% | 8 | 37.5% | 5 | 3 |
| SBIN | +2.44% | 5 | 40.0% | 3 | 2 |
| SUNPHARMA | +2.34% | 5 | 40.0% | 3 | 2 |
| INFOSYS | +0.22% | 6 | 33.3% | 1 | 5 |
| MARUTI | +0.10% | 7 | 28.6% | 4 | 3 |
| TCS | -1.82% | 9 | 22.2% | 3 | 6 |
| ITC | -3.58% | 8 | 12.5% | 2 | 6 |
| RELIANCE | -6.27% | 7 | 14.3% | 4 | 3 |

### Key Insights

1. **Losing stocks have more non-MTF trades** (RELIANCE, ITC, TCS)
2. **Best performers often have 100% MTF alignment** (HDFCBANK, BHARTIARTL, ICICIBANK)
3. **Some stocks do well with daily-only** (TITAN: 0 MTF, +4.97%)
4. **Position sizing by alignment** is the key - not hard filtering

### Recommendations

1. **Use Relaxed MTF approach** - +1.64% avg return, 80% profitable
2. **Size positions by quality** - Full size for MTF-aligned, half for daily-only
3. **Don't skip good daily signals** - Some stocks don't align but still work
4. **Weekly as BIAS, not BLOCK** - Use for context, not hard filter

### Files

| File | Purpose |
|------|---------|
| `SwingTrading/macd_mtf_strategy.py` | MTF V1 (Weekly/Daily/4H) - too restrictive |
| `SwingTrading/macd_mtf_v2_strategy.py` | **MTF V2 - BEST (+1.64% avg, 80% profitable)** |
| `SwingTrading/macd_signal_scanner.py` | Real-time signal scanner |

### How to Run

```bash
cd SwingTrading

# Best strategy (MTF V2)
python macd_mtf_v2_strategy.py

# Real-time signal scanner
python macd_signal_scanner.py --all
```

---

## FINAL RECOMMENDATIONS (Updated 2026-01-22)

> **SUPERSEDED (2026-10-07):** MACD MTF V2 has no edge vs random entries and had a weekly lookahead bug. See the SwingSystem section at the bottom.

### For Swing Trading (BEST APPROACH)
1. **Use MACD MTF V2 (Relaxed)** - +1.64% avg return, 80% stocks profitable
2. Check weekly MACD for trend bias
3. Enter on daily signals, size by alignment quality
4. Use `macd_signal_scanner.py` for real-time signals

### For Long-term Investment
1. **Use Aggressive Crash SIP** - 18.15% XIRR
2. Save during bull markets, go all-in during crashes
3. Use `smart_sip_v2.py` for implementation

### For Intraday Trading
1. **AVOID** - All strategies lose money after costs
2. If you must, use Ichimoku (+0.57% avg) but expect marginal returns

### Summary Table

| Approach | Strategy | Avg Return | Profitable |
|----------|----------|------------|------------|
| **Swing Trading** | **MACD MTF V2** | **+1.64%** | **80%** |
| Long-term | Aggressive Crash SIP | 18.15% XIRR | - |
| Intraday | Ichimoku | +0.57% | 47% |
| Intraday | MACD | -0.94% | 0% |

---

## NEW: SwingSystem/ - Fresh Start (2026-10-07)  << CURRENT WORK

Goal: a system that monitors Nifty 100 with a set of strategies, prints ranked "high value calls"
in the console, logs every call to a journal BEFORE the outcome, and tracks outcomes (forward test).
Later: broker API for live checks. For now: yfinance, swing (daily), console output. See
`SwingSystem/README.md`. Run `cd SwingSystem && python main.py daily` after market close.

### Corrections to earlier findings (verified 2026-10-07)
1. **macd_mtf_v2_strategy.py had lookahead**: yfinance weekly bars are labelled by Monday, so Mon-Thu
   daily signals saw that week's Friday close. Fixed in `SwingSystem/core/indicators.py`.
2. **MACD MTF has NO entry edge**: vs random entries with identical exits on the same stocks:
   +2.52% vs +2.54% per trade (t=-0.1). The "+1.64% / 80% profitable" result is not real.
3. **Survivorship bias**: the current Nifty 100 list makes any long strategy look good historically.
   Judge strategies ONLY by edge over the random-entry control (`python main.py backtest`).
4. Vs a matched-hold random-entry control (monthly t-stat): only trend_pullback (RSI2 dip in uptrend)
   shows a candidate edge (+0.22%/trade, monthly t=3.2, beats control 68% of months). momentum_breakout
   (t=-1.1) and macd_mtf (t=1.0) do not -> flagged research_only (logged, not shown as calls).
   All 3 are negative in 2026 so far. Nothing is proven until the live journal confirms it.
5. `backtest_results.csv` (root) holds the old BUGGY ORB numbers - do not trust it.

### Research lab (2026-10-07) - `SwingSystem/lab.py`
- Frozen snapshot in `SwingSystem/data/lab/`. Train = 10 LAB_STOCKS 2016-2023; holdouts A (same stocks 2024+),
  B (other 90 stocks), B24 (other 90, 2024+). Every holdout view is logged in data/lab/holdout_log.csv.
- Sweep #1 (2,829 configs): only short-term DIP-BUYING entries (drop X% from N-day high, low RSI) beat random entries
  in the stage-1 screen (3-20 day time exits, which favours mean reversion; trend entries need a trailing-exit test). Promoted dip12_rsi50 + dip8_rsi2 (ranked) and 2 log-only configs
  via `strategies/lab_promoted.py`. Dip strategies do well in BEAR regime, badly in NEUTRAL -> next experiment.
- Sweep #2 (2026-10-08, 6,172 configs, 17 families incl. 9 candlestick patterns, Connors RSI, CCI, stochastic,
  squeeze, deep drawdowns): dip-buying is the only real edge; candles weak (best t 2.6); breakouts/EMA/squeeze
  no edge even with trailing exits. Dip strategies lose in NEUTRAL regime -> live set is now dip12_nn,
  crsi10_nn, crsi5_t5 (regime=not_neutral) + trend_pullback. Scanner shows Qty (1% risk) + correlation warning.
- Intraday lab (2026-10-08, `SwingSystem/lab_intraday.py`, hourly bars Nov-2023..Oct-2026, 468 configs, long+short,
  time-of-day matched control, 0.05%/side costs): NO intraday strategy survives holdouts (ORB, gap go/fade,
  prev-day high/low, open=low/high, last-hour momentum, intraday dip, VWAP). Confirms January conclusion.
  `main.py daily` now collects 5m+1h bars daily (data/intraday/) so 5-minute setups can be tested later.
- Data fix: overnight gaps >= 30% treated as unadjusted corporate actions (Yahoo missed VEDL demerger,
  TRENT, BAJFINANCE intraday split) - `core.data.fix_corporate_jumps`, applied to daily and intraday data.
- Angel One SmartAPI connected (data only, `SwingSystem/core/angel_data.py`, creds in SwingSystem/.env - never print).
  5m history from 2018; backfill to data/angel/5m running (~3 min/stock). Intraday lab now interval-generic
  (`lab_intraday.py ... --interval 5m`); freeze with `python lab_intraday.py freeze` once backfill completes.
- 5-minute intraday sweep (2026-10-09, Angel One 2018-2026, 85 stocks, 883 configs incl. 15/30/60-min ORB, gap at
  9:20/9:30/10:15, open=low/high, last-hour momentum, VWAP): NOTHING survives after 0.10% round-trip costs.
  Intraday is closed as a research direction unless costs drop; focus on daily dip strategies + live journal.
- 1m data (2026-10-14): `backfill('1m', symbols=['^NSEI'])` -> data/angel/1m/IDX_NSEI.csv (2018+). SmartAPI returns
  max ~8000 candles per request, so 1m chunks are 20 days (30 silently dropped 09:15-13:24 of each chunk's first day;
  26 days repaired). Angel's index feed lacks ~15:15-15:28 on many recent days (both 1m and 5m) - vendor gap.
- `ChartReplay/` (2026-10-14): local bar-replay chart (stdlib server + lightweight-charts 5.2.1): indicators, drawing
  tools, forming candles from 1m/5m ticks. `cd ChartReplay && python server.py` -> http://127.0.0.1:8050.
- `COMMANDS.md` (root): cheat sheet of every runnable command and the folder to run it from - keep it updated.
- `watch.py`: live watcher (Angel One quotes, 60 s) for journal calls during market hours - run in own terminal.
- Daily review loop (2026-10-10): journal now stores signal-time `features` + `strategy_version`, and track fills
  MFE/MAE, Nifty-over-window, post-exit 5d move and a diagnosis. `main.py review` (part of `daily`) writes
  data/reviews/YYYY-MM-DD.md: forward vs 2024+ backtest baseline (per signal day, verdict after 10 days),
  feature splits after 20 closed calls = lab hypotheses only. watch.py now ignores stale (weekend/holiday) quotes.
- Nifty options in ChartReplay (2026-10-15): option contracts are symbols `NIFTY_<YYYYMMDD>_<strike><CE|PE>` (`ChartReplay/options.py`):
  REAL = 1m candles + OI recorded by `SwingSystem/core/angel_options.record()` (part of `main.py daily`; Angel One serves
  history only for listed contracts, so it must run before each expiry), else SYNTH = Black-Scholes on Nifty with India VIX
  (`IDX_INDIAVIX` 1m, 2018+). UI: Options panel (chain at the replay clock, premium pane, buy-only CE/PE paper trades).
  Live mode (`ChartReplay/live.py`, Nifty + options only) polls Angel One in market hours; `server.py --no-live` disables it.
  Nifty lot is 65 (scrip master, 2026); weekly expiry Tuesday since Sep-2025, holiday -> previous trading day.
- Live trading assist (2026-10-17, `ChartReplay/assist.py`, DATA ONLY - no order placement yet): reads `/api/assist` from
  the ChartReplay server (one shared Angel One session; `live.depth()` = FULL quote bid/ask, `live.account()` = trade book +
  positions). Setup alerts (OPEN, GAP fail, TREND, RANGE, BREAKOUT; ~3/day calibrated over 60 days) 09:30-12:00, plus trade /
  discipline alerts from fills. User rules: max 2 trades/day, Rs 1000 daily loss cap, FEWER alerts. Checklist verdict GO only
  with a confirmed trend in that direction. In a session: Claude arms a Monitor on `SwingSystem/data/assist/alerts.log`
  (re-arm every 30 min), answers each alert in 2-3 lines, runs `python assist.py check CE|PE` when asked "CE now?".
  Alerts are attention filters, not a tested edge. Review of 16-Oct trades: `SwingSystem/trade_review_2026-10-16.md`.
  Workflow lives in `ChartReplay/RULEBOOK.md` (rules, session routine, roles, alerts) and roadmap in `ChartReplay/TODO.md`
  (stages 0-5 toward propose -> user confirms -> order). Keep both updated when the workflow changes (RULEBOOK has a change log).
  Angel One API key type is Trading; orders need the registered static IP (106.213.83.31 on 2026-10-17; this PC's IP is dynamic).
- First live test day 2026-10-19 (review: `SwingSystem/trade_review_2026-10-19.md`): data outages were Windows
  standby/sleep on battery, NOT Angel One (check the System event log, Kernel-Power 506/507/42, before blaming the API);
  `live.py` now keeps the PC awake in market hours. The Angel app does not share the SmartAPI rate limit. User rejected a
  "recovery trade after the loss cap" rule - the Rs 1,000 cap stays hard. `angel_options.record()` now also records
  the expiring contracts when run after the close on expiry day.
- Assist alerts/checklist backtested as a strategy (2026-10-20, `ChartReplay/alert_backtest.py`, Nifty 1m 2018-2026,
  2,162 days, live code, rules fixed before the run, results in SwingSystem/data/lab/alert_backtest/): NO EDGE.
  Gross per trade ~0 before costs (GO -0.35 pts, TREND -0.68, BREAKOUT -1.66, GAP -1.78), 0/9 years positive after
  costs, direction = coin flip vs same-bar random direction (t -0.56). Capped live rules: ~-Rs 535/day at stress costs.
  Alerts as attention filters (same-minute control on nearby days): RANGE -3% next-30-min range, TREND +4.5% range,
  +0.17 ATR drift - all below the pre-set 10% bar. Alerts are a discipline/attention aid only. Threshold tuning can't
  close a 7-10 pt cost gap; don't treat GO_fixed30 (t 2.07, ~1 pt) as a lead. Control "random bar same day, same
  direction" leaks the day's direction - never use it.
- Entries x exits sweep (2026-10-20, `ChartReplay/alert_tune.py`, 60 configs, premium P&L via BS incl. theta, train
  2018-22): 1/60 barely positive (+0.5 prem pts, t 0.3) -> holdout 2023+ -5.9/trade. Intraday option BUYING is closed.
- Nifty INDEX daily dip-buying (2026-10-20, `SwingSystem/lab_nifty_daily.py`, Yahoo daily 2007+, 125 configs):
  106/125 profitable after 0.08% costs on train, but NONE beats matched random long entries (best t_edge 1.4).
  Best (dip 3% from 10-day high, hold 10 days): holdout 2019-26 +1.0%/trade, 7/8 years positive, ~8 trades/yr, sum
  +65% vs buy-and-hold +114%. = Nifty's drift, not a timing edge. Not a reason to trade it over simply holding Nifty.
- India VIX vs the NEXT 21 days' realized Nifty vol (2018-2026): VIX higher on 80% of days, avg +2.5 vol pts, every
  year positive (2026: +0.9) -> option buyers overpay on average (why buying loses), sellers earn a premium, with
  crash risk (Mar-2020 gap -64 vol pts). Only structural edge found for Nifty options; needs a hedged-selling
  backtest on REAL option prices (skew, margin) before anything else.
- Nifty INDEX intraday search (2026-10-20, `SwingSystem/lab_nifty_intraday.py`, 1m 2018-2026, futures-style, 0.05%
  stress cost, pre-registration + amendment + views log in data/lab/nifty_intraday/): 16 families / 1,310 configs
  (ORB+NR7, gap, prev-day H/L, CPR, RSI2 MR, EMA/Supertrend, time-of-day, Gao intraday momentum, Zarattini noise
  area x3, prev-day reversal, calendar/expiry, midday, prev last hour, expiry pinning). NOTHING WORKS: 0 of 5
  mechanical finalists passed the single 2025-26 TEST look. Closest: REV fade th1.5 e15 x359 (fade a >=1.5% prior-day
  move 09:30->15:14) passed DEV/VAL/TEST/walk-forward but only 54 OOS trades, t 1.66. Noise-area momentum's
  2018-22 edge was the 2020 crash (ex-2020 ~0); all momentum families broke in 2023. 2023-24 is spent (browsed);
  2025-26 has now been looked at once - any new idea needs fresh forward data.
- Code audit of that search (2026-10-20, independent agent + sanity tests): no lookahead, fills/costs/signs correct
  (coin flip = -cost, perfect foresight = full move). Minor bugs fixed (gap-fade target already hit at entry booked as
  loss, REV/PREVLAST two-day return after dropped days, back-filled day open, neighbour sort, TREND family key);
  cache now matrix_v2.npz. 70% of configs are profitable BEFORE costs; median edge 0.02-0.07%/trade vs 0.035-0.05%
  cost -> costs + regime shifts are why nothing passes, not bugs. Post-audit second TEST look: GAP fade th0.3 e0
  x165 stop1.0 (fade a >=0.3% gap at the open, target prev close, stop 1x gap, exit 12:00) passes criteria 1,2,3,5,
  fails 4 (OOS 2023-26 t 1.29, +9.1% unlevered, 355 trades) - best paper-trade candidate with REV fade th1.5.
- LAB paper alerts (2026-10-20, `ChartReplay/lab_alerts.py`, wired into assist live/replay): GAPFADE, REVFADE and
  TRENDBAND (= NOISE3 L20 vm1.25 f30 both hivol, best trend variant, ~break-even OOS) fire `LAB` alerts and log every
  signal to SwingSystem/data/assist/lab_journal.csv (mode live/sim/replay). `verify` matches the lab engine exactly.
  PAPER ONLY (stops exceed the Rs 1,000 cap). Decision after 2-3 months: forward avg net > 0 and not worse than backtest.
  In a session, answer LAB alerts like the others (2-3 lines) and remind that they are paper.
- Trend + 9-EMA pullback lab (2026-10-20, `ChartReplay/pullback_lab.py`, pre-registered, results in
  SwingSystem/data/lab/pullback/): the user's setup = touch of the 1m 9-EMA in a confirmed trend, enter on resumption
  (RESUME1m; regression check matches the 16-Oct 10:12 winner). 24 entry x exit configs (limit at touch / resume,
  1m / 5m EMA, first pullback, clean-day filter, live trail / breakeven+EMA trail / 2R), premium P&L 1 strike ITM next
  expiry: 0/24 positive on train after 5.5 prem pts cost, 0/4 pass holdout. Best gross ~+1 premium pt/trade
  (RESUME1m first X2: holdout gross +1.05, net -4.45). Direction = coin flip vs same-bar random direction (all
  holdout vs_randdir_t < 1) AND gross ~= brokerage alone (Rs 62 = ~0.95 prem pts) -> no edge at any cost level.
  Gross shape is the user's idea (train: 42% win, avg win +11.4 vs avg loss -7.7 prem pts, max +181) but expectancy
  only ~+0.3. 2023+ holdout now viewed 3x for this family: new variants of this setup -> forward paper data only.
- PULLBACK alert + practice loop (2026-10-20, `ChartReplay/pullback.py`, `static/alerts.js`): the user chose to trade
  the pullback setup discretionarily and refine it with experience. PULLBACK fires in assist live/replay and in the
  ChartReplay Alerts panel (no lookahead: shown at bar close; Take/Skip within 3 min, logged to
  SwingSystem/data/assist/pullback_decisions.csv; rule paper trades in live -> pullback_journal.csv).
  `python pullback.py report` = the user's picks vs all signals. In a session, answer PULLBACK alerts in 2-3 lines.
- PULLBACK paper trades priced on the real ATM contract (2026-10-21): contract fixed at the signal (strike = close
  rounded to 50, expiry = assist_data.pick_expiry); journal has premium entry/exit, Rs/lot gross and net. 21-Oct 10:19
  PE: Nifty breakeven but premium -3.75 (Rs -306 net) - Nifty points overstate option results.
- User's own paper trades (2026-10-21): `SwingSystem/data/assist/user_paper_trades.csv` (contract, premium entry/stop/target/exit,
  Rs gross/net, risk). Log each one the user reports; compare with pullback_journal.csv (rule) over time.
- BASEBREAK lab (2026-10-21, `ChartReplay/basebreak.py`, pre-registered, train 2018-22 only, results in
  SwingSystem/data/lab/basebreak/): the user's "sideways base -> higher lows -> break -> first retracement -> enter,
  small stop, trail" setup (21-Oct 22800PE chart). 0/48 configs pass; best (30-min base <= 5 ATR, higher-lows
  structure, stop <= 15 pts) gross +0.3 premium/trade, net -5.2 after 5.5 cost, 0/5 years positive, direction = coin
  flip vs random (t 0.16). Structure filter adds ~+0.5 gross vs no filter - too small. Tight stops (median ~8 Nifty
  pts = ~4.5 premium) make the ~5.5 cost > 1R. Replay: 21-Oct 11:11 PE stopped in 1 min, 11:34 PE +82 premium;
  16-Oct 10:13 CE +38. Not wired as an alert (`python basebreak.py day YYYY-MM-DD` shows a day).
  Round 2 (same day, pre-registered, train only, `python basebreak.py lab2`, round2.csv): 0/14 pass. Runner exit (no
  breakeven, 15-bar swing trail, 180 min) -1.65..-0.56 gross; 3m bars +0.16..+0.23 (stops ~16 pts); 2nd retest
  +0.57 on 75 trades; day-direction filter WORSE (-1.05/-1.95, vs random t -2.1); all combined -1.6. Every variant
  gross within +/-2 premium of zero vs 5.5 cost. BASEBREAK closed as a mechanical rule.
- BASEBREAK practice alert (2026-10-21, user decision): replaces PULLBACK in assist live/replay and the ChartReplay
  Alerts panel (`pullback.SETUP`; `pullback.make_rule`). basebreak.BaseBreakRule = lab W30 k7 struct R25 XS
  (verify 1,464/1,464). Journal basebreak_journal.csv, decisions basebreak_decisions.csv, `python basebreak.py
  report`. Fires on 21-Oct 11:11 PE (stopped, Rs -758) and 11:34 PE (+77.5 premium). In a session, answer
  BASEBREAK alerts in 2-3 lines and remind that they are practice/paper. Restart server.py + assist.py to load it.
