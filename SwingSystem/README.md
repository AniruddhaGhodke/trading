# Swing Signal System

Fresh start (2026-10-07). Scans the Nifty 100 every day after market close, ranks swing setups from
pluggable strategies, **logs every call before the outcome is known**, and tracks each call to its
exit. The journal's forward record, not the backtest, decides which strategies survive.

## Daily use (after 3:45 PM IST)
```bash
cd SwingSystem
python main.py daily        # = track (update open calls + scorecard), review, then scan (new calls)
```
**Which session are the calls for?** Always the NEXT session's open. `scan` only uses completed daily
bars (today's bar is ignored until 15:45 IST), so run it after the close; on a weekend it re-scans Friday
(calls for Monday's open, already logged -> shown as `dup`/`already active`). Running it during market hours
just re-derives yesterday's calls, whose entry (today's open) has already happened. `watch.py` never makes
calls - it only follows journal calls, and only raises alerts when a session is trading today.

## Daily review loop (why calls worked / failed)
`daily` runs `review` after `track`. It writes `data/reviews/YYYY-MM-DD.md`:
- **Signal-time snapshot** (`features` in the journal): RSI2/RSI14, ATR%, drop from 20d high, distance to
  SMA50/200, volume ratio, Nifty 5d return, regime, sector, how many signals fired that day. Recorded at
  scan time, so later analysis is not hindsight. `strategy_version` = hash of the rules.
- **Per closed call**: MFE/MAE (best/worst move while held, also in R), Nifty over the same window, the
  stock's move 5 sessions after the exit, and a diagnosis: target hit / gave back the move / stopped then
  recovered (stop too tight) / never worked (entry wrong) / market fell (beta) / time exit.
- **Forward vs backtest** per strategy (baseline = same rules since 2024, `data/reviews/baseline.json`,
  rebuilt automatically when a strategy's rules change; `review --baseline` forces it). Judged on EXCESS
  return vs Nifty over each trade, only on mature signal days (all calls of that day closed, so quick
  stop-outs don't bias it), one point per day; verdict after 10 days; z < -2 = demotion candidate.
- **Final verdicts** 5 sessions after each exit (did the price come straight back after the stop?).
- **Feature splits** once a strategy has 20 closed calls: return above vs below the median of each feature.
  These are hypotheses for `lab.py` (train -> holdouts), never direct edits to a live strategy.
During market hours (separate terminal, light on memory, Angel One quotes once a minute):
```bash
python watch.py            # live P&L, distance to stop/target, alerts (beep + data/watch_alerts.log)
python watch.py --all      # include research-only strategies
python watch.py --once     # one snapshot
```
Other commands:
```bash
python main.py scan                       # today's ranked calls, logged to data/journal.db
python main.py track                      # update outcomes + live scorecard
python main.py calls -n 30                # list journal entries
python main.py backtest                   # all strategies vs random-entry control
python main.py backtest --strategy trend_pullback --start 2020-01-01 --save
python main.py backtest --min-edge 0.05                 # change the per-trade edge floor (default 0.10%)
python main.py portfolio                  # live strategies as ONE account: top 3 new/day, equity curve, drawdown
python main.py portfolio --capital 1000000 --top-n 5 --start 2020-01-01 --strategies dip12_nn,crsi10_nn
python main.py portfolio --sizing equal --tie-break atr   # best ranking/selection variant found (still far below Nifty)
python -m pytest                          # test suite (~3 min; runs on a temp copy of data/, never downloads)
```

## Research lab (finding and tuning strategies)
```bash
python lab.py freeze          # snapshot prices into data/lab/ (research never reads the live cache)
python lab.py families        # entry/exit families and parameter grids
python lab.py sweep           # screen all entries -> sweep exits -> finalists -> holdouts
python lab.py test "rsi_dip(n=2,level=10,trend=sma200)" "fixed_pct(target=5,stop=5,hold=10)"
python lab.py test "<entry>" "<exit>" --holdout   # reveal holdouts (logged)
```
**Splits (fixed in config.py, don't change after seeing results):**
- **train:** 10 tuning stocks (one per sector), 2016-2023. Data is cut at 2023-12-31, so nothing later is visible.
- **A:** the same 10 stocks, 2024 onward (unseen time).
- **B:** the other 90 stocks, 2016 onward (unseen stocks).
- **B24:** the other 90 stocks, 2024 onward (unseen in both stocks and time).

**How a config gets judged:**
- Every config is compared to random entries with the same stop/target/hold ("edge%"). For rule-based exits, the control uses matched holding periods.
- Train results are only a ranking; with ~2000 configs tested, a high train t can appear by luck.
- A finalist is a CANDIDATE only if: it has a positive edge on A and B24, is significant on B (t>2), makes money after costs, and its neighbouring parameters also work (a plateau, not a spike).
- Candidates then get a plug-in in `strategies/` and are forward-tested in the journal. That's the only truly unseen data.
- Every holdout look is logged in `data/lab/holdout_log.csv`. Re-tuning after looking turns holdouts into training data.

To add an idea, add a `Family` to `lab/families.py` (entry: a boolean signal; exit: stop/rule/ExitPlan).

## Layout
| Path | Role |
|---|---|
| `config.py` | Costs (0.15%/side), entry gap filter, paths. One place for all assumptions |
| `core/data.py` | yfinance download + CSV cache; drops today's still-forming bar |
| `core/indicators.py` | SMA/EMA/RSI/ATR/MACD; weekly mapping **without lookahead** |
| `core/regime.py` | Nifty regime (BULL/NEUTRAL/BEAR) + index returns for relative strength |
| `core/execution.py` | **Single trade simulator used by backtest, live tracker and lab** |
| `lab/`, `lab.py` | Research lab: frozen snapshot, families/grids, sweep engine |
| `core/backtest.py` | Universe backtest + random-entry control |
| `core/journal.py` | SQLite call log (immutable signal fields, outcome fields updated) |
| `strategies/` | Plug-ins. Add a class, register it in `strategies/__init__.py` |

## Execution rules (identical in backtest and live)
Signal on day T close -> entry at T+1 **open**. Skip if T+1 opens at/below the stop or gaps up more
than 1 ATR. Stop touched -> exit at stop (or at the open if it gapped through). Stop and target in
the same bar -> assume the stop hit first. Costs of 0.15% per side.

## Adding a strategy
Subclass `Strategy`, implement `prepare(df, ctx)` returning `signal, score, stop, atr, exit_signal,
reason` columns, set an `ExitPlan`, and add it to `ALL_STRATEGIES`. Then run
`python main.py backtest --strategy <name>`. It is labelled EDGE only if it beats the random-entry
control with monthly t > 2.5 (`config.EDGE_MIN_T`), by at least 0.10%/trade (`config.MIN_EDGE_PER_TRADE`,
a third of the round-trip cost), AND makes money after costs - and it should do so in most years before
it's worth ranking. Until then set `research_only = True`. Then check `python main.py portfolio`: a per-trade
edge does not guarantee a profitable account once capital and the top-N rule bind.

## Backtest results (2016 - Oct 2026, Nifty 100, net of costs)

Each strategy is compared to a **control**: the same number of random entries on the same stocks,
with the same initial stop, held for the same distribution of days. Significance is measured on
monthly differences, because trades within a month are correlated.

| Strategy | Trades | Avg/trade | Control | Edge | Monthly t | Beats control | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| trend_pullback (RSI2) | 6512 | +0.13% | -0.10% | +0.22% | 3.19 | 68% of months | **Ranked - candidate edge, pending forward test** |
| momentum_breakout | 1797 | +1.14% | +0.75% | +0.39% | -1.10 | 43% | research only (no edge) |
| macd_mtf (ported V2 relaxed) | 3514 | +2.52% | +1.82% | +0.70% | 0.99 | 51% | research only (no edge) |

How to read this:
- **Raw returns are inflated by survivorship bias.** Today's Nifty 100 members are, by construction,
  stocks that went up, so even random entries made money. Only the **edge over the control** counts.
- **MACD MTF has no entry edge.** Its earlier "+1.64%" came from survivorship, the exits, and a weekly
  lookahead bug (fixed here).
- **trend_pullback's edge is thin.** +0.22% per trade, against 0.30% round-trip costs that are
  already deducted. All three strategies are negative in 2026 so far.
- `research_only` strategies are still scanned and logged to the journal, but never shown as calls.

## Lab sweep #1 (2026-10-07): 2,829 configs, 171 entries x 143 exits

**Finding: in Nifty large caps, buying sharp short-term drops beats random entries.** Trend, breakout and crossover entries didn't pass
stage 1. But stage 1 screens with short time exits (3-20 days), which favours mean reversion by design.
Trend entries deserve a separate test with trailing exits before being written off.

Promoted to live forward testing (`strategies/lab_promoted.py`):

| Name | Entry | Exit | Holdouts | Status |
|---|---|---|---|---|
| dip12_rsi50 | close 12% below its 20-day high close | RSI14 > 50, 3xATR stop, max 10 days | B +0.38%/trade (t=4.7), B24 +1.03% (t=4.0), **A -0.85% (41 trades)** | ranked |
| dip8_rsi2 | close 8% below its 10-day high close | RSI2 > 70, 3xATR stop, max 60 days | B +0.37% (t=5.5), B24 +0.89% (t=4.0), A +0.03% | ranked |
| rsi5_dip | RSI5 < 20 | RSI2 > 70, 2xATR stop, max 10 days | only 6/11 years positive | log only |
| rsi2_atr_1r | RSI2 < 20 | 3xATR stop, 1R target, max 10 days | A negative, B24 barely positive | log only |

Caveats:
- The best dip configs had **no stop** and lost up to 57-71% on a single trade (crash names). The promoted version
  adds a 3xATR stop, chosen on train only.
- Survivorship bias flatters dip-buying the most: today's index members are the ones whose dips recovered.
  B24 (2024+, unseen stocks) is the least biased number.
- Quick fixed targets (+3%/+5% in a few days) were tested; their finalists lost money after costs.
- Stops are wide: 7-10% of price. Size positions so a stop-out costs about 1% of capital.
- **Regime matters (not yet tuned):** dip strategies made +1.3 to +2.2%/trade when the Nifty regime was BEAR and
  lost -1.2 to -1.4%/trade in NEUTRAL (just after the market breaks down). The ranker's BEAR -20 penalty is wrong
  for these strategies. Next experiment: add a regime filter to lab entry families and test it on train first.

Note: data/lab snapshot (frozen 2026-10-07) still contains ~7 zero-volume holiday bars per stock that the
live downloader now drops. Effect is tiny. Keep the snapshot frozen for reproducibility.

## Lab sweep #2 (2026-10-08): 6,172 configs, 17 entry families

New families: 9 candlestick patterns (hammer, engulfing, piercing, morning star, three white soldiers,
outside reversal, doji, NR7 breakout, inside-bar breakout), Connors Double 7s, Stochastic, CCI, Connors RSI,
Bollinger squeeze breakout, distance below SMA, and deep 6/12-month drawdowns. Stage 1 now also screens with
60/120/250-day holds and SMA20/SMA50 trailing exits, so trend and breakout entries got a fair test.

Best train t per family (higher = stronger, ~3+ is interesting):

| Family | best t | Verdict |
|---|---:|---|
| drop_from_high, below_sma, connors_rsi, rsi_dip | 3.9 - 4.2 | **short-term dip buying = the only real signal** |
| down_days, stoch_low, deep_drop, double7, cci_low, bb_lower | 2.7 - 3.0 | same dip effect, weaker versions |
| candle (best: hammer after a 3% pullback) | 2.6 | weak. Candles add little beyond "it dropped" |
| ibs_low, sma_cross_up | 2.1 - 2.4 | weak |
| squeeze_break, breakout, ema_cross | 1.1 - 1.5 | **no edge**, even with trailing exits |

Market regime: on train, every dip finalist made money in BEAR and BULL but lost in NEUTRAL (Nifty just
broken below its 200-day average, i.e. a falling knife). Adding `regime=not_neutral` improved average return per
trade on holdouts A, B and B24 (tested once).

Live strategy set after sweep #2 (`strategies/lab_promoted.py`):

| Name | Rule | avg/trade A / B / B24 | Status |
|---|---|---|---|
| dip12_nn | 12% below 20d high close, not NEUTRAL; exit RSI14>50, 3xATR stop, 10d | -0.36 / +0.65 / +1.01% | ranked |
| crsi10_nn | Connors RSI < 10, not NEUTRAL; exit RSI2>70, 2xATR stop, 10d | +0.15 / +0.44 / +0.48% | ranked |
| crsi5_t5 | Connors RSI < 5, not NEUTRAL; +5% target, 8% stop, 5d | +0.53 / +0.19 / +0.57% | ranked |
| trend_pullback | RSI2 < 10 above rising SMA200 | (original) | ranked |
| dip12_rsi50, dip8_rsi2, rsi5_dip, rsi2_atr_1r | earlier versions | - | log only |

Scanner changes: one row per stock (other agreeing strategies listed); position size (Qty) from
config.CAPITAL / RISK_PER_TRADE; warning when more than MAX_NEW_POSITIONS new calls fire on one day (dip
signals cluster in selloffs, and often in one business group, e.g. three Adani stocks on 2026-10-08).
The ranker's BEAR -20 penalty was removed; regime is now handled inside strategies.

## Intraday lab (2026-10-08) - `python lab_intraday.py`

Data: yfinance gives ~3 years of HOURLY bars (used for research) and only ~60 days of 5-minute bars.
`python main.py daily` now appends 5m + 1h bars to data/intraday/ every day, so 5-minute history grows.

Data fixes found while building this (they also apply to the daily system):
- Yahoo intraday bars are not reliably split-adjusted (BAJFINANCE June-2025 split showed as a -80% hourly bar),
  and Yahoo DAILY data is inconsistent too (VEDL demerger 2026-04-30 shown as -65%, TRENT 2026-01-01 as -33%).
  Any overnight gap >= 30% is now treated as a corporate action and earlier prices are rescaled
  (`core.data.fix_corporate_jumps`). Without this, dip strategies would "buy" a demerger as a 65% dip.
- Diwali Muhurat evening sessions and incomplete days are removed from intraday data.

Method: same as the daily lab, adapted. Train = 10 lab stocks Dec-2023..Jun-2025, A = same stocks Jul-2025+,
B = other 90 stocks, B25 = other 90 from Jul-2025. Long and short. Exit by 15:15 at the latest. One trade per
stock per day. Control = random entries at the SAME time of day, same direction, same exits. Costs 0.05%/side
(0.10% round trip, realistic for liquid large caps) with a 0.10%/side stress test.

Families (62 configs x 20 exits): first-hour range breakout (ORB), gap-and-go / gap-fade, previous-day
high/low break, open=low / open=high, last-hour momentum/reversal, intraday dip/rip from the open, VWAP cross.

**Result: nothing survives.** All 10 finalists lost money on the 90 unseen stocks (B) and in the latest
period (B25), even at 0.10% round-trip cost. Random intraday entries lose about the cost (~-0.1%/trade);
the best strategies beat random by only ~0.05-0.1%, which is less than costs. This confirms the January
conclusion with a better method: **on Nifty 100 hourly data there is no intraday edge in these classic setups.**
Not tested yet (need 5-minute history): 15/30-minute ORB, VWAP on 5m bars, opening-range scalps.

### 5-minute intraday sweep (2026-10-09, Angel One 2018-2026, 85 stocks, 883 configs)

Data cleaning for raw broker candles: overnight moves are compared with Yahoo's adjusted daily
close-to-close moves on the same session calendar; a mismatch > 3% is treated as a corporate action
(found e.g. POWERGRID 1:3 bonus, BEL/CANBK/HAL splits, GAIL/IOC bonuses). Then the 30% backstop.

**Result: nothing survives** (same as hourly). Random 5-minute entries lose ~0.1%/trade (the cost);
the best setups beat random by only 0.03-0.14%, and every finalist is negative after costs on the
holdout stocks or periods. Closest to working: fading a >=1% opening GAP-DOWN at 9:20 once price turns
back up (long side: +0.06% B, +0.14% B25 per trade) - the same mean-reversion effect as the daily dips,
but too small to pay intraday costs reliably and negative on holdout A. Not promoted.

## Angel One SmartAPI (data only) - added 2026-10-08

- `core/angel_data.py`: login with TOTP, instrument tokens from Angel's scrip master, chunked candle download.
  Credentials live ONLY in `SwingSystem/.env` (ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET).
  No orders are placed through the API (SEBI 2026 rules require a static IP + strategy tags for API orders).
- The PC clock was ~150 s slow, which made every TOTP invalid; the connector now corrects TOTP time from
  internet time. Syncing Windows time is still recommended.
- 5-minute history is available from 2018. Backfill (resumable, slow: ~3 min/stock):
  `python -c "from core.angel_data import backfill; backfill('5m')"`
  `python main.py daily` tops it up each day.
- Then: `python lab_intraday.py freeze` (cleans + fixes corporate actions, broker candles are RAW prices)
  and `python lab_intraday.py sweep --interval 5m` (15/30/60-min opening ranges, gaps at 5/15/60 min, etc.).

## Known limitations / next steps
1. Survivorship bias: need point-in-time index membership, or judge by edge-vs-control only.
2. Portfolio simulation exists (`main.py portfolio`, core/portfolio.py; assumptions in its docstring). First run
   (2026-10-11, 4 live strategies, Rs 5L, top 3/day): CAGR -2.4% (A-Z tie-break; -2.3..-0.9% over 10 random
   tie-break seeds, `--tie-seed`), max drawdown -37..-44% vs Nifty +10.7% CAGR. The ranker
   favours trend_pullback (higher scores, weakest strategy) and the top-N cap cuts off selloff-cluster days, where
   dip candidates average +1.3%/trade (days with <=6 signals average about -0.1%). Ranking/selection is the next problem.
   Ranking/selection study (2026-10-11; train 2016-2020, test 2021-2026, 5 tie seeds each; options in `main.py portfolio`):
   `--rank ev` (walk-forward strategy EV), `--tie-break atr`, `--select budget|all`, `--sizing equal --position-pct`.
   The real culprit was SIZING: risk sizing (1%/stop) gives low-volatility trades the biggest positions and those do worst
   (size-vs-return correlation negative in every strategy). Best consistent variant: `--sizing equal --tie-break atr`
   (train +2.4%, test +0.9% CAGR vs baseline -2.9% / -2.3%; full period +0.2..+1.6%; still -1.2% without 2020).
   EV ranking and budget selection did not help; `--select all` won only on test with -52% train drawdowns.
   NONE comes close to Nifty buy & hold (+12.4% train, +8.6% test). Point-in-time membership is not implemented,
   so all of these are probably still flattering.
3. Only one strategy is ranked today. Priority: find more candidates that beat the control.
4. The ranker's confluence bonus is an untested guess. Validate it with the journal.
5. Next: live price watcher on Angel One WebSocket for stop/target/entry alerts on open journal calls.
