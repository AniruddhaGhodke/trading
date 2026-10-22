# Chart Replay

A local bar-replay tool, similar to TradingView's Replay, for practising chart reading.
It uses a Python standard-library server and the TradingView lightweight-charts library, which is stored in `static/` so no internet connection is needed.

## Run

```
pip install pandas            # the only dependency
cd D:\python\ChartReplay
python server.py              # then open http://127.0.0.1:8050
```

1. Pick a symbol, an interval, and the start date and time.
2. Click **Load**.
3. Use **⏮ / ▶⏸ / ⏭** or the keyboard: **← / Space / →**.

Speed sets how fast candles appear during play, in candles per second at 1×.

**Start semantics:** you see every candle that has closed by the chosen time, and nothing after it. For example:
- A 5m replay at 10:02 shows up to the 09:55 candle.
- A 1D replay at 13:00 shows up to the previous day's candle.
- A replay at 09:15 shows the previous session's close, and **Next** reveals the opening candle.

Each load sends 300 bars of history plus 2,000 bars ahead. When replay reaches the end of that window, the start box is set to the current candle, so clicking **Load** continues from where you were.

## Forming candles (live-style replay)

With **Tick** set smaller than the chart interval, each candle builds up step by step, like a live chart. The open is fixed, the high and low grow, and the close moves. Indicators update on the partial candle too.

**Controls**

| Input | Action |
|---|---|
| **→** or **⏭** | Next tick |
| **Shift+→** | Finish the candle (or jump a whole candle) |
| **←** | Back one tick |
| **Shift+←** | Remove the forming candle |

- **Speed** is still time per candle; during play that time is split across the candle's ticks.
- **Tick sizes:** `Auto` uses 1-minute data where a stock has it, and 5-minute otherwise. `1m`, `5m`, `15m`, `30m` and `1h` are fixed sizes; a 1m request falls back to 5m for periods without 1m data. `Off` reveals whole candles.
- **On the 5m chart**, candles only form when the stock has 1m data.
- **No lookahead:** a forming candle shows only the sub-candles up to the current tick. Its indicator values are computed on the earlier completed candles plus the partial candle (`indicators.for_partials`), so nothing later leaks in. The final tick equals the completed candle exactly; `tests/test_forming.py` checks both.
- **Speed limit:** each candle's ticks and indicator values are fetched from the server about two candles ahead. At 10× speed with forming on, playback is limited to about 6 candles per second by server time; 5× and below run at full speed.

### Adding 1-minute data

Angel One's smallest historical candle is 1 minute; there's no seconds data and no tick history. The SwingSystem downloader already saves 1m files where this tool looks for them:

```
cd D:\python\SwingSystem
python -c "from datetime import datetime; from core.angel_data import backfill; backfill('1m', start=datetime(2024,1,1), symbols=['RELIANCE.NS','HDFCBANK.NS'])"
```

- **Where it saves:** files go to `SwingSystem/data/angel/1m/<SYMBOL>.csv`.
- **Resumable:** run it again later to download only the new candles.
- **Request size:** 30 days per request, so a few years of data per stock takes a few minutes.
- **What changes:** stocks with 1m files get a **1m** chart interval, and `Auto` ticks become 1-minute (a 5m candle forms in 5 steps). Stocks without 1m data keep using 5m.
- **Your own data:** 1-minute CSVs can also go in `ChartReplay/csv/1m/`.

## Price strip and hover

The strip under the toolbar shows the symbol, interval, time, O/H/L/C, the change from the previous close, and volume (in lakh or crore).
- O/H/L/C are green for an up candle and red for a down candle.
- **Hover** over any revealed candle to see its prices and indicator values; the time turns amber and says "(hover)". Move the mouse off the chart, or over the empty area to the right, to go back to the current candle.

## Drawing tools (left toolbar)

| Tool | How | Shortcut |
|---|---|---|
| Trend line / Ray | Click, click (or press, drag, release). A ray extends to the right edge | Alt+T |
| Horizontal line | One click, with a price label on the axis | Alt+H |
| Vertical line | One click | Alt+V |
| Rectangle | Click, click (or drag) | Alt+R |
| Ruler | Click, click (or drag). Shows the price change, %, number of candles and time span. Not saved | Shift+click |

- **Editing:** in Cursor mode, click a drawing to select it, drag it to move it, or drag a white handle to reshape it. Delete removes the selected drawing, and Esc cancels whatever you're doing. The ✕ button deletes all drawings on the symbol.
- **Saving:** drawings are saved per symbol in the browser as time-and-price points, so they survive reloads, new start times and interval switches.
- **Drawing into the future:** you can draw into the empty area right of the last candle. This reveals nothing about future prices.

## Indicators

Click **Indicators ▾** to turn indicators on or off, change their settings, add more copies (for example a third EMA), or **Reset** to the defaults. Your settings are saved in the browser.

| Where | Indicators |
|---|---|
| On the price chart | EMA, SMA, Bollinger Bands, VWAP (resets daily; intraday only), volume bars |
| Own panel | RSI with 30/70 lines, MACD line, signal and histogram |

- **Same formulas as SwingSystem:** they come from `SwingSystem/core/indicators.py`, so the chart matches the scanner and the lab.
- **Accurate from the first candle:** each indicator is calculated over the symbol's full history, so there is no warm-up gap. Values are hidden only where the indicator genuinely has no value yet, such as the first 199 candles of an SMA-200.
- **No lookahead:** values are revealed one candle at a time along with the price. `tests/test_indicators.py` checks that every indicator is causal, meaning its value at a candle doesn't change when later candles are removed.

**To add an indicator:**
1. In `indicators.py`, write a function that takes the price data plus the settings and returns `{line_name: Series}`.
2. Add one `Spec` entry to `REGISTRY`, giving its settings, panel, line styles and warm-up length.

The test file picks up new entries automatically, and so does the Indicators menu.

## Candle patterns

The **Patterns** button shows or hides pattern markers (shortcut **P**). The menu next to it chooses the set:
- **Key:** Hammer, Hanging Man, Shooting Star, Inverted Hammer, Bullish/Bearish Engulfing, Piercing Line, Dark Cloud Cover, Morning/Evening Star, Three White Soldiers, Three Black Crows.
- **All** adds Doji (plain, Dragonfly, Gravestone), Spinning Top, Marubozu, Harami, Tweezer Top/Bottom and Inside Bar.

A marker appears only once its candle has closed, never on a forming candle. Reversal patterns need a clear move before them (5-bar trend), so a hammer shape after a rise is labelled Hanging Man. Hover over a marked candle to see what the pattern means. The rules are in `static/patterns.js` (`detectAt`).

## Paper trading

The **Trade** button shows or hides the panel on the right.
- **Orders:** **Buy** (B), **Sell** (S) and **Close** (X) fill at the price on screen, which is the last revealed tick. Buying while short covers the short, and selling while long exits.
- **Stop and target:** type them in, click ⌖ and then click the chart, or drag the dashed lines. Levels set while flat are a plan for the next order.
- **Fills:** stops and targets fill only on price action revealed later. A candle that opens past the level fills at the open (gap). If the stop and target are touched in the same step, the stop wins. With forming candles, fills are checked tick by tick.
- **Sizing:** with **Risk ₹** set and a stop set, qty = risk ÷ (entry − stop). Otherwise the Qty field is used.
- **Costs:** P&L includes **Cost % per side** (default 0.05%). R is net P&L ÷ initial risk, so costs show up in R too.
- **No cheating:** after stepping back you can look, but you can't trade until you return to the furthest candle you had reached. Loading a window that continues the same chart (for example Load at the end of data) keeps an open position; any other load closes it at the last price.
- **Results:** win rate, net, average R, profit factor, max drawdown and a trade log. Hover a log row for the reason and MFE/MAE in R. **CSV** downloads every trade. Trades are kept in browser storage (`replay.trades.v1`).

## Nifty options

The **Options** button opens the option chain panel. Everything in it is as of the **clock**: the end of the Nifty candle on screen (or of its forming tick), or the server time in Live mode. Nothing after the clock is used.

- **Chain:** pick an expiry (M = monthly) and the number of strikes on each side of ATM. Each row shows CE and PE price, delta and OI (in lakh). Shading marks in-the-money strikes, and the ATM strike is blue. Hover over a price to see theta per day, vega, gamma, IV and the time of the last real trade.
- **Contract:** click a CE or PE price. Its premium candles appear in a pane under the chart and move with the replay, including forming candles. **Main chart** opens the contract as the main symbol, so indicators, patterns, drawings and the Trade panel work on the premium itself.
- **Buy-only paper trading:** set Lots, an optional Stop ₹ and Target ₹ on the premium, then **Buy** or **Exit**.
  - Fills follow the same rules as the Trade panel (gap, stop wins on ties).
  - Slippage is added when buying and subtracted when selling.
  - Costs are ₹ per order plus a % of premium turnover. They are editable estimates; check your broker's charges.
  - A contract still open at expiry settles at its 15:30 value, which is intrinsic for synthetic contracts.
  - Trades are kept in browser storage (`replay.opt.trades.v1`).

**Where the prices come from** (each contract has a badge):

| Badge | Source | Limits |
|---|---|---|
| **REAL** | 1-minute candles and OI recorded from Angel One (`SwingSystem/data/angel/options/`) | Only contracts recorded while they were listed. Angel One deletes expired contracts, so this must run daily. Minutes with no trade are drawn as flat candles. |
| **SYNTH** | Black-Scholes on Nifty, with India VIX as the volatility (`options.py`) | Approximate. There's no skew, so out-of-the-money puts come out too cheap. VIX is a 30-day measure, so expiry-day premiums are off. No spread and no OI. |

- **Expiry calendar:** monthly only (last Thursday) until 11-Feb-2019, then weekly on Thursdays, then Tuesdays from 1-Sep-2025. A holiday moves expiry to the previous trading day. Recorded contracts give the real dates.
- **Lot sizes** depend on the expiry: 75, then 50 (Jul-2021), 25 (May-2024), 75 (Jan-2025) and 65 (Jan-2026). The change-over months are approximate.
- **Recording real data:** `main.py daily` runs `SwingSystem/core/angel_options.record()`. It covers the next 3 expiries, ±40 strikes, CE and PE, from each contract's listing date.

## Live mode (Nifty and Nifty options)

Select `IDX_NSEI` or open an option contract, then click **● Live**. During market hours the server polls Angel One:
- **Every minute:** today's 1m candles (plus OI for options) for Nifty, India VIX, ATM ±5 strikes of the nearest expiry, and any contract a chart is showing. These are saved to the same history files.
- **Every 2 s:** last traded prices, which build the current minute's candle.

The chart and the premium pane follow the market, and the option chain shows the latest prices. Click **● Live** again, or **Load**, to go back to replay.
- **Requirements:** this needs `SwingSystem/.env`. Outside market hours it shows the latest data.
- **Clock:** times come from Angel's corrected clock, because this PC's clock can run minutes slow.
- **Turning it off:** start with `python server.py --no-live` to switch the live feed off, for example while a long Angel One download is running. Two logins at once can invalidate each other.

## Data

| Source | Location | Notes |
|---|---|---|
| Angel One SmartAPI 5-minute backfill | `../SwingSystem/data/angel/5m/*.csv` | Nifty 100, 2018 onwards, IST. Refreshed by SwingSystem's backfill. |
| Your own CSV | `csv/<NAME>.csv` | Takes priority over Angel One data. |
| Angel One 1-minute (optional, per stock) | `../SwingSystem/data/angel/1m/*.csv` or `csv/1m/` | The 1m chart and forming ticks. See "Adding 1-minute data". |
| India VIX 1-minute | `../SwingSystem/data/angel/1m/IDX_INDIAVIX.csv` | Volatility for synthetic option prices (2018+). |
| Nifty options 1-minute + OI | `../SwingSystem/data/angel/options/1m/NIFTY/<expiry>/<strike><CE\|PE>.csv`, `contracts.csv` | Recorded daily; see "Nifty options". |

The CSV needs a `Datetime` column (or `Date`/`Timestamp`, or separate `Date` and `Time` columns) plus Open/High/Low/Close and an optional Volume column. Column names are case-insensitive. Times should be IST; timezone-aware times are converted to IST.

Higher intervals (15m, 30m, 1h, 1D) are resampled from 5m, with bars aligned to the 09:15 open.

Prices are not adjusted for corporate actions, so a split may show as a large gap. `SwingSystem/core/data.fix_corporate_jumps` can fix this if it becomes a problem.

## Structure, and how to extend it

- `data_source.py`: finds data, loads it into an in-memory cache, resamples it, and builds the replay window. Add new data sources to `SOURCES`.
- `server.py`: the API (`/api/symbols`, `/api/bars`, `/api/forming`, `/api/expiries`, `/api/chain`, `/api/live`). Add endpoints to the `ROUTES` dict. 
- `options.py`: option contracts as symbols (`NIFTY_<YYYYMMDD>_<strike><CE|PE>`), the expiry calendar, lot sizes, Black-Scholes, Greeks, implied volatility and the chain.
- `live.py`: the live polling thread (overlay of today's bars plus the forming minute). `data_source.invalidate()` drops derived caches when new bars arrive.
- `static/options.js`: the chain panel, the premium pane and option paper trading.
- `indicators.py`: the indicator registry; values are calculated on the server and lined up with the candles.
- `static/replay.js`: the replay state lives in `window.replay` (`bars`, `cursor`, `sub` for the forming tick, `playing`, `speedMs`, plus `next`, `prev`, `nextCandle`, `prevCandle`, `play`, `pause`). Chart add-ons are **layers** (`LAYERS`), each with `mount(chart, data, cursor)`, `push(i, time)`, `pop()` and `form(i, k, formingData, partialBar)`.
- `static/indicators.js`: the indicator layer and its settings menu.
- `static/patterns.js`: candle pattern detection and markers.
- `static/trade.js`: paper trading (orders, fills, panel, results). `TradeLayer.fill` is the pure fill rule.
- `static/drawings.js`: the drawing layer, built on the library's custom-drawing (primitive) API, with its own mouse handling. Add a tool by adding it to `POINTS`, `drawShape` and `hit`.
- The chart library is TradingView lightweight-charts **5.2.1**, stored in `static/`.
- `static/index.html`: the toolbar.

**No-lookahead rule.** Indicators, overlays and practice trades must use only `replay.visible()` (bars up to the cursor), never `replay.bars`. Register them with `replay.onStep((visibleBars, cursor) => ...)`, which runs after every load, next and prev.

## Tests

`python -m pytest tests -q` covers:
- **Loader:** start snapping, clamping to the data range, no bars past the start, and resample alignment.
- **Indicators:** that each one is causal, warm-up periods, alignment with the candles, VWAP resetting daily, and rejection of invalid settings.
- **Forming candles:** partial states, partial-candle indicators matching a direct calculation, and the 1m-to-5m fallback.
- **Options:** put-call parity, intrinsic value at expiry, IV round-trip, Greek signs, holiday expiry shift, option forming candles ending at the completed bar, no lookahead in premiums or chain, and real contracts (gap fill, session clip, OI).
