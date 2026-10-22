# TODO: toward semi-automated trading (you confirm every order)

Goal: the system proposes a trade, you confirm it, and only then is an order placed. Safety limits live in code,
not in anyone's judgement in the moment. Each stage must prove itself before the next one starts.
Session rules and routine: `RULEBOOK.md`. This is a discipline tool, not an edge: the intraday lab found no
strategy that survives costs.

Legend: `[ ]` open · `[x]` done · `[~]` in progress

---

## Stage 0 - Live check (week of 19-Oct-2026)
- [~] First live session with `assist.py live` (19-Oct): bars arrive every minute **while the PC is awake**.
      All 4 data outages (10:17, 10:31-10:38, 12:02-12:08, 12:51-13:43) line up with Windows standby/sleep on
      battery (Kernel-Power 506/507/42, "Critical Battery Trigger" 12:02) or a Wi-Fi driver reset (Netwtw14 7021).
      Fixed: `live.py` keeps the PC awake in market hours. Re-check on a session with the laptop plugged in.
- [x] `check CE` shows live bid/ask (19-Oct)
- [x] Trade book picks up real fills within ~30 s: both entries and exits alerted in the same minute (19-Oct)
- [x] Windows notification pop-ups appear (confirmed 19-Oct)
- [ ] Note late, noisy or missing alerts and fix them. 19-Oct: 1 setup alert (TREND 11:39) - quiet as wanted; OPEN
      alert missed because the PC was asleep until 09:38 and the scripts started 09:43-09:44
- [x] Windows clock: can't be synced on this PC (~160 s slow) - the code's internet-time correction is the permanent fix
- [x] "Rate limit shared with another Angel One process" was a guess baked into the message, not a finding.
      Both rate-limit hits came right after waking from standby (catch-up burst). The Angel app/web terminal
      don't use the SmartAPI key, so using the app for charts and orders is fine. Message fixed (19-Oct)
- [x] Warn in the TRADE alert when the filled contract is the expiring one on expiry day (both 19-Oct trades were) - 2026-10-19
- [x] Recovery-trade-after-cap rule: rejected 19-Oct, the Rs 1,000 cap stays hard

**Moves on when:** data, bid/ask and the trade book behave live for a few sessions.

## Stage 1 - Proposals and journal
- [ ] Save the checklist (GO/WAIT/NO + reasons) automatically at each entry fill
- [ ] At GO, or on request, write a concrete **proposal**: contract, 1 lot, limit price, Nifty stop, premium stop,
      trailing-stop rule, ID, valid ~90 s
- [ ] Log every proposal with your answer (yes/no) and the outcome
- [ ] Log what Nifty and the premium did 15 and 30 min after each alert (alert usefulness)
- [ ] Daily review written automatically at 15:30 (fills + alerts + checklist at entry)

**Moves on when:** proposals look sensible to you for about a week.

## Stage 2 - Paper execution
- [ ] Confirm a proposal by typing its ID in the `assist` terminal -> paper fill at the ask/limit
- [ ] Paper trades go into the same trade tracker (limits and alerts work the same)
- [ ] Stop and trailing-stop exits proposed and confirmed on paper
- [ ] Optional: feed ChartReplay's paper CE/PE trades into the same tracker

**Moves on when:** about 10 sessions without a bug, and you trust the workflow.

## Stage 3 - Static IP + order module
- [ ] **Static IP** (independent of everything above, start any time):
  - [ ] Ask the internet provider whether a static IP is available (and its cost)
  - [ ] If not: a small Mumbai cloud server with a fixed IP, used only as a proxy for order requests
        (SmartConnect accepts `proxies`); register it in the empty Secondary IP slot
  - [ ] Verify Angel One's current rules: SmartAPI docs and forum (smartapi.angelone.in), Angel One support,
        the SEBI circular on retail algo trading (Feb 2025, sebi.gov.in)
  - Facts as of 2026-10-17: API key type = **Trading**; registered Primary IP 106.213.83.31; this PC's IP was
    106.213.81.156 (dynamic). IPs can be changed once a week.
- [ ] `orders.py`, separate and small, with hard limits:
      Nifty options only · buy to open, sell to close (no shorts) · 1 lot · limit only · price within a few points
      of the ask · max 2 entries/day · Rs 1,000 cap · market hours · no duplicates · kill-switch file ·
      **dry-run by default** · every order logged
- [ ] Confirmation = the proposal ID typed by you (Claude may relay, never decide)
- [ ] Automated tests, including the 16-Oct replay as a regression test
- [ ] First real test: a limit order far from the price (won't fill), then cancel it
  - 19-Oct 20:31: AMO BUY 1 lot NIFTY27OCT2622850CE @ Rs 5 -> **rejected AG7002 "106.213.81.50 is not a registered
    IP"** (registered 106.213.83.31). Order path, login and params are fine; only the static IP blocks it. Nothing
    reached the order book. Test script: re-create from this note (place -> orderBook -> cancelOrder(oid, "AMO")).
  - 19-Oct 20:36, after the user registered 106.213.81.50: variety AMO -> AB1007 "should be NORMAL, STOPLOSS, ROBO";
    variety **NORMAL** -> accepted (order 1019326d0e34AO, Angel turned it into an AMO after hours), status open,
    then cancelOrder(oid, "NORMAL") -> **cancelled**. Place + read back + cancel works. Still blocked whenever
    the home IP changes (dynamic) - the static IP item above is still needed.
  - The SmartAPI SDK logs full request headers, including the API key, to the console and to `logs/` on errors.

**Moves on when:** the far-limit test passes from the registered IP.

## Stage 4 - Live, 1 lot
- [ ] Your confirmation places the entry **and an exchange stop-loss order** straight away (protection that doesn't
      depend on this PC, the script or Claude)
- [ ] Trailing-stop and EMA exits proposed -> you confirm
- [ ] A month of live journal before changing size or rules

## Stage 5 - Later (only if the data justifies it)
- [ ] Confirm button + chart overlay in ChartReplay (trend state, alerts, stop level, verdict)
- [ ] Tune thresholds on journal data (chase 20%, stretch 1.5 ATR, stop 5 bars/1 ATR, trail +10/-15) vs random entries
- [ ] Let you set your own stop (`assist.py stop 23050`)
- [ ] Exact charges from the contract note / API instead of the ~Rs 62 estimate
- [ ] Tick-by-tick prices (Angel One WebSocket) if trade alerts prove too slow
- [ ] Expiry-day rules, if expiry-day options are ever traded
- [ ] Optional status note from Claude every ~15 min (off by default; noise vs value)

## Backtest of the alerts (2026-10-20)
- [x] `alert_backtest.py`: the checklist GO / TREND / BREAKOUT / GAP entries with the live exits, Nifty 1m 2018-2026 ->
      **no edge** (gross ~0 before costs, every year negative after costs, direction = coin flip). Alerts mark
      slightly more active (TREND +4.5% range) or quieter (RANGE -3%) periods - too small to matter.
      Consequence: Stages 3-4 (auto orders) only make sense for a rule that passes the lab; the assist stays a
      discipline tool (limits, stops, expiry choice, journal).
- [x] `pullback_lab.py` (2026-10-20): the user's trend + 1m 9-EMA pullback/resume setup, 24 configs -> 0 positive on train,
      0/4 holdout; direction = coin flip, gross ~ brokerage. New variants only on forward paper data.

## Lab rules as paper alerts (2026-10-20)
- [x] `lab_alerts.py`: GAPFADE, REVFADE, TRENDBAND fire `LAB` alerts in `assist.py live`/replay and write every signal
      to `SwingSystem/data/assist/lab_journal.csv`; `verify` reproduces the lab engine exactly (2023-26: 355 / 53 / 289)
- [ ] Forward test 2-3 months; `python lab_alerts.py report` weekly. Decision rule fixed now: keep a rule only if
      forward avg net > 0 after 0.05% cost and not worse than its backtest OOS average
- [ ] Risk: the rules' stops are wider than the Rs 1,000 cap (gap fade ~Rs 4-6k/futures lot) - decide sizing/cap
      only with forward data
- [ ] Then Stage 1-2: proposal card + Place button on the chart (dry-run first), Stage 3-4 with the droplet IP

## Don't (for now)
- Add more indicators or alert types: new ones must come from what the alert log shows.
- Change rules during a session.

---

## Done
- [x] 2026-10-16 trade review (`SwingSystem/trade_review_2026-10-16.md`)
- [x] 2026-10-19 trade review (`SwingSystem/trade_review_2026-10-19.md`)
- [x] `assist.py`: live checklist, setup / trade / discipline / data alerts, replay, sim, calibrate (~3 setup alerts/day)
- [x] `/api/assist` server route (shared Angel One session, bid/ask, trade book)
- [x] Monitor wiring tested; real Angel One read calls tested (2026-10-17)
- [x] `RULEBOOK.md` + this file (2026-10-17)
