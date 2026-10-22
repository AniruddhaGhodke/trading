# Trade Review: Nifty options, Fri 16-Oct-2026

Four round trips in NIFTY 19-Oct-2026 weekly options, 1 lot (65) each. Fills come from the Angel One trade book. Market context comes from Angel One 1-minute candles (Nifty index, India VIX, and each traded contract with OI), fetched after the close via `core/angel_data.py` and `core/angel_options.py`.

---

## 1. Summary of the day

**How the market moved**

| Time | Nifty | What happened |
|---|---|---|
| 09:15 | 23,065 open | Sold off right away |
| 09:36 | **22,914 low** | Day's low, 150 pts below the open |
| 09:40–10:25 | 22,914 → **23,148** | V-shaped reversal, +234 pts. The 10:00–10:20 leg was the strongest move of the day |
| 10:25–11:08 | 23,148 → 23,059 | Pullback |
| 11:10–15:15 | 23,060–23,140 | Range for the rest of the day, mostly 23,080–23,140 |

India VIX was flat (14.0 at the open, 14.0 at the close). The options expired Mon 19-Oct, one trading day after today, so gamma was high. One-minute candles on these contracts often had a 10–20 point range, which is 10–15% of the premium.

**Results**

| # | Time | Contract | Buy | Sell | Points | Hold | P&L |
|---|---|---|---:|---:|---:|---|---:|
| 1 | 09:42:06 → 09:47:02 | 23050 PE | 188.20 | 183.00 | −5.20 | 5.0 min | −338 |
| 2 | 10:00:20 → 10:03:51 | 23050 CE | 91.50 | 93.05 | +1.55 | 3.5 min | +101 |
| 3 | 10:12:24 → 10:14:58 | 23050 CE | 109.25 | 124.20 | +14.95 | 2.5 min | +972 |
| 4 | 11:30:07 → 11:45:12 | 23100 CE | 113.85 | 106.35 | −7.50 | 15.0 min | −488 |
| | | | | | | **Gross** | **+247** |

**Charges estimate: the day probably ended about flat or slightly negative.**

Assumptions: standard Angel One F&O pricing (₹20 per executed order); STT on the option sell premium; NSE transaction charge 0.03503% of premium turnover; SEBI fee ₹10/crore; stamp duty 0.003% on buys; GST 18% on brokerage + transaction + SEBI fees. Buy turnover = ₹32,682 and sell turnover = ₹32,929, for a total of ₹65,611.

| Item | ₹ |
|---|---:|
| Brokerage (8 orders × ₹20) | 160.00 |
| STT on sells: 0.15% (Budget 2026 rate, if it applies) / 0.10% (old rate) | 49.39 / 32.93 |
| NSE transaction charges | 22.98 |
| SEBI fee | 0.07 |
| Stamp duty | 0.98 |
| GST | 32.95 |
| **Total** | **≈ 266 / ≈ 250** |

**Net ≈ −₹19 (or −₹3 at the old STT rate).** Charges took the whole ₹247. Each round trip costs about ₹62, which is about **0.95 premium points per lot**, so a trade has to make about 1 point just to break even. Check the exact figure on Angel One's contract note.

**One-line verdict:** you read the direction right on the big move (trades 2–3) but cut it within minutes. You lost on the two trades taken against or late in a move (1 and 4). Charges took what was left.

---

## 2. Trade-by-trade

### Trade 1: 23050 PE, 09:42 → 09:47, −₹338

**What the market was doing**
- Nifty bottomed at **22,918 at 09:36**, then bounced to 22,965 by 09:42.
- The PE peaked at **220.45 at 09:36**, the same minute as the Nifty low, and had already fallen to ~185 when you bought.
- On the 1-minute chart, the 9-EMA crossed above the 21-EMA right at your entry (09:42–09:43). Short-term momentum had just turned up.

**What went wrong**
- **You bought the put after the down move had ended.** The entry came 6 minutes and 50 Nifty points after the low, chasing the 09:15–09:36 sell-off once it was over.
- **The exit price was poor.** In the minute before the exit (09:46) the PE traded 189–198, and the 09:47 candle opened at 188.05. You sold at 183.00 two seconds into that minute, near its 177.65 low, and the same minute later hit 201. A limit order at or near the last price would probably have got you out around breakeven. Most of the ₹338 loss came from this exit fill, not from the direction.

**What went well**
- **You cut it quickly.** After your exit the PE fell to 125.7 by 10:02 and 83.7 by 10:17. Holding even 15 minutes would have cost about **₹4,000** instead of ₹338. This was the best decision of the day.

---

### Trade 2: 23050 CE, 10:00 → 10:03, +₹101

**What the market was doing**
- Nifty had rallied from 22,938 (09:39) to 23,033, about +95 pts in 20 minutes, with the 9-EMA well above the 21-EMA.
- Option volume and OI were expanding: the 09:57–10:03 candles traded 43k–94k contracts per minute, against 10–20k earlier.

**What went well**
- **You traded with the move.** Switching from PE to CE was the right read.
- The strike was at the money (23050 with Nifty at 23033, delta ≈ 0.50), which is reasonable.

**What went wrong**
- **You were late.** You entered 13 minutes after the PE exit. At 09:47, when trade 1 was closed, the CE was at about 56–60. Your entry was 91.5, so you paid about 30 points more for the same idea.
- **Both fills were at the bad end of the candle.** You bought at 91.50 when the 10:00 candle closed at 88.85 (near its 93.85 high). You sold at 93.05 when the 10:03 candle ranged 92.65–106.45 and closed at 97.25 (near its low).
- **The exit was far too early.** Nifty was still rising during the hold (+14 pts), and nothing on the chart had turned. After the exit the CE reached 110 within 5 minutes and **165.5 within 16 minutes (10:19)**. A gain of 1.55 points barely covers the ~0.95-point cost of a round trip.

---

### Trade 3: 23050 CE, 10:12 → 10:14, +₹972 (best trade)

**What the market was doing**
- Nifty went sideways in a tight 23,050–23,068 band from 10:05 to 10:11. You bought at 10:12 (Nifty 23,073) as price pushed above that band.
- You sold at 10:14:58 (Nifty 23,094). That candle was the start of a breakout leg that took Nifty to 23,140 by 10:19.

**What went well**
- **This was the best-structured entry of the day:** a breakout from a small range, in the direction of the trend, with Nifty above both EMAs.
- **The entry fill was good.** You bought at 109.25 and the minute closed at 111.35. This was your only entry below the candle close.

**What went wrong**
- **You exited at the start of the move, not the end.** The CE went 124.2 → 146 (10:17) → **165.5 (10:19)** and held 150–160 until about 10:35. Even a loose trailing stop of 15 points below the high would have exited around 150, for roughly **+₹2,650** instead of ₹972.
- **Trades 2 and 3 were one idea split into two trades.** Holding trade 2's entry (91.50) to trade 3's exit (124.20) gives +32.7 points = **₹2,125**, with two fewer orders (about ₹50 less in charges). Your actual result was ₹1,073.

---

### Trade 4: 23100 CE, 11:30 → 11:45, −₹488

**What the market was doing**
- After the 10:25 high of 23,148, Nifty pulled back to 23,059 (11:08). It then bounced to 23,125 by 11:30, still under the morning high, which was resistance.
- Over the 30 minutes before entry, Nifty had gone almost nowhere (+6 pts). The market had become a range.
- In the 8 minutes before you bought, the CE ran from 88 to 115 (+30%). You bought on the 11:30 spike candle (108.75–115.65, 63k contracts) at 113.85, near its high.

**What went wrong**
- **You chased a spike in a range, just below resistance.** Nifty never got through 23,132 and finished the hold 3.6 pts lower.
- **Nifty hardly moved, yet the premium lost 7.5 points.** About 2 points of that came from buying near the top of the minute (113.85 against a 111.65 close). Most of the rest was the spike's extra premium fading: implied volatility fell from about 10.9% to 10.2%, which is roughly 6 points on this contract (vega ≈ 8.7 per vol point). Buying an option on a spike pays for excitement that drains away if the move stalls.
- At no point did the trade go meaningfully in your favour: the best price during the hold was 115.9, only 2 points above entry.

**What went well**
- **The exit was reasonable.** The CE dropped to **91.3** within 30 minutes of your exit, about ₹1,000 more loss if held. It briefly touched 117.9 within the hour, then closed the day at 94.1.
- You stopped trading after this. The afternoon was a 23,070–23,140 range with nothing to gain from buying options.

---

## 3. Patterns across the four trades

1. **Trading with the trend made money; trading against it or late lost money.** The two CE trades during the 09:40–10:25 trend made +₹1,073. The PE bought after the low (trade 1) and the CE bought in the late-morning range (trade 4) lost ₹826 between them. Your direction read in the strongest phase was good.

2. **Winners were cut faster than losers.** Winners were held 2.5 and 3.5 minutes; losers 5 and 15 minutes. From 10:00 to 10:19 the 23050 CE went from about 86 to 165, roughly 80 points. You captured 16.5 of them.

3. **The PE-to-CE switch was right but slow.** Exiting the PE at 09:47 was itself the signal that the down move was over: Nifty had made its low at 09:36 and the fast EMA had crossed up. The CE entry came 13 minutes and about 90 Nifty points later, at 91.5 instead of about 60.

4. **Fills were usually at the wrong end of the candle.** Three of the four entries were above the minute's close (by +2.5, +2.65 and +2.2 points), and two exits were near the minute's low (trade 1: 183 against a 188 open; trade 2: 93.05 against a 97.25 close). With 10–20-point one-minute ranges, buying at the market into a spike or selling at the market into a dip costs several points. That is about 2–5× the ~1-point round-trip charge.

5. **Strike choice was fine.** All trades were at the money or slightly in the money (delta 0.50–0.60), with liquid strikes and tight enough candles. Strike selection did not cause any of the losses.

6. **Charges matter at this size.** At ~₹62 per round trip, 4 trades cost about ₹250, the whole day's gross. Two well-held trades would have made more and cost half as much.

7. **The timing of the day was good.** All the action was 09:42–11:45, the period with the trend. You took no trades in the afternoon range.

---

## 4. What to repeat and what to avoid

### Repeat
- **Cut a losing trade within minutes when the reason for it is gone.** The trade 1 exit saved about ₹4,000.
- **Trade in the direction of the current 5–15-minute trend**, with Nifty above (CE) or below (PE) the 1-minute 9- and 21-EMAs, as in trades 2 and 3.
- **Buy a breakout from a tight consolidation**, as in trade 3 (Nifty pushing above a 7-minute 23,050–23,068 band).
- **Trade at the money or slightly in the money** in the nearest weekly expiry.
- **Stop trading once the market turns into a range**, as you did after 11:45.

### Avoid
- **Buying the option for the move that just ended.** On 16-Oct the PE was bought 6 minutes after the low, when the fast EMA had already crossed up. Rule: if the 9-EMA has crossed against your direction, don't enter.
- **Taking 1–2 points of profit while the trend is still running.** Before exiting a winner, ask what has actually changed: a Nifty lower high (for a CE), or a close back below the 9-EMA. If nothing has changed, keep holding and trail the stop instead (for example, 10–15 premium points below the high, or Nifty's last 1-minute swing low).
- **Re-buying the same idea higher.** If you exit and then buy back the same strike 9 minutes later at a 16-point higher price, the first exit was the mistake. Hold it the first time.
- **Buying on a spike candle in a range** (trade 4). Wait for a pullback to the 9-EMA, or a close above the range high (23,148 that day), before buying.
- **Market orders on 10–20-point one-minute candles.** Use limit orders at or near the last price, both for entries and especially for exits that are not emergencies (trade 1's exit gave up about 5 points).
- **More than about 2 trades for a gain of around ₹250.** Each round trip needs about 1 premium point just to cover costs.

### A simple checklist for the next session
1. Which way is Nifty trending over 15 minutes, and are both 1-minute EMAs pointing that way? If not, no trade.
2. Am I buying after a pause or pullback, or chasing a candle that is already 5+ points up? If I'm chasing, wait.
3. Use a limit order.
4. Set the stop on Nifty structure, not only on the premium.
5. To exit a winner, a Nifty signal must have turned. Otherwise trail the stop.

---

## 5. Data notes and limits

- **Source:** Angel One SmartAPI `getCandleData` (1 minute) for NIFTY 50, INDIA VIX and NFO tokens 47166 (23050PE), 47164 (23050CE) and 47167 (23100CE), plus `getOIData` for OI. Fills are from `tradeBook`. All option contracts have 376 one-minute candles for 09:15–15:30.
- **Nifty index feed gap:** the index candles for **15:15–15:19 and 15:29 are missing**, and the 15:20–15:28 candles show a jump to 23,364 that the option prices do not reflect (the 23050 CE stayed at 120–130, which is inconsistent with Nifty at 23,300+). Treat the index data after 15:14 as bad. Last reliable Nifty print: 23,119 at 15:14. None of the trades fall in this window. This is a known gap in Angel One's index feed.
- **Not available from the API:** bid/ask quotes and order book depth at your fill times, tick-level prices inside each minute, and volume for the index itself. Comments about where fills sat relative to the minute's open, high, low and close describe the candle, not the actual spread. Some of the gap may have been the spread rather than your timing.
- **Implied volatility and delta** were derived with Black-Scholes from the Nifty spot price (rate 6.5%, expiry 19-Oct 15:30). They are approximate. Calls and puts came out at different implied volatilities (about 10–11% for the calls, 15–17% for the put) because spot was used instead of the futures price. Compare implied volatility only within the same contract (as in trade 4).
- **Charges** are estimates from published rate cards. The exact amount is on the contract note.
