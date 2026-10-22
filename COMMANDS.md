# Commands cheat sheet

Each block says which folder to run it from. Long downloads are best run in their own terminal.

## Chart Replay (bar-replay practice chart)

```
cd D:\python\ChartReplay
python server.py                 # then open http://127.0.0.1:8050   (Ctrl+C to stop); live Nifty/options in market hours
python server.py --no-live       # without the live Angel One feed (e.g. while a long download runs)
python -m pytest tests -q        # tests
```
- Restart the server after downloading new data (each file is read once and cached).
- "Port 8050 is already in use" means an old copy is still running: close that terminal, or run
  `Get-NetTCPConnection -LocalPort 8050 | % { Stop-Process -Id $_.OwningProcess }` in PowerShell.
- Shortcuts: Space play/pause · ←/→ step (Shift = whole candle) · B buy · S sell · X close trade · P patterns on/off ·
  Alt+T/H/V/R drawing tools · Shift+click measure · Delete removes the selected drawing · Esc cancel.

## Live trading checklist (Nifty options, data only - never places orders)

Needs the Chart Replay server running (it owns the Angel One session; don't run `watch.py` at the same time).
Session rules and routine: `ChartReplay/RULEBOOK.md` · roadmap: `ChartReplay/TODO.md`.

```
cd D:\python\ChartReplay
python server.py                          # terminal 1 (from ~09:05)
python assist.py live                     # terminal 2: checklist line every minute + beeping alerts
python assist.py check CE                 # full CE checklist + plan (or PE) - what Claude runs on "CE now?"
python assist.py replay 2026-10-16 --trades ..\SwingSystem\data\assist\trades_2026-10-16.csv --at 10:12
python assist.py live --sim 2026-10-16T09:25 --sim-speed 0.05 --trades ..\SwingSystem\data\assist\trades_2026-10-16.csv
python assist.py calibrate --days 60      # setup alerts per day (target ~3)
```
- Rules: max 2 trades/day, Rs 1000 daily loss cap (net of ~Rs 62 charges per round trip), setup alerts 09:30-12:00.
- On expiry day it uses the next expiry (override with `--expiry YYYY-MM-DD`).
- Each alert beeps and shows a Windows notification; set `ASSIST_NO_TOAST=1` to keep only the beep.
- If the trade book can't be read, the checklist shows WAIT ("book") instead of assuming no trades.
- Files: `SwingSystem/data/assist/state.json` (latest checklist), `alerts.log` (one line per alert; `SIM` lines are tests).
- Claude watches alerts with a Monitor on `tail -n 0 -F SwingSystem/data/assist/alerts.log | grep --line-buffered -E "ALERT|DATA"`.

## Market data (Angel One)

```
cd D:\python\SwingSystem

# Top up NIFTY 50 1-minute data (resumes from the last stored candle; a few seconds)
python -c "from core.angel_data import backfill; backfill('1m', symbols=['^NSEI'])"

# 1-minute data for stocks (first time from 2018 takes a while per stock; later runs only top up)
python -c "from datetime import datetime; from core.angel_data import backfill; backfill('1m', start=datetime(2018,1,1), symbols=['RELIANCE.NS','HDFCBANK.NS'])"

# India VIX 1-minute (volatility for synthetic option prices; first run from 2018 takes a while)
python -c "from datetime import datetime; from core.angel_data import backfill; backfill('1m', start=datetime(2018,1,1), symbols=['^INDIAVIX'])"

# Record Nifty options: next 3 expiries, ATM +-40 strikes, 1m candles + OI (main.py daily also does this).
# Angel One deletes expired contracts - a missed day before an expiry is lost for good.
python -c "from core.angel_options import record; record()"

# Top up 5-minute data for the whole Nifty 100 + NIFTY (main.py daily also does this)
python -c "from core.angel_data import backfill; backfill('5m')"
```
- Files: `SwingSystem/data/angel/1m/` and `SwingSystem/data/angel/5m/` (index file = `IDX_NSEI.csv`, VIX = `IDX_INDIAVIX.csv`),
  options in `SwingSystem/data/angel/options/` (`contracts.csv` + `1m/NIFTY/<expiry>/<strike><CE|PE>.csv`).
- If the latest day is missing after a run, Angel One hasn't published it yet. Try again later.
- Credentials are in `SwingSystem/.env`.

## Swing system (daily signals and journal)

```
cd D:\python\SwingSystem
python main.py daily             # after market close: track, review, scan; also records Nifty options + VIX
python main.py scan              # today's ranked calls only (logged to data/journal.db)
python main.py track             # update outcomes + live scorecard
python main.py review            # write data/reviews/YYYY-MM-DD.md
python main.py calls -n 30       # list journal entries
python watch.py                  # live watcher during market hours (own terminal); --once = one snapshot, --all = include research-only
```

## Research / backtests

```
cd D:\python\SwingSystem
python main.py backtest                                   # all strategies vs random-entry control
python main.py backtest --strategy trend_pullback --start 2020-01-01 --save
python main.py portfolio                                  # live strategies as one account (equity curve, drawdown)
python lab.py freeze | families | sweep                   # daily research lab
python lab.py test "<entry>" "<exit>" [--holdout]         # test one config (holdout views are logged)
python lab_intraday.py freeze                             # snapshot intraday data for the intraday lab
python lab_intraday.py sweep --interval 5m                # intraday sweep
python lab_nifty_intraday.py dev                          # Nifty index intraday search: all families, rank 2018-22 (~1 min)
python lab_nifty_intraday.py validate NOISE3 MIDDAY       # 2023-24 check for those families' finalists (logged)
python lab_nifty_intraday.py final                        # amendment-1 finalists + the one 2025-26 TEST look (logged)
python lab_nifty_daily.py                                 # Nifty daily dip-buying lab (2007+)
cd ..\ChartReplay && python lab_alerts.py report          # paper journal of the LAB alerts (after the close)
cd ..\ChartReplay && python lab_alerts.py verify          # self-check: LAB rules == lab engine on 2023+
cd ..\ChartReplay && python alert_backtest.py             # assist alerts/checklist as a strategy (Nifty 1m 2018+)
cd ..\ChartReplay && python pullback_lab.py [smoke|check] # trend + 9-EMA pullback entries x exits (pre-registered, done 2026-10-20: no edge)
cd ..\ChartReplay && python pullback.py report            # your Take/Skip on PULLBACK alerts vs the rule's paper outcome
cd ..\ChartReplay && python pullback.py day 2026-10-16    # PULLBACK + assist alerts of one day; `verify` = matches the lab
cd ..\ChartReplay && python basebreak.py day 2026-10-21   # BASEBREAK (current practice alert) signals + premium result of one day
cd ..\ChartReplay && python basebreak.py report           # your Take/Skip on BASEBREAK alerts vs the rule's paper outcome
cd ..\ChartReplay && python basebreak.py verify           # live rule == lab trades; `lab` / `lab2` = the two backtest rounds (no edge)
python -m pytest                                          # SwingSystem tests (~3 min)
```

## Older scripts (root folder, January 2026 - superseded, kept for reference)

```
cd D:\python
python smart_sip_v2.py           # long-term Smart SIP (Aggressive Crash SIP)
python longterm_system.py        # long-term stock selection
```
