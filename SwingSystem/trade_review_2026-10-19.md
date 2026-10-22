# Trade Review: Nifty options, Mon 19-Oct-2026

Two round trips in NIFTY 19-Oct-2026 options (expiry day), 1 lot (65) each. Fills come from the Angel One trade
book. Market context comes from the live 1-minute Nifty bars in ChartReplay and from `SwingSystem/data/assist/alerts.log`.

---

## 1. Summary of the day

**How the market moved**

| Time | Nifty | What happened |
|---|---|---|
| 09:15 | 23,151 open (prev close 23,146) | Flat open; day high **23,175 at 09:18** |
| 09:18–09:29 | 23,175 → **22,971** | Sharp 200-pt sell-off |
| 09:29–09:45 | 22,971 → 23,156 | Full V-reversal, +185 pts |
| 09:45–10:00 | 23,156 → 23,040 | Down again |
| 10:00–11:30 | 23,040–23,125 | Choppy range (checklist: RANGE at 10:30) |
| 11:30–12:25 | 23,060 → 22,925 | Trend down (TREND DOWN alert 11:39) |
| 12:25–13:47 | 22,950 → **22,845 low** | Trend continued another ~100 pts |
| 13:47–14:55 | 22,845–22,880 | Range near the low |

VIX 14.4 → 14.8. The morning was two full swings of ~200 pts in 30 minutes: hard to read, easy to get chopped.

**Results**

| # | Time | Contract | Buy | Sell | Points | Hold | P&L |
|---|---|---|---:|---:|---:|---|---:|
| 1 | 10:09:07 → 10:20:29 | 19-Oct 23100 PE | 95.20 | 74.10 | −21.10 | 11.4 min | −1,372 |
| 2 | 12:14:13 → 12:25:01 | 19-Oct 23000 PE | 91.70 | 100.85 | +9.15 | 10.8 min | +595 |
| | | | | | **Gross** | | **−777** |
| | | | | | **Charges (~2 × 62)** | | **~−124** |
| | | | | | **Net** | | **≈ −901** |

**One-line verdict:** trade 1 was a range trade in an expiring option and lost to time decay, not to direction.
Trade 2 was a good setup, well managed, but taken **after the loss cap**: a rule break that happened to work.

---

## 2. Trade-by-trade

### Trade 1: 19-Oct 23100 PE, 10:09 → 10:20, −₹1,372

**What the market was doing**
- Nifty had dropped 23,156 → 23,040 (09:45–09:59), bounced to 23,096 (10:03), and was at 23,073 at entry.
- During the whole hold Nifty stayed in a **23,052–23,081 band**. Exit was at Nifty ≈ 23,075, i.e. **unchanged from entry**.

**What went wrong**
- **The premium lost 22% while Nifty went nowhere.** The 23100 PE was ~27 pts in the money at entry, so ~68 of the
  95.20 was time value. On expiry day that time value decays fast, and it also shrinks as volatility settles after a
  wild open. With no move, 21 points disappeared in 11 minutes.
- **No trend.** The market was in a range after two 200-pt swings. The checklist at 10:30 read RANGE ("bought options
  bleed here"). A PE needed a confirmed down trend that wasn't there.
- **Wrong expiry.** RULEBOOK: "on expiry day the **next** one". The checklist plan picks the 27-Oct contract on
  expiry day for exactly this reason. Today's contract has the fastest decay of any option.
- **Risk larger than planned.** The entry alert put the Nifty stop at 23,087 (~₹633). The loss was ₹1,372 because
  the premium decayed with Nifty still below the stop, so the Nifty stop never triggered. On expiry day a premium stop
  is the one that protects you.

**What went well**
- Exited instead of holding and hoping. The cap alert came at 10:16; the exit was 10:20.

---

### Trade 2: 19-Oct 23000 PE, 12:14 → 12:25, +₹595

**What the market was doing**
- TREND DOWN alert at 11:39 (Nifty −72 in 30 min, below both EMAs). At 11:39 the checklist said WAIT: 3.3 ATR
  stretched and premium up 22%, i.e. chasing.
- Nifty broke the morning low (22,970) at 12:05, touched 22,939, bounced to **22,978 (12:07–12:08)**, then rolled
  over. The 12:08 checklist: trend down PASS, Nifty back at the 9-EMA (the pullback), risk ~₹436.
- Entry at 12:14 with Nifty 22,946. Nifty reached 22,925 at 12:16; the premium peaked at **112.70**.

**What went well**
- **Waited for the trend and the pullback** instead of chasing the 11:39 alert. This is the setup the rulebook describes.
- **Stop sized on the premium** (~6 pts = ₹400), not on Nifty structure (~₹620).
- Moved the stop to breakeven at +10, respected the trail-hit alert, and exited at 100.85, better than the 97.70
  at the alert.

**What went wrong / caveats**
- **Taken after the loss cap was hit** (day net −₹1,434 at the time, cap ₹1,000). The checklist said NO on `loss` and
  `risk`. It worked; that does not make it the right decision.
- **Wrong expiry again** (19-Oct instead of 27-Oct). Here the fast moves of an expiring option helped (+10 premium
  points in ~1 minute), but the same speed cut trade 1.
- Entry 91.70 was above the 87.12 limit shown at 12:11, so the limit was moved up (about 4.6 pts given up).
- **The trend kept going:** Nifty fell another ~100 pts to 22,845 by 13:47. A 15-pt trail on a ~100-pt expiring
  premium exits on ordinary noise. Whether a wider trail (or trailing on Nifty's 9-EMA instead of the premium) is
  better is a **lab question, not a conclusion from one trade**.

---

## 3. Patterns

1. **Expiry-day options in a range lose money even when the direction call is not wrong.** Trade 1 lost 22% with Nifty
   flat. On expiry day, buy the next expiry (as the rulebook already says), or only buy today's contract in a
   confirmed trend.
2. **The trend-plus-pullback entry worked again.** 16-Oct trade 3 and 19-Oct trade 2 were both entries in a confirmed trend
   after a pause. The losers on both days were range or late entries.
3. **The loss cap was broken.** The rulebook says rules change only outside market hours. The second trade was a
   live decision to change the rule. See the rule proposal below.
4. **The data outages were the laptop, not Angel One.** Windows event log: Modern Standby 10:31–10:35, a
   "Critical Battery" sleep at 12:02 (resumed 12:04), standby 12:51–13:38, and a Wi-Fi driver reset at 10:17. Each
   matches a STALE/DOWN/BOOK alert. The two "rate limit" errors came right after waking up (catch-up burst).
   The message's "shared with another Angel One process" was an assumption in the code, now removed. The Angel
   app does not share the SmartAPI limit.

---

## 4. Rule decision

A "recovery trade after the cap" rule was considered after the session and **rejected**: the ₹1,000 cap stays hard.

---

## 5. What to repeat and what to avoid

### Repeat
- Waiting for TREND + pullback to the 9-EMA before entering (trade 2).
- Premium stop sized to the rupee budget; breakeven at +10; act on the trail alert.

### Avoid
- Buying the **expiring** contract on expiry day. Use the checklist's plan contract (27-Oct today).
- Trading the first hour after two 200-pt swings: that's chop, not trend.
- Moving a limit price up to get filled.
- Deciding rule changes mid-session.

---

## 6. Data notes

- Nifty 1m bars from the ChartReplay live feed (Angel One). The time-decay figures in trade 1 are estimates from the
  intrinsic value (Nifty 23,073 vs strike 23,100). The 19-Oct option candles were recorded after the close (see the
  `angel_options.record` fix) and are in `data/angel/options/1m/NIFTY/2026-10-19/`. Re-check trade 1 against them.
- 23000 PE 1m candles around trade 2: entry minute 12:14 ranged 90.45-100.15, so the 91.70 fill was near the low.
  After the 12:18 trail alert the premium went **back up to 115.95 (12:23)** before dropping to 92 by 12:26. A wider
  trail would have held through the dip but also given back more at the end. This is one example only, not evidence for a change.
- Charges are the assist estimate (~₹62 per round trip); the contract note is exact.
