# Rulebook: live Nifty options sessions

How we work during a trading session. Keep this short and current: when the workflow changes, change this file
and add a line to the change log at the bottom. Plans and ideas go in `TODO.md`, not here.

---

## 1. Trading rules (non-negotiable)

| Rule | Value | Enforced by |
|---|---|---|
| Max trades per day | **2** entries | checklist shows NO after the 2nd; DISCIPLINE alert |
| Daily loss cap | **Rs 1,000** net of charges (~Rs 62 per round trip) | warning at -Rs 700 booked; LOSS CAP alert at -Rs 1,000 |
| Setup window | **09:30-12:00** | no trades before 09:30 (opening range forming); setup alerts off after 12:00 |
| Instrument | Nifty weekly options, **ATM strike only**, 1 lot (65), **buy only** | checklist plan + PULLBACK alert name the ATM strike |
| Expiry | nearest; on expiry day the **next** one | `assist.py` picks it (`--expiry` to override) |
| Orders | **Limit** orders, placed **by you** in the Angel One app | the system places no orders (yet) |
| Direction | Only with a **confirmed trend** in that direction | checklist GO needs it |
| No chasing | Don't buy a spike or a premium already up 20%+ in 10 min | checklist WAIT |
| Same idea, higher price | Don't re-buy what you just sold at a higher price | checklist warns |
| Exits | Exit on a Nifty stop close, a trailing-stop hit, or a lost 9-EMA | trade alerts |

**GO means "fits the rules", not "will work".** If the chart disagrees, skip it. Rules can only be changed outside
market hours, never during a session.
---

## 2. Session routine

### Before 09:05
0. Laptop **on the charger, lid open** (on battery Windows goes to standby and the live data stops).
1. Keep the Claude Code session open.
2. Terminal 1: `cd D:\python\ChartReplay` then `python server.py`. The only Angel One login, so leave it running.
3. Terminal 2: `cd D:\python\ChartReplay` then `python assist.py live`. Keep it visible next to the chart.
4. Open the chart (ChartReplay: http://127.0.0.1:8050).
5. Tell Claude **"live"**. Claude starts watching `alerts.log`.
6. Do **not** run `SwingSystem/watch.py` at the same time (a second login makes them fight).

### 09:15-09:30
Watch only. Every checklist shows WAIT. The OPEN alert comes at about 09:30.

### 09:30-12:00
1. **Alert beeps.** Look at the chart. Claude posts 2-3 lines on what it means.
2. **Thinking of a trade?** Ask **"CE now?" / "PE now?"** while the setup forms, not at the click (the reply takes 10-60 s).
3. **GO** = all checks pass. **WAIT** = not yet (chasing, under the EMA, too early). **NO** = a rule blocks it.
4. Place a **limit** order yourself. No need to tell Claude: the trade book is read automatically (~30 s delay).
5. In the trade, act on the STOP and TRAIL alerts or your own read. Don't move the stop further away.

### After the last trade / 15:30
1. Press Ctrl+C in both terminals.
2. Tell Claude **"review today"** to get a write-up from your fills, the alerts and what the checklist said at each entry.
3. `SwingSystem/main.py daily` is fine after 15:30 as usual.

---

## 3. Who does what

| | Does | Doesn't |
|---|---|---|
| **You** | Watch the chart, decide, place and exit orders | Trade outside the rules |
| **`assist.py`** | Every 10 s: fresh data. Every minute: checklist line. Alerts when a condition *newly* becomes true (beep + notification + `alerts.log`) | Place orders, predict |
| **Claude** | Watches `alerts.log` (re-arms the watch every 30 min); explains each alert; runs the checklist on request; restates the plan on trade alerts; flags data problems; writes the review | Watch the chart or every minute line, place orders, override your decision |

Claude is idle between events, and works only while this session is open. The beeps and the checklist keep working without Claude.

---

## 4. Alerts

| Alert | Meaning | Typical action |
|---|---|---|
| `OPEN` | Gap vs prev close, opening range, VIX, expiry in use | Note the levels |
| `GAP` | The gap is failing: price closed through the opening range against the gap | Possible reversal; the opening-range level matters |
| `TREND` | Confirmed trend up/down (15- and 30-min moves, EMAs aligned) | Look for a pullback to the 9-EMA or a tight breakout on that side |
| `RANGE` | 30 min stuck in a band | Stand aside; buying options bleeds here |
| `BREAKOUT` | Close out of a tight 8-min box in the trend direction near the day extreme | Possible entry if the premium hasn't already spiked |
| `BASEBREAK` | Your setup since 21-Oct (replaces PULLBACK): a 30-min sideways base (<= 7 ATR) with higher lows (CE) / lower highs (PE), a close out of the base, a retest of the 9-EMA or the broken edge, then a close past the post-break extreme. Entry ~next open, Nifty stop beyond the retest (<= 25 pts), at +10 premium stop to entry then trail the 5-bar swing. Names the ATM contract + premium; a later `paper exit` line gives the real-premium result | **Practice:** take or skip in the panel within 3 min; checklist and Rs 1,000 cap still apply. No tested edge (`basebreak.py`: 0/62 configs, ~0 before costs). `python basebreak.py report` = your picks vs all signals |
| `PULLBACK` | (off since 21-Oct, `pullback.SETUP`) Your earlier setup: confirmed trend, Nifty touched the 1m 9-EMA and resumed. Entry ~next open, Nifty stop, exit plan. Names the exact contract (ATM at the signal, checklist expiry) and its premium. A later `paper exit` line gives the rule's result **on that contract's real premium** (Rs/lot, after charges) | **Your call**, take or skip; checklist rules still apply. Not a tested edge (`pullback_lab.py`: ~break-even before costs) |
| `CUTOFF` | 12:00, setup alerts off | Stop looking for new trades |
| `LAB` | Paper-tested rule fired or closed: GAPFADE (09:16), REVFADE (09:30), TRENDBAND (30-min checks, high-vol days only). Exact entry/stop/target/exit + suggested option | **Paper only** - logged to `lab_journal.csv` automatically; trade it only after the journal proves it (see `lab_alerts.py`) |
| `TRADE` | Entry seen (with Nifty stop and Rs risk) / trade closed (with day net) | Check that the stop is where you think |
| `STOP` | Nifty closed through your stop, or the 9-EMA crossed against you | Exit |
| `TRAIL` | +10 pts: trail armed / premium 15 below its high: trail hit | Hold / take it |
| `DISCIPLINE` | 2 trades done, over the limit, -Rs 700 booked, loss cap hit | Stop for the day when it says so |
| `STALE` / `DOWN` / `BOOK` / `OK` | Data stopped / server down / trade book unavailable / recovered | See section 6 |

`SIM` in front of an alert means it came from a test run, not the market.

---

## 5. The checklist (`python assist.py check CE|PE`)

| Check | Passes when |
|---|---|
| trend | Confirmed trend in this direction (range or opposite trend = NO; nothing confirmed yet = WAIT) |
| ema | Nifty above 9-EMA above 21-EMA (CE), mirrored for PE |
| stretch | Within 1.5 ATR of the 9-EMA (beyond = chasing) |
| spike | Last 1-min bar not over 2.5 ATR in your direction |
| premium | Premium not up 20%+ in the last 10 min |
| spread | Bid/ask spread small (live only) |
| trades / loss | Under 2 trades and inside the Rs 1,000 cap (trade book unavailable = WAIT) |
| risk | Stop (5-bar swing, at least 1 ATR away) x delta x lot + charges fits the loss budget left |
| time / same-idea | Warnings: before 09:30 or after 12:00; re-buying higher after an exit |

The plan line gives the strike, bid/ask, a limit price, the Nifty stop, the premium stop and the exit rule.

---

## 6. When something breaks

| You see | Do |
|---|---|
| `DOWN ... server not reachable` | Restart `python server.py` in terminal 1 |
| `STALE ...` | Live data stopped: restart the server; tell Claude if it persists |
| `BOOK ... trade book unavailable` | Count your own trades; the checklist shows WAIT until it's back |
| "Port 8050 already in use" | An old server is still running: close it first |
| No notification pop-ups | Beeps still work; tell Claude. `ASSIST_NO_TOAST=1` turns pop-ups off |
| GO but the chart looks wrong | Skip the trade |

---

## 7. Files

| File | What |
|---|---|
| `ChartReplay/assist.py`, `assist_data.py` | Checklist, alerts, replay, sim, calibrate |
| `SwingSystem/data/assist/alerts.log` | Every alert, one line each |
| `SwingSystem/data/assist/state.json` | Latest checklist and market state |
| `ChartReplay/TODO.md` | Roadmap |
| `COMMANDS.md` (root) | All commands |

---

## Change log
- 2026-10-17: first version. Max 2 trades, Rs 1,000 cap, few alerts, setup window 09:30-12:00, data only (manual orders).
- 2026-10-19: a "recovery trade after the loss cap" rule was considered and **rejected**: the Rs 1,000 cap stays
  hard (trade 2 on 19-Oct broke it; see `SwingSystem/trade_review_2026-10-19.md`). The TRADE entry alert now says
  "EXPIRING TODAY" when the filled contract expires that day. Laptop must be **plugged in, lid open**: the 19-Oct
  data outages were Windows standby/sleep on low battery, not Angel One; `server.py` now keeps the PC awake in market hours.
- 2026-10-20: on a holiday (Dussehra) Angel One quotes return the last session's prices, and the server built fake
  "live" bars from them. Now: no Nifty exchange candle by 09:20 = no session -> market closed, no quote bars.
  Check the NSE holiday list before starting a session.
- 2026-10-20: `LAB` alerts added (`lab_alerts.py`): the 3 closest misses of the Nifty intraday lab, as a forward paper
  test. Not proven (OOS t 1.3 / 1.7 / 0.6); their stops are wider than the Rs 1,000 cap, so they are PAPER ONLY.
  After the close: `python lab_alerts.py report`. Decide after ~2-3 months (~25-40 GAPFADE signals): keep a rule only
  if its forward average is positive after costs and not worse than the backtest. TREND/BREAKOUT stay attention aids.
- 2026-10-20: `PULLBACK` alert added (`pullback.py`, = pullback_lab RESUME1m/first/X2, verified 1,731/1,731 trades).
  Fires in `assist.py live`/replay and in the ChartReplay **Alerts** panel (revealed with the replay clock, pauses
  on each signal; Take/Skip within 3 min -> `pullback_decisions.csv`). Practise on past days in ChartReplay, then
  `python pullback.py report`: your picks vs the rule. Your selection counts only if 'take' beats all signals over 50+.
- 2026-10-20: **ATM strike only** (user decision). Checked on the 2018-22 PULLBACK trades: ATM vs 1 strike ITM gives the
  same expectancy (gross +0.30 vs +0.31 premium pts/trade); ATM wins/losses are ~13% smaller in points (delta ~0.5)
  but larger in % of premium, with the tightest spreads.
- 2026-10-21: PULLBACK paper trades are priced on the **ATM option contract** fixed at the signal (real 1m bar open at
  the entry/exit minute, else live ltp), not Nifty points. Signals/exits still decided on Nifty (verify 1,731/1,731).
  `pullback_journal.csv` gains contract, prem_entry/exit, prem_pts, rs_gross, rs_net, prem_src. Scrip-master download
  failures now fall back to the previous day's file (21-Oct: the download kept breaking and blocked live data).
- 2026-10-21: `BASEBREAK` replaces `PULLBACK` as the practice alert (user decision; `pullback.SETUP`, switch back by
  setting it to "PULLBACK"). Rule = basebreak.py W30 k7 struct R25 XS (verify 1,464/1,464 trades vs the lab). Paper
  trades -> `basebreak_journal.csv`, Take/Skip -> `basebreak_decisions.csv`. Backtest (train 2018-22, two
  pre-registered rounds, 62 configs): no version beats costs; it is a practice aid to test the user's selection.
