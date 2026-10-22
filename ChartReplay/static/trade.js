// Paper trading inside the replay.
//
// Market orders fill at the price on screen (the last revealed tick). Stop-loss / target fill when LATER
// revealed price action crosses them: a new candle's open is checked first (gap fill at the open), then its
// low/high; on a forming candle only the part of the range revealed since the last step counts. If stop and
// target are both touched in one step, the stop wins (conservative - the order inside a step is unknown).
//
// NO-LOOKAHEAD: orders are only accepted at the furthest point the replay has reached ("front"). After
// stepping back you can look, but not trade, and fills are only processed for states not seen before.
window.TradeLayer = (() => {
  const STORE = "replay.trades.v1", SETTINGS = "replay.trade.settings.v1";
  const BLUE = "#2962ff", RED = "#ef5350", GREEN = "#26a69a";
  const $ = (id) => document.getElementById(id);

  let rp = null, series = null, markersApi = null, el = null;
  // Order / fill messages go to the panel (the status line is rewritten on every replay step).
  const setMsg = (text, c = "") => { const m = $("tr-msg"); if (m) { m.textContent = text; m.className = c; } };
  let settings = { qty: 1, risk: "", cost: 0.05, open: true };
  let trades = [];              // closed trades, oldest first
  let pos = null;               // open position
  let plan = { sl: null, tp: null };  // levels set while flat, used by the next order
  let hw = -1, lastBars = null; // furthest replay state reached; bars array of the current load
  let pick = null, drag = null; // "sl" | "tp" while picking a level on the chart / dragging its line
  const priceLines = {};

  try { settings = { ...settings, ...JSON.parse(localStorage.getItem(SETTINGS) || "{}") }; } catch {}
  try { trades = JSON.parse(localStorage.getItem(STORE) || "[]"); } catch {}
  const saveSettings = () => { try { localStorage.setItem(SETTINGS, JSON.stringify(settings)); } catch {} };
  const saveTrades = () => { try { localStorage.setItem(STORE, JSON.stringify(trades)); } catch {} };

  const num = (v, d = 2) => (v === null || v === undefined || !isFinite(v) ? "–"
    : v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const sgn = (v, d = 2) => (v > 0 ? "+" : "") + num(v, d);
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
  const when = (t) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ");

  // ---- replay state helpers ----
  const ordinal = () => rp.cursor * 1e5 + (rp.sub === null ? 99999 : rp.sub);
  const atFront = () => ordinal() >= hw;
  const shown = () => rp.barAt(rp.cursor);
  const stateEnd = (i) => (i === rp.cursor && rp.sub !== null ? rp.form.partial[rp.sub].end : rp.ends[i]);
  const levels = () => (pos ? pos : plan);

  // Pure fill check for one bar. seen = {high, low} of this bar already processed, or null if the whole bar
  // (including its open) is new. Returns {reason, price} or null.
  function fill(p, b, seen) {
    const lo = seen ? (b.low < seen.low ? b.low : null) : b.low;
    const hi = seen ? (b.high > seen.high ? b.high : null) : b.high;
    const L = p.side > 0;
    const stopHit = (x) => p.sl != null && x !== null && (L ? x <= p.sl : x >= p.sl);
    const tgtHit = (x) => p.tp != null && x !== null && (L ? x >= p.tp : x <= p.tp);
    if (!seen && stopHit(b.open)) return { reason: "stop (gap)", price: b.open };
    if (!seen && tgtHit(b.open)) return { reason: "target (gap)", price: b.open };
    const s = stopHit(L ? lo : hi), t = tgtHit(L ? hi : lo);
    if (s) return { reason: t ? "stop (target also touched)" : "stop", price: p.sl };
    if (t) return { reason: "target", price: p.tp };
    return null;
  }

  function pnlOf(p, exit) {
    const gross = p.side * (exit - p.entry) * p.qty;
    const cost = (settings.cost / 100) * (p.entry + exit) * p.qty;
    const risk = p.initSl != null ? Math.abs(p.entry - p.initSl) * p.qty : null;
    return { gross, cost, net: gross - cost, r: risk ? (gross - cost) / risk : null };
  }

  // ---- orders ----
  function order(side) {
    if (!rp?.bars.length) return;
    if (rp.live?.on) { setMsg("Live mode: this panel trades replays only - use the Options panel for live paper trades", "warn"); return; }
    if (!atFront()) { setMsg("Viewing the past - step forward to the latest candle to trade", "warn"); return; }
    if (pos) {
      if (pos.side === -side) close("manual");
      else setMsg(`Already ${pos.side > 0 ? "long" : "short"} - close first (X)`, "warn");
      return;
    }
    const price = shown().close, { sl, tp } = plan;
    if (sl != null && (side > 0 ? sl >= price : sl <= price)) { setMsg(`Stop must be ${side > 0 ? "below" : "above"} the entry`, "err"); return; }
    if (tp != null && (side > 0 ? tp <= price : tp >= price)) { setMsg(`Target must be ${side > 0 ? "above" : "below"} the entry`, "err"); return; }
    let qty = Math.max(1, Math.floor(settings.qty) || 1);
    if (+settings.risk > 0 && sl != null) qty = Math.max(1, Math.floor(+settings.risk / Math.abs(price - sl)));
    const b = shown();
    setMsg("");
    pos = {
      symbol: rp.meta.symbol, interval: rp.meta.interval, side, qty, entry: price,
      entryTime: stateEnd(rp.cursor), entryBar: rp.bars[rp.cursor].time, sl, tp, initSl: sl,
      best: price, worst: price, last: price, seen: { i: rp.cursor, high: b.high, low: b.low, time: b.time },
    };
    plan = { sl: null, tp: null };
    render();
  }

  function close(reason, price, exitTime, exitBar) {
    if (!pos) return;
    if (price === undefined) {
      if (!atFront()) { setMsg("Viewing the past - step forward to the latest candle to close", "warn"); return; }
      price = shown().close; exitTime = stateEnd(rp.cursor); exitBar = rp.bars[rp.cursor].time;
    }
    const p = pnlOf(pos, price), risk = pos.initSl != null ? Math.abs(pos.entry - pos.initSl) : null;
    const { seen, last, ...keep } = pos;
    trades.push({ ...keep, exit: price, exitTime, exitBar, reason, pnl: +p.net.toFixed(2), cost: +p.cost.toFixed(2),
      r: p.r === null ? null : +p.r.toFixed(2),
      mfe: risk ? +((pos.side * (pos.best - pos.entry)) / risk).toFixed(2) : null,
      mae: risk ? +((pos.side * (pos.worst - pos.entry)) / risk).toFixed(2) : null });
    saveTrades();
    setMsg(`Closed ${pos.side > 0 ? "long" : "short"} - ${reason} @ ${num(price)}: ${sgn(p.net)}`, p.net >= 0 ? "live" : "err");
    pos = null;
    render(); renderLog();
  }

  // Process newly revealed price action from the position's last processed state up to the cursor.
  function advance() {
    for (let i = pos.seen.i; pos && i <= rp.cursor; i++) {
      const b = i === rp.cursor ? rp.barAt(i) : rp.bars[i];
      const seen = i === pos.seen.i ? pos.seen : null;
      const f = fill(pos, b, seen);
      const L = pos.side > 0;
      if (f) {
        pos.best = L ? Math.max(pos.best, f.price) : Math.min(pos.best, f.price);
        pos.worst = L ? Math.min(pos.worst, f.price) : Math.max(pos.worst, f.price);
        close(f.reason, f.price, stateEnd(i), b.time);
        return;
      }
      pos.best = L ? Math.max(pos.best, b.high) : Math.min(pos.best, b.low);
      pos.worst = L ? Math.min(pos.worst, b.low) : Math.max(pos.worst, b.high);
      pos.last = b.close;
      pos.seen = { i, high: b.high, low: b.low, time: b.time };
    }
  }

  // A new window was loaded: keep the position if the new window continues the same chart, else close it.
  function onLoad() {
    hw = ordinal();
    if (!pos) return;
    const k = rp.bars.findIndex((b) => b.time === pos.seen.time);
    if (pos.symbol === rp.meta.symbol && pos.interval === rp.meta.interval && k >= 0 && k <= rp.cursor) {
      pos.seen.i = k;
      advance();
    } else close("closed on reload", pos.last, pos.seen.time, pos.seen.time);
  }

  // ---- chart: price lines, markers, level dragging ----
  function setLine(key, price, color, title, style) {
    if (price == null) { if (priceLines[key]) { series.removePriceLine(priceLines[key]); delete priceLines[key]; } return; }
    const opts = { price, color, title, lineWidth: 1, lineStyle: style, axisLabelVisible: true };
    if (priceLines[key]) priceLines[key].applyOptions(opts); else priceLines[key] = series.createPriceLine(opts);
  }
  function drawLines() {
    const LS = LightweightCharts.LineStyle, lv = levels();
    setLine("entry", pos?.entry, BLUE, pos ? `${pos.side > 0 ? "LONG" : "SHORT"} ${pos.qty}` : "", LS.Solid);
    setLine("sl", lv.sl, RED, pos ? "SL" : "plan SL", LS.Dashed);
    setLine("tp", lv.tp, GREEN, pos ? "TP" : "plan TP", LS.Dashed);
  }
  function drawMarkers() {
    if (!rp.bars.length) { markersApi.setMarkers([]); return; }
    const times = new Set(rp.bars.slice(0, rp.cursor + 1).map((b) => b.time)), out = [];
    const mine = trades.filter((t) => t.symbol === rp.meta.symbol && t.interval === rp.meta.interval);
    const add = (time, side, text) => times.has(time) && out.push(side > 0
      ? { time, position: "belowBar", shape: "arrowUp", color: BLUE, text }
      : { time, position: "aboveBar", shape: "arrowDown", color: "#ff9800", text });
    for (const t of mine) {
      add(t.entryBar, t.side, `${t.side > 0 ? "B" : "S"} ${num(t.entry)}`);
      add(t.exitBar, -t.side, `${t.r !== null ? sgn(t.r, 1) + "R" : sgn(t.pnl, 0)}`);
    }
    if (pos && pos.symbol === rp.meta.symbol) add(pos.entryBar, pos.side, `${pos.side > 0 ? "B" : "S"} ${num(pos.entry)}`);
    out.sort((a, b) => a.time - b.time);
    markersApi.setMarkers(out);
  }

  function chartXY(e) {
    const r = el.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    const inPane = x >= 0 && x <= rp.chart.timeScale().width() && y >= 0 && y <= rp.chart.panes()[0].getHeight();
    return { x, y, inPane };
  }
  const priceAt = (y) => { const p = series.coordinateToPrice(y); return p === null ? null : +p.toFixed(2); };
  function setLevel(key, price) {
    const lv = levels();
    lv[key] = price;
    syncInputs(); drawLines(); renderLive();
  }
  function nearLevel(y) {
    const lv = levels();
    for (const key of ["sl", "tp"]) {
      if (lv[key] == null) continue;
      const c = series.priceToCoordinate(lv[key]);
      if (c !== null && Math.abs(c - y) <= 6) return key;
    }
    return null;
  }
  // Registered before the drawing layer (capture phase), so picking / dragging a level wins over drawings.
  function onDown(e) {
    if (!rp?.bars.length || e.button !== 0) return;
    const { y, inPane } = chartXY(e);
    if (!inPane) return;
    if (pick) { setLevel(pick, priceAt(y)); pick = null; syncPick(); stop(e); return; }
    const key = nearLevel(y);
    if (key && window.DrawingLayer?.tool === "cursor" && !e.shiftKey) { drag = key; stop(e); }
  }
  const stop = (e) => { e.stopImmediatePropagation(); e.preventDefault(); };
  function onMove(e) {
    if (!rp?.bars.length) return;
    const { y, inPane } = chartXY(e);
    if (drag) { const p = priceAt(y); if (p !== null) setLevel(drag, p); return; }
    el.style.cursor = pick && inPane ? "crosshair" : inPane && nearLevel(y) ? "ns-resize" : "";
  }

  // ---- panel ----
  function build() {
    $("trade").innerHTML = `
      <div class="tr-head">Paper trade <span class="muted" title="Shortcuts">B buy · S sell · X close</span></div>
      <div class="tr-grid">
        <label for="tr-qty">Qty</label><input id="tr-qty" type="number" min="1" step="1"><span></span>
        <label for="tr-risk" title="If set (₹) and a stop is set: qty = risk ÷ (entry − stop)">Risk ₹</label><input id="tr-risk" type="number" min="0" step="100" placeholder="off - use Qty"><span></span>
        <label for="tr-cost" title="Brokerage + slippage per side, % of value">Cost %/side</label><input id="tr-cost" type="number" min="0" step="0.01"><span></span>
        <label for="tr-sl" class="sl">Stop</label><input id="tr-sl" type="number" step="0.05" placeholder="none"><button id="tr-pick-sl" title="Click the chart to set the stop">⌖</button>
        <label for="tr-tp" class="tp">Target</label><input id="tr-tp" type="number" step="0.05" placeholder="none"><button id="tr-pick-tp" title="Click the chart to set the target">⌖</button>
      </div>
      <div class="tr-btns">
        <button id="tr-buy" class="buy">Buy</button><button id="tr-sell" class="sell">Sell</button><button id="tr-close">Close</button>
      </div>
      <div id="tr-msg"></div>
      <div id="tr-live"></div>
      <div class="tr-sub">Results <span><button id="tr-csv" title="Download all trades as CSV">CSV</button><button id="tr-clear" title="Delete all trades">Clear</button></span></div>
      <div id="tr-stats"></div>
      <div id="tr-log"></div>
      <div class="muted tr-note">Drag the SL / TP lines on the chart to move them.</div>`;
    const bind = (id, key) => {
      $(id).value = settings[key];
      $(id).onchange = (e) => { settings[key] = e.target.value === "" ? "" : +e.target.value; saveSettings(); renderLive(); renderStats(); };
    };
    bind("tr-qty", "qty"); bind("tr-risk", "risk"); bind("tr-cost", "cost");
    for (const key of ["sl", "tp"]) {
      $(`tr-${key}`).onchange = (e) => setLevel(key, e.target.value === "" ? null : +e.target.value);
      $(`tr-pick-${key}`).onclick = () => { pick = pick === key ? null : key; syncPick(); };
    }
    $("tr-buy").onclick = () => order(1);
    $("tr-sell").onclick = () => order(-1);
    $("tr-close").onclick = () => close("manual");
    $("tr-csv").onclick = exportCsv;
    $("tr-clear").onclick = () => { if (trades.length && confirm(`Delete all ${trades.length} paper trades?`)) { trades = []; saveTrades(); renderLog(); drawMarkers(); } };
  }
  function syncPick() {
    $("tr-pick-sl").classList.toggle("active", pick === "sl");
    $("tr-pick-tp").classList.toggle("active", pick === "tp");
    setMsg(""); if (pick) setMsg(`Click the chart to set the ${pick === "sl" ? "stop" : "target"}`, "live");
  }
  function syncInputs() {
    const lv = levels();
    for (const key of ["sl", "tp"]) if (document.activeElement !== $(`tr-${key}`)) $(`tr-${key}`).value = lv[key] ?? "";
  }

  function renderLive() {
    if (!el) return;
    const front = rp?.bars.length ? atFront() : true;
    $("tr-buy").textContent = pos?.side < 0 ? "Buy (cover)" : "Buy";
    $("tr-sell").textContent = pos?.side > 0 ? "Sell (exit)" : "Sell";
    for (const id of ["tr-buy", "tr-sell"]) $(id).disabled = !rp?.bars.length || !front;
    $("tr-close").disabled = !pos || !front;
    let html = "";
    if (pos) {
      const px = rp.barAt(rp.cursor).close, p = pnlOf(pos, px);
      html = `<div class="tr-pos ${pos.side > 0 ? "long" : "short"}">${pos.side > 0 ? "LONG" : "SHORT"} ${pos.qty} @ ${num(pos.entry)}</div>
        <div>P&amp;L <b class="${cls(p.net)}">${sgn(p.net)}</b>${p.r !== null ? ` <span class="${cls(p.r)}">(${sgn(p.r)} R)</span>` : ""}
        <span class="muted">· costs ${num(p.cost)}</span></div>
        <div class="muted">${pos.sl != null ? `risk ${num(Math.abs(pos.entry - pos.sl) * pos.qty)}` : "no stop"}${pos.tp != null && pos.sl != null
          ? ` · R:R 1:${num(Math.abs(pos.tp - pos.entry) / Math.abs(pos.entry - pos.sl), 1)}` : ""} · since ${when(pos.entryTime)}</div>`;
      if (!front) html += `<div class="warn">Viewing the past - P&amp;L at the shown candle</div>`;
    } else if (rp?.bars.length) {
      const px = shown().close, { sl, tp } = plan;
      html = `<div class="muted">Flat · price ${num(px)}</div>`;
      if (sl != null) {
        const qty = +settings.risk > 0 ? Math.max(1, Math.floor(+settings.risk / Math.abs(px - sl))) : Math.max(1, Math.floor(settings.qty) || 1);
        html += `<div class="muted">Planned: ${qty} qty, risk ${num(Math.abs(px - sl) * qty)}${tp != null ? `, R:R 1:${num(Math.abs(tp - px) / Math.abs(px - sl), 1)}` : ""}</div>`;
      }
      if (!front) html += `<div class="warn">Viewing the past - trading paused</div>`;
    }
    $("tr-live").innerHTML = html;
  }

  function renderStats() {
    const n = trades.length;
    if (!n) { $("tr-stats").innerHTML = `<div class="muted">No trades yet.</div>`; return; }
    const wins = trades.filter((t) => t.pnl > 0), losses = trades.filter((t) => t.pnl <= 0);
    const net = trades.reduce((s, t) => s + t.pnl, 0);
    const gw = wins.reduce((s, t) => s + t.pnl, 0), gl = -losses.reduce((s, t) => s + t.pnl, 0);
    const rs = trades.filter((t) => t.r !== null), avgR = rs.length ? rs.reduce((s, t) => s + t.r, 0) / rs.length : null;
    let peak = 0, eq = 0, dd = 0;
    for (const t of trades) { eq += t.pnl; peak = Math.max(peak, eq); dd = Math.min(dd, eq - peak); }
    const row = (k, v) => `<div><span class="muted">${k}</span> ${v}</div>`;
    $("tr-stats").innerHTML = `<div class="tr-stats">` +
      row("Trades", n) + row("Win rate", `${num((wins.length / n) * 100, 0)}%`) +
      row("Net", `<b class="${cls(net)}">${sgn(net)}</b>`) + row("Per trade", `<span class="${cls(net)}">${sgn(net / n)}</span>`) +
      row("Avg R", avgR === null ? "–" : `<span class="${cls(avgR)}">${sgn(avgR)}</span>`) +
      row("Profit factor", gl > 0 ? num(gw / gl) : "∞") + row("Max DD", `<span class="down">${num(dd)}</span>`) +
      row("Avg win / loss", `${num(wins.length ? gw / wins.length : 0)} / ${num(losses.length ? gl / losses.length : 0)}`) + `</div>`;
  }
  function renderLog() {
    renderStats();
    $("tr-log").innerHTML = `<table><tr><th>Exit</th><th>Side</th><th>Entry→Exit</th><th>P&amp;L</th><th>R</th></tr>` +
      trades.slice(-100).reverse().map((t) =>
        `<tr title="${t.symbol} ${t.interval} · ${t.qty} qty · in ${when(t.entryTime)} · ${t.reason}${t.mfe !== null ? ` · MFE ${t.mfe}R / MAE ${t.mae}R` : ""}">
          <td>${when(t.exitTime).slice(5)}</td><td class="${t.side > 0 ? "up" : "down"}">${t.side > 0 ? "L" : "S"}</td>
          <td>${num(t.entry)}→${num(t.exit)}</td><td class="${cls(t.pnl)}">${sgn(t.pnl, 0)}</td><td>${t.r === null ? "–" : sgn(t.r, 1)}</td></tr>`).join("") +
      `</table>`;
  }

  function exportCsv() {
    const cols = ["symbol", "interval", "side", "qty", "entryTime", "entry", "initSl", "tp", "exitTime", "exit", "reason", "pnl", "cost", "r", "mfe", "mae"];
    const val = (t, c) => (c.endsWith("Time") ? when(t[c]) : c === "side" ? (t.side > 0 ? "long" : "short") : t[c] ?? "");
    const csv = [cols.join(","), ...trades.map((t) => cols.map((c) => JSON.stringify(val(t, c))).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `paper_trades_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  }

  function render() { syncInputs(); drawLines(); drawMarkers(); renderLive(); }

  // ---- public ----
  function init(replay, candleSeries, chartEl) {
    rp = replay; series = candleSeries; el = chartEl;
    markersApi = LightweightCharts.createSeriesMarkers(series, []);
    build();
    el.addEventListener("mousedown", onDown, true);
    el.addEventListener("pointerdown", (e) => {
      if (!rp?.bars.length) return;
      const { y, inPane } = chartXY(e);
      if (inPane && (pick || (nearLevel(y) && window.DrawingLayer?.tool === "cursor" && !e.shiftKey))) e.stopImmediatePropagation();
    }, true);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", () => { drag = null; });
    setVisible(settings.open);
    renderLog();
  }
  // Called after every replay step (before the status line is written).
  function update() {
    if (rp.bars !== lastBars) { lastBars = rp.bars; onLoad(); }
    const ord = ordinal();
    if (ord > hw) { if (pos) advance(); hw = ord; }
    render();
  }
  function setVisible(v) {
    settings.open = v; saveSettings();
    $("trade").style.display = v ? "" : "none";
    $("trade-btn")?.classList.toggle("active", v);
  }
  function onKey(e) {
    if (e.ctrlKey || e.altKey || e.metaKey) return false;
    if (e.code === "KeyB") { order(1); return true; }
    if (e.code === "KeyS") { order(-1); return true; }
    if (e.code === "KeyX") { close("manual"); return true; }
    if (e.code === "Escape" && pick) { pick = null; syncPick(); return true; }
    return false;
  }

  return { init, update, onKey, setVisible, fill, get visible() { return settings.open; },
    get position() { return pos; }, get trades() { return trades; } };
})();
