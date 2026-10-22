// Candlestick pattern markers. A pattern on bar i uses bars [0..i] only and is shown once bar i has
// COMPLETED (never on the forming candle), so it appears exactly when a trader could have seen it.
// Detection runs once per load over all bars; display is cut at the last completed bar.
window.PatternLayer = (() => {
  const STORE = "replay.patterns.v1";
  const BULL = "#26a69a", BEAR = "#ef5350", NEUT = "#b2b5be";
  // tier "key" = classic reversal / continuation signals; "all" also shows indecision / structure candles.
  // Listed in priority order: the first match on a bar becomes its chart label.
  const INFO = {
    morning_star: { name: "Morning Star", short: "M.Star", dir: 1, tier: "key",
      tip: "Big red candle, small-bodied pause, then a green candle closing past the red's midpoint, after a fall. Sellers exhausted." },
    evening_star: { name: "Evening Star", short: "E.Star", dir: -1, tier: "key",
      tip: "Big green candle, small-bodied pause, then a red candle closing below the green's midpoint, after a rise. Buyers exhausted." },
    three_soldiers: { name: "Three White Soldiers", short: "3 Soldiers", dir: 1, tier: "key",
      tip: "Three strong green candles, each opening inside the last body and closing higher. Steady buying pressure." },
    three_crows: { name: "Three Black Crows", short: "3 Crows", dir: -1, tier: "key",
      tip: "Three strong red candles, each opening inside the last body and closing lower. Steady selling pressure." },
    bull_engulf: { name: "Bullish Engulfing", short: "Bull Engulf", dir: 1, tier: "key",
      tip: "After a fall, a green body completely covers the previous red body. Buyers overpowered sellers." },
    bear_engulf: { name: "Bearish Engulfing", short: "Bear Engulf", dir: -1, tier: "key",
      tip: "After a rise, a red body completely covers the previous green body. Sellers overpowered buyers." },
    piercing: { name: "Piercing Line", short: "Piercing", dir: 1, tier: "key",
      tip: "After a fall, a green candle opens at/below the prior red close and closes above its midpoint." },
    dark_cloud: { name: "Dark Cloud Cover", short: "Dark Cloud", dir: -1, tier: "key",
      tip: "After a rise, a red candle opens at/above the prior green close and closes below its midpoint." },
    hammer: { name: "Hammer", short: "Hammer", dir: 1, tier: "key",
      tip: "After a fall: long lower wick (2x+ body), small body at the top. Price was pushed down and bought back." },
    hanging_man: { name: "Hanging Man", short: "Hang Man", dir: -1, tier: "key",
      tip: "Hammer shape after a rise. Sellers showed up intraday - a warning, needs a red candle to confirm." },
    shooting_star: { name: "Shooting Star", short: "Shoot Star", dir: -1, tier: "key",
      tip: "After a rise: long upper wick (2x+ body), small body at the bottom. Price was pushed up and rejected." },
    inv_hammer: { name: "Inverted Hammer", short: "Inv Hammer", dir: 1, tier: "key",
      tip: "Shooting-star shape after a fall. Buyers tried to push up - needs a green candle to confirm." },
    bull_harami: { name: "Bullish Harami", short: "Harami", dir: 1, tier: "all",
      tip: "After a fall, a small candle sits inside the previous big red body. Selling momentum is stalling." },
    bear_harami: { name: "Bearish Harami", short: "Harami", dir: -1, tier: "all",
      tip: "After a rise, a small candle sits inside the previous big green body. Buying momentum is stalling." },
    tweezer_bottom: { name: "Tweezer Bottom", short: "Tweezer", dir: 1, tier: "all",
      tip: "After a fall, two candles with (almost) the same low, red then green. That low was defended twice." },
    tweezer_top: { name: "Tweezer Top", short: "Tweezer", dir: -1, tier: "all",
      tip: "After a rise, two candles with (almost) the same high, green then red. That high was rejected twice." },
    bull_marubozu: { name: "Bullish Marubozu", short: "Marubozu", dir: 1, tier: "all",
      tip: "Large green candle with almost no wicks: buyers in control from open to close." },
    bear_marubozu: { name: "Bearish Marubozu", short: "Marubozu", dir: -1, tier: "all",
      tip: "Large red candle with almost no wicks: sellers in control from open to close." },
    dragonfly: { name: "Dragonfly Doji", short: "Dragonfly", dir: 1, tier: "all",
      tip: "Open = close at the high with a long lower wick. Strong rejection of lower prices." },
    gravestone: { name: "Gravestone Doji", short: "Gravestone", dir: -1, tier: "all",
      tip: "Open = close at the low with a long upper wick. Strong rejection of higher prices." },
    doji: { name: "Doji", short: "Doji", dir: 0, tier: "all",
      tip: "Open ≈ close: indecision. Matters most after a strong move or at a support / resistance level." },
    spinning_top: { name: "Spinning Top", short: "Spin", dir: 0, tier: "all",
      tip: "Small body with wicks on both sides: neither side won this candle." },
    inside_bar: { name: "Inside Bar", short: "Inside", dir: 0, tier: "all",
      tip: "High and low inside the previous candle: range contraction. A break of the mother bar's high/low often starts the next move." },
  };
  const ORDER = Object.keys(INFO);
  const MODES = ["key", "all"];

  // ---- detection ----
  const body = (b) => Math.abs(b.close - b.open);
  const range = (b) => b.high - b.low;
  const upper = (b) => b.high - Math.max(b.open, b.close);
  const lower = (b) => Math.min(b.open, b.close) - b.low;
  const green = (b) => b.close > b.open, red = (b) => b.close < b.open;
  const mid = (b) => (b.open + b.close) / 2;

  function avg(bars, i, f, n = 10) {
    let s = 0, k = 0;
    for (let j = Math.max(0, i - n); j < i; j++) { s += f(bars[j]); k++; }
    return k ? s / k : 0;
  }
  // Direction of the 5 bars BEFORE bar i: +1 rising, -1 falling, 0 no clear move.
  function trendBefore(bars, i) {
    if (i < 6) return 0;
    const d = bars[i - 1].close - bars[i - 6].close;
    return Math.abs(d) < 0.5 * avg(bars, i, range) ? 0 : Math.sign(d);
  }

  function detectAt(bars, i) {
    const b = bars[i], r = range(b), found = new Set();
    if (!(r > 0)) return [];
    const ab = avg(bars, i, body), ar = avg(bars, i, range);
    const p = bars[i - 1], a = bars[i - 2];

    // three-bar
    if (a) {
      const t = trendBefore(bars, i - 2);
      if (t < 0 && red(a) && body(a) > ab && body(p) <= 0.4 * body(a) && green(b) && b.close > mid(a)) found.add("morning_star");
      if (t > 0 && green(a) && body(a) > ab && body(p) <= 0.4 * body(a) && red(b) && b.close < mid(a)) found.add("evening_star");
      const three = [a, p, b];
      const strong = (x) => body(x) >= 0.6 * range(x) && body(x) >= 0.7 * ab;
      if (three.every((x) => green(x) && strong(x)) && p.close > a.close && b.close > p.close &&
          p.open >= a.open && p.open <= a.close && b.open >= p.open && b.open <= p.close) found.add("three_soldiers");
      if (three.every((x) => red(x) && strong(x)) && p.close < a.close && b.close < p.close &&
          p.open <= a.open && p.open >= a.close && b.open <= p.open && b.open >= p.close) found.add("three_crows");
    }
    // two-bar
    if (p && range(p) > 0) {
      const t = trendBefore(bars, i - 1);
      if (t < 0 && red(p) && green(b) && b.open <= p.close && b.close >= p.open && body(b) > body(p)) found.add("bull_engulf");
      if (t > 0 && green(p) && red(b) && b.open >= p.close && b.close <= p.open && body(b) > body(p)) found.add("bear_engulf");
      if (t < 0 && red(p) && body(p) > ab && green(b) && b.open <= p.close && b.close > mid(p) && b.close < p.open) found.add("piercing");
      if (t > 0 && green(p) && body(p) > ab && red(b) && b.open >= p.close && b.close < mid(p) && b.close > p.open) found.add("dark_cloud");
      const inBody = Math.max(b.open, b.close) <= Math.max(p.open, p.close) && Math.min(b.open, b.close) >= Math.min(p.open, p.close);
      if (t < 0 && red(p) && body(p) > ab && body(b) < 0.5 * body(p) && inBody) found.add("bull_harami");
      if (t > 0 && green(p) && body(p) > ab && body(b) < 0.5 * body(p) && inBody) found.add("bear_harami");
      const tol = 0.05 * ar;
      if (t < 0 && red(p) && green(b) && Math.abs(p.low - b.low) <= tol) found.add("tweezer_bottom");
      if (t > 0 && green(p) && red(b) && Math.abs(p.high - b.high) <= tol) found.add("tweezer_top");
      if (b.high <= p.high && b.low >= p.low && (b.high < p.high || b.low > p.low)) found.add("inside_bar");
    }
    // single-bar
    const t = trendBefore(bars, i), bd = body(b);
    if (bd <= 0.1 * r) {
      if (lower(b) >= 0.6 * r && upper(b) <= 0.1 * r) found.add("dragonfly");
      else if (upper(b) >= 0.6 * r && lower(b) <= 0.1 * r) found.add("gravestone");
      else found.add("doji");
    } else {
      if (lower(b) >= 2 * bd && upper(b) <= 0.15 * r) found.add(t < 0 ? "hammer" : t > 0 ? "hanging_man" : null);
      if (upper(b) >= 2 * bd && lower(b) <= 0.15 * r) found.add(t > 0 ? "shooting_star" : t < 0 ? "inv_hammer" : null);
      if (bd >= 0.9 * r && bd > 1.3 * ab) found.add(green(b) ? "bull_marubozu" : "bear_marubozu");
      if (bd <= 0.3 * r && upper(b) >= bd && lower(b) >= bd) found.add("spinning_top");
    }
    found.delete(null);
    return ORDER.filter((k) => found.has(k));
  }

  // ---- display ----
  // mode = which set ("key" | "all"); show = hide/show toggle. Saved as {mode, show}.
  let mode = "key", show = true, markers = null, bars = null, hits = [], shownLast = null, shownState = null;
  try {
    const v = JSON.parse(localStorage.getItem(STORE));
    if (MODES.includes(v?.mode)) mode = v.mode;
    if (typeof v?.show === "boolean") show = v.show;
  } catch {}
  const save = () => { try { localStorage.setItem(STORE, JSON.stringify({ mode, show })); } catch {} };

  const visibleKeys = (i) => (!show ? [] : (hits[i] || []).filter((k) => mode === "all" || INFO[k].tier === "key"));

  function init(series) { markers = LightweightCharts.createSeriesMarkers(series, []); }

  function setMode(m) { mode = m; save(); if (window.replay?.bars.length) update(window.replay); }
  function setShow(v) { show = v; save(); if (window.replay?.bars.length) update(window.replay); }

  // Called after every replay step: (re)detect on a new load, then show markers up to the last completed bar.
  function update(rp) {
    if (rp.bars !== bars) {
      bars = rp.bars; shownLast = null;
      hits = bars.map((_, i) => detectAt(bars, i));
    }
    const last = rp.sub !== null ? rp.cursor - 1 : rp.cursor;
    const state = `${mode}/${show}`;
    if (last === shownLast && state === shownState) return;
    shownLast = last; shownState = state;
    const out = [];
    for (let i = 0; i <= last; i++) {
      const k = visibleKeys(i)[0];
      if (!k) continue;
      const info = INFO[k];
      out.push(info.dir > 0 ? { time: bars[i].time, position: "belowBar", shape: "arrowUp", color: BULL, text: info.short }
        : info.dir < 0 ? { time: bars[i].time, position: "aboveBar", shape: "arrowDown", color: BEAR, text: info.short }
        : { time: bars[i].time, position: "aboveBar", shape: "circle", color: NEUT, text: info.short, size: 0.5 });
    }
    markers.setMarkers(out);
  }

  // Legend lines for bar i (only if it is completed - the caller passes the bar index being shown).
  function legend(i, rp) {
    if (i > (rp.sub !== null ? rp.cursor - 1 : rp.cursor)) return "";
    return visibleKeys(i).map((k) => {
      const info = INFO[k], c = info.dir > 0 ? BULL : info.dir < 0 ? BEAR : NEUT;
      return `<div class="pat"><b style="color:${c}">${info.name}</b> <span class="muted">${info.tip}</span></div>`;
    }).join("");
  }

  return { init, update, setMode, setShow, legend, detectAt, INFO, MODES,
    get mode() { return mode; }, get show() { return show; } };
})();
