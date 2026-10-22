// Nifty options: option chain at the replay time, a premium pane for one contract, and paper trading (buy CE/PE).
//
// Clock = the time of the state on screen: the end of the current (or forming) Nifty candle, or the server time in
// live mode. The chain, the premium pane and fills only use data that ended by that clock (NO-LOOKAHEAD).
// Contracts are symbols NIFTY_<YYYYMMDD>_<strike><CE|PE>: REAL = recorded Angel One candles, SYNTH = Black-Scholes on
// Nifty with India VIX as volatility (see options.py for what that misses).
window.OptionsLayer = (() => {
  const STORE = "replay.opt.trades.v1", SETTINGS = "replay.opt.settings.v1";
  const BLUE = "#2962ff", RED = "#ef5350", GREEN = "#26a69a";
  const $ = (id) => document.getElementById(id);
  const LW = LightweightCharts;

  let rp = null, chart = null, series = null, markers = null;
  let settings = { open: false, lots: 1, perOrder: 20, chargesPct: 0.1, slip: 0.1, n: 8, expiry: null, sel: null };
  let trades = [], pos = null, plan = { sl: null, tp: null };
  let expiries = [], expDay = null;              // expiries listed at the clock's day
  let chainData = null, chainBusy = false, chainWant = null, chainFailed = null;
  // Premium-pane window for the selected contract: bars/ends from /api/bars, forming partials cached by bar time.
  let ow = { key: null, bars: [], ends: [], loading: null, forms: new Map(), liveForming: null, done: false };
  let vis = [], visE = null;                     // option bars visible at clock visE (last one may be partial)
  let frontE = -Infinity, clockNow = null;
  const priceLines = {};

  try { settings = { ...settings, ...JSON.parse(localStorage.getItem(SETTINGS) || "{}") }; } catch {}
  try { trades = JSON.parse(localStorage.getItem(STORE) || "[]"); } catch {}
  const save = () => { try { localStorage.setItem(SETTINGS, JSON.stringify(settings)); } catch {} };
  const saveTrades = () => { try { localStorage.setItem(STORE, JSON.stringify(trades)); } catch {} };

  const num = (v, d = 2) => (v === null || v === undefined || !isFinite(v) ? "–"
    : v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const sgn = (v, d = 2) => (v > 0 ? "+" : "") + num(v, d);
  const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
  const hhmm = (t) => new Date(t * 1000).toISOString().slice(11, 16);
  const when = (t) => new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ");
  const iso = (t) => new Date(t * 1000).toISOString().slice(0, 16);
  const expEnd = (d) => Date.parse(`${d}T15:30:00Z`) / 1000;
  const parse = (s) => { const m = /^NIFTY_(\d{4})(\d\d)(\d\d)_(\d+)(CE|PE)$/.exec(s || ""); return m ? { expiry: `${m[1]}-${m[2]}-${m[3]}`, strike: +m[4], type: m[5] } : null; };
  const label = (s) => { const p = parse(s); return p ? `NIFTY ${p.strike} ${p.type} ${p.expiry.slice(8)}-${p.expiry.slice(5, 7)}` : s; };
  const setMsg = (text, c = "") => { const m = $("op-msg"); if (m) { m.textContent = text; m.className = c; } };
  const live = () => !!rp?.live?.on;

  function clock() {
    if (live()) return rp.live.now;
    if (!rp?.bars.length) return null;
    return rp.sub !== null ? rp.form.partial[rp.sub].end : rp.ends[rp.cursor];
  }

  // ---------------------------------------------------------------- data
  async function getJson(url, params) {
    const res = await fetch(`${url}?${new URLSearchParams(params)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    return data;
  }

  async function refreshExpiries(E) {
    const day = Math.floor(E / 86400);
    if (day === expDay && expiries.length && expEnd(expiries[0].expiry) > E) return;
    expDay = day;
    try {
      expiries = (await getJson("/api/expiries", { time: E, n: 6 })).expiries;
    } catch (e) { setMsg(e.message, "err"); return; }
    if (!expiries.some((x) => x.expiry === settings.expiry)) settings.expiry = expiries[0]?.expiry ?? null;
    $("op-exp").innerHTML = expiries.map((x) =>
      `<option value="${x.expiry}">${x.expiry}${x.monthly ? " M" : ""} · ${num(x.days, 1)}d${x.real ? "" : " · synth"}</option>`).join("");
    $("op-exp").value = settings.expiry ?? "";
  }

  // One chain request in flight; the newest clock wins.
  async function requestChain(E) {
    chainWant = E;
    if (chainBusy || !settings.expiry || !settings.open) return;
    chainBusy = true;
    try {
      while (chainWant !== null) {
        const t = chainWant; chainWant = null;
        const k = `${settings.expiry}|${settings.n}|${Math.floor(t / 60)}`;
        if (k === chainFailed) continue;            // don't hammer the server with a request that just failed
        try {
          chainData = await getJson("/api/chain", { time: t, expiry: settings.expiry, n: settings.n });
          chainFailed = null;
          renderChain();
        } catch (e) { $("op-chain").innerHTML = `<div class="err">${e.message}</div>`; chainData = null; chainFailed = k; }
      }
    } finally { chainBusy = false; }
  }

  function key() { return settings.sel && rp?.meta ? `${settings.sel}|${rp.meta.interval}` : null; }

  // lc = the live "last complete" bar this window was loaded for (live mode reloads when it changes).
  async function loadWindow(E, lc = null) {
    const k = key();
    if (!k) return;
    const req = (ow.loading = getJson("/api/bars", { symbol: settings.sel, interval: rp.meta.interval, start: iso(E), before: 600, after: 3000 }));
    try {
      const d = await req;
      if (ow.loading !== req || k !== key()) return;
      const p = parse(settings.sel);
      ow = { key: k, bars: d.bars, ends: d.ends, loading: null, forms: new Map(), liveForming: ow.liveForming,
        liveQuote: ow.liveQuote, lastComplete: lc, done: !d.ends.length || d.ends[d.ends.length - 1] >= expEnd(p.expiry) };
      shownK = -2;
      if (!live()) update();          // live: the caller (update -> liveTick) draws right after
    } catch (e) {
      if (ow.loading === req) { ow = { ...ow, key: k, bars: [], ends: [], loading: null, done: true }; setMsg(e.message, "err"); }
    }
  }

  function formingAt(time) {
    if (!ow.forms.has(time)) {
      ow.forms.set(time, getJson("/api/forming", { symbol: settings.sel, interval: rp.meta.interval, time, tick: "auto" })
        .catch(() => null));
    }
    return ow.forms.get(time);
  }

  // Index of the last bar with ends[k] <= E (-1 if none).
  function lastDone(E) {
    let lo = 0, hi = ow.ends.length - 1, k = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (ow.ends[m] <= E) { k = m; lo = m + 1; } else hi = m - 1; }
    return k;
  }

  // ---------------------------------------------------------------- premium pane
  let shownK = -2, pane = null;
  function ensureSeries() {
    if (series) return;
    pane = chart.panes().length;
    series = chart.addSeries(LW.CandlestickSeries, {
      upColor: GREEN, downColor: RED, borderVisible: false, wickUpColor: GREEN, wickDownColor: RED,
      priceFormat: { type: "price", precision: 2, minMove: 0.05 },
    }, pane);
    chart.panes()[pane].setStretchFactor(0.6);
    markers = LW.createSeriesMarkers(series, []);
  }
  function dropSeries() {
    if (!series) return;
    Object.keys(priceLines).forEach((k) => delete priceLines[k]);
    chart.removeSeries(series); series = null; markers = null; vis = [];
  }

  async function syncPane(E) {
    if (!settings.sel || !rp.meta) { dropSeries(); return; }
    ensureSeries();
    if (ow.key !== key()) { if (!ow.loading) loadWindow(E); return; }
    if (!ow.bars.length) return;
    if (!ow.done && E > ow.ends[ow.ends.length - 1] && !ow.loading) { loadWindow(E); return; }
    const k = lastDone(E);
    if (k !== shownK) { series.setData(ow.bars.slice(0, k + 1)); shownK = k; }
    vis = ow.bars.slice(0, k + 1);
    // The next bar has started but not ended: show its partial state at E.
    const nb = ow.bars[k + 1];
    let partial = null;
    if (live() && ow.liveForming && (!vis.length || ow.liveForming.time >= vis[vis.length - 1].time)) {
      partial = ow.liveForming;
      if (vis.length && partial.time === vis[vis.length - 1].time) vis.pop();     // partly built bar -> live state
    }
    else if (nb && nb.time < E && !live()) {
      const f = await formingAt(nb.time);
      if (clock() !== E || ow.key !== key()) return;
      const ps = f?.partial?.filter((p) => p.end <= E) ?? [];
      if (ps.length) { const p = ps[ps.length - 1]; partial = { time: nb.time, open: p.open, high: p.high, low: p.low, close: p.close, volume: p.volume }; }
    }
    if (partial) { series.update(partial); shownK = -2; vis.push(partial); }
    visE = E;
  }

  async function liveTick() {
    if (!settings.sel || ow.key !== key()) return;
    try {
      const r = await getJson("/api/live", { symbol: settings.sel, interval: rp.meta.interval });
      ow.liveForming = r.forming; ow.liveQuote = r.quote;
      if (r.last_complete !== ow.lastComplete) await loadWindow(r.now, r.last_complete);
    } catch (e) { setMsg(e.message, "err"); }
  }

  // ---------------------------------------------------------------- trading (buy only)
  const premium = () => (vis.length ? vis[vis.length - 1].close : null);
  const lotOf = () => chainData?.lot ?? expiries.find((x) => x.expiry === parse(settings.sel)?.expiry)?.lot ?? 65;
  function pnlOf(p, exit) {
    const gross = (exit - p.entry) * p.qty;
    const cost = 2 * settings.perOrder + (settings.chargesPct / 100) * (p.entry + exit) * p.qty;
    return { gross, cost, net: gross - cost };
  }

  function buy() {
    const E = clock();
    if (!settings.sel || E === null) return;
    if (E < frontE) { setMsg("Viewing the past - step forward to the latest candle to trade", "warn"); return; }
    if (pos) { setMsg("Already in a trade - exit first", "warn"); return; }
    if (visE !== E) { setMsg("Updating the premium - try again", "warn"); return; }
    if (live() && !ow.liveQuote) { setMsg("No fresh live quote for this contract - can't fill", "warn"); return; }
    const px = premium();
    if (px === null) { setMsg("No premium yet for this contract at this time", "warn"); return; }
    const entry = +(px + settings.slip).toFixed(2);
    if (plan.sl != null && plan.sl >= entry) { setMsg("Stop must be below the entry premium", "err"); return; }
    if (plan.tp != null && plan.tp <= entry) { setMsg("Target must be above the entry premium", "err"); return; }
    const lots = Math.max(1, Math.floor(settings.lots) || 1), lot = lotOf();
    const row = chainData?.rows.find((r) => r.symbol === settings.sel);
    const last = vis[vis.length - 1];
    pos = { symbol: settings.sel, side: 1, lots, lot, qty: lots * lot, entry, entryTime: E, entryBar: last.time,
      sl: plan.sl, tp: plan.tp, initSl: plan.sl, spot: chainData?.spot ?? null, delta: row?.delta ?? null,
      source: chainData?.rows.find((r) => r.symbol === settings.sel)?.source ?? "SYNTH",
      best: px, worst: px, seen: { time: last.time, high: last.high, low: last.low } };
    if (row?.source === "REAL" && row.last_trade && E - row.last_trade > 300) setMsg(`Filled - but the last real trade was at ${hhmm(row.last_trade)} (illiquid)`, "warn");
    else setMsg(`Bought ${lots} lot × ${lot} @ ${num(entry)} (incl. ${num(settings.slip)} slippage)`, "live");
    plan = { sl: null, tp: null };
    render();
  }

  function exit(reason, price, time, bar) {
    if (!pos) return;
    if (price === undefined) {
      const E = clock();
      if (E < frontE) { setMsg("Viewing the past - step forward to exit", "warn"); return; }
      if (visE !== E) { setMsg("Updating the premium - try again", "warn"); return; }
      if (live() && !ow.liveQuote) { setMsg("No fresh live quote for this contract - can't fill", "warn"); return; }
      price = +Math.max(0.05, premium() - settings.slip).toFixed(2); time = E; bar = vis[vis.length - 1]?.time;
    }
    const p = pnlOf(pos, price);
    const { seen, ...keep } = pos;
    trades.push({ ...keep, exit: price, exitTime: time, exitBar: bar, reason, pnl: +p.net.toFixed(2), cost: +p.cost.toFixed(2),
      mfe: +((pos.best - pos.entry) * pos.qty).toFixed(0), mae: +((pos.worst - pos.entry) * pos.qty).toFixed(0) });
    saveTrades();
    setMsg(`Exited (${reason}) @ ${num(price)}: ${sgn(p.net)} ₹`, p.net >= 0 ? "live" : "err");
    pos = null;
    render();
  }

  // Process option price action revealed since the last processed state (same rules as trade.js).
  function advance(E) {
    if (!pos || pos.symbol !== settings.sel || !vis.length) return;
    let i = vis.findIndex((b) => b.time === pos.seen.time);
    let seen = pos.seen;
    if (i < 0) { i = vis.findIndex((b) => b.time > pos.seen.time); seen = null; if (i < 0) return; }
    for (; pos && i < vis.length; i++) {
      const b = vis[i], sn = b.time === pos.seen.time ? seen : null;
      const f = TradeLayer.fill(pos, b, sn);
      if (f) {
        const px = f.reason.startsWith("stop") ? Math.max(0.05, f.price - settings.slip) : f.price;
        exit(f.reason, +px.toFixed(2), i === vis.length - 1 ? E : ow.ends[i] ?? E, b.time);
        return;
      }
      pos.best = Math.max(pos.best, b.high); pos.worst = Math.min(pos.worst, b.low);
      pos.seen = { time: b.time, high: b.high, low: b.low };
    }
    const p = parse(pos.symbol);
    if (E >= expEnd(p.expiry)) exit("expiry (settled)", vis[vis.length - 1].close, E, vis[vis.length - 1].time);
  }

  // ---------------------------------------------------------------- panel
  function build() {
    $("opt").innerHTML = `
      <div class="tr-head">Nifty options <span class="muted" id="op-src"></span></div>
      <div class="op-top"><select id="op-exp" title="Expiry (M = monthly)"></select>
        <label title="Strikes each side of ATM">± <input id="op-n" type="number" min="2" max="20" step="1"></label></div>
      <div id="op-head" class="muted"></div>
      <div id="op-chain"></div>
      <div class="tr-sub">Contract <span><button id="op-main" title="Open this contract on the main chart (indicators, drawings)">Main chart</button><button id="op-unsel" title="Hide the premium pane">✕</button></span></div>
      <div id="op-sel" class="muted">Click a CE or PE price in the chain.</div>
      <div class="tr-grid">
        <label for="op-lots">Lots</label><input id="op-lots" type="number" min="1" step="1"><span></span>
        <label for="op-sl" class="sl" title="Stop on the premium">Stop ₹</label><input id="op-sl" type="number" step="0.05" placeholder="none"><span></span>
        <label for="op-tp" class="tp" title="Target on the premium">Target ₹</label><input id="op-tp" type="number" step="0.05" placeholder="none"><span></span>
      </div>
      <div class="tr-btns op-btns"><button id="op-buy" class="buy">Buy</button><button id="op-exit">Exit</button></div>
      <div id="op-msg"></div>
      <div id="op-live"></div>
      <div class="tr-sub">Option trades <span><button id="op-csv">CSV</button><button id="op-clear">Clear</button></span></div>
      <div id="op-stats"></div>
      <div id="op-log"></div>
      <details class="muted op-costs"><summary>Costs (estimates - edit)</summary>
        <div class="tr-grid">
          <label for="op-po" title="Brokerage per executed order">₹ / order</label><input id="op-po" type="number" min="0" step="1"><span></span>
          <label for="op-ch" title="STT, exchange, SEBI, stamp, GST as % of premium turnover (both sides)">Charges %</label><input id="op-ch" type="number" min="0" step="0.01"><span></span>
          <label for="op-sp" title="Slippage per side in premium points (you buy higher, sell lower)">Slippage ₹</label><input id="op-sp" type="number" min="0" step="0.05"><span></span>
        </div></details>`;
    const bind = (id, k) => { $(id).value = settings[k]; $(id).onchange = (e) => { settings[k] = +e.target.value; save(); renderLive(); }; };
    bind("op-lots", "lots"); bind("op-po", "perOrder"); bind("op-ch", "chargesPct"); bind("op-sp", "slip");
    $("op-n").value = settings.n;
    $("op-n").onchange = (e) => { settings.n = Math.min(20, Math.max(2, +e.target.value || 8)); save(); update(); };
    $("op-exp").onchange = (e) => { settings.expiry = e.target.value; save(); update(); };
    for (const k of ["sl", "tp"]) $(`op-${k}`).onchange = (e) => { (pos || plan)[k] = e.target.value === "" ? null : +e.target.value; drawLines(); renderLive(); };
    $("op-buy").onclick = buy;
    $("op-exit").onclick = () => exit("manual");
    $("op-unsel").onclick = () => select(null);
    $("op-main").onclick = () => settings.sel && window.ReplayUI?.openSymbol(settings.sel);
    $("op-csv").onclick = exportCsv;
    $("op-clear").onclick = () => { if (trades.length && confirm(`Delete all ${trades.length} option trades?`)) { trades = []; saveTrades(); render(); } };
    $("op-chain").onclick = (e) => { const td = e.target.closest("td[data-sym]"); if (td) select(td.dataset.sym); };
  }

  function select(sym) {
    if (pos && sym !== pos.symbol && sym !== null) { setMsg("Exit the open trade before switching contract", "warn"); return; }
    settings.sel = sym; save();
    ow = { key: null, bars: [], ends: [], loading: null, forms: new Map(), liveForming: null, done: false };
    shownK = -2;
    if (!sym) dropSeries();
    update();
  }

  function renderChain() {
    const c = chainData;
    if (!c) return;
    $("op-head").innerHTML = `Spot <b>${num(c.spot)}</b> · VIX ${num(c.vix)} · ${num(c.days, 2)} days · lot ${c.lot}`;
    const real = c.rows.filter((r) => r.source === "REAL").length;
    $("op-src").innerHTML = real ? (real === c.rows.length ? `<span class="badge real">REAL</span>` : `<span class="badge mixed">REAL+SYNTH</span>`)
      : `<span class="badge synth">SYNTH</span>`;
    const by = {};
    for (const r of c.rows) (by[r.strike] ??= {})[r.type] = r;
    const strikes = Object.keys(by).map(Number).sort((a, b) => a - b);
    const cell = (r, itm) => {
      const c0 = itm ? ' class="itm muted"' : ' class="muted"';
      if (!r) return `<td${c0}></td><td${c0}></td><td${c0}></td>`;
      const px = r.ltp ?? r.model, s = r.source === "SYNTH";
      const tip = `${label(r.symbol)}
${s ? "synthetic (Black-Scholes, VIX)" : `last trade ${r.last_trade ? hhmm(r.last_trade) : "-"} · model ${num(r.model)}`}` +
        `
Δ ${r.delta} · θ ${num(r.theta)}/day · vega ${num(r.vega)} · γ ${r.gamma}${r.iv != null ? ` · IV ${r.iv}%` : ""}`;
      const oi = r.oi != null ? num(r.oi / 1e5, 1) : "";
      const stale = !s && (!r.last_trade || c.time - r.last_trade > 600);
      const p = `<td data-sym="${r.symbol}" class="px${itm ? " itm" : ""}${r.symbol === settings.sel ? " sel" : ""}${s ? " syn" : ""}${stale ? " stale" : ""}" title="${stale ? "STALE - no trade in the last 10 min\n" : ""}${tip}">${num(px)}</td>`;
      return r.type === "CE" ? `<td${c0}>${oi}</td><td${c0}>${num(r.delta, 2)}</td>${p}`
        : `${p}<td${c0}>${num(r.delta, 2)}</td><td${c0}>${oi}</td>`;
    };
    $("op-chain").innerHTML = `<table class="oc"><tr><th>OI L</th><th>Δ</th><th>CE</th><th>Strike</th><th>PE</th><th>Δ</th><th>OI L</th></tr>` +
      strikes.map((k) => `<tr class="${k === c.atm ? "atm" : ""}">${cell(by[k].CE, k < c.spot)}<td class="k">${k}</td>${cell(by[k].PE, k > c.spot)}</tr>`).join("") +
      `</table><div class="muted op-note">Shaded = in the money · click a price to chart / trade it · hover for Greeks · <i>italic</i> = synthetic · grey = no trade for 10+ min (stale)</div>`;
    renderSel();
  }

  function renderSel() {
    const s = settings.sel;
    if (!s) { $("op-sel").innerHTML = "Click a CE or PE price in the chain."; return; }
    const r = chainData?.rows.find((x) => x.symbol === s);
    const px = premium();
    let html = `<b>${label(s)}</b> <span class="badge ${r?.source === "REAL" ? "real" : "synth"}">${r?.source ?? (ow.bars.length ? "" : "…")}</span>`;
    html += ` · <b>${num(px)}</b>`;
    if (r) {
      const lot = lotOf();
      html += `<div>Δ ${num(r.delta, 2)} · θ <span class="down">${num(r.theta)}</span>/day (₹${num(r.theta * lot, 0)}/lot) · vega ${num(r.vega)}${r.iv != null ? ` · IV ${num(r.iv)}%` : ""}</div>`;
      html += `<div>1 lot = ₹${num((px ?? 0) * lot, 0)} · Nifty +50 ≈ ${sgn(r.delta * 50)} premium</div>`;
    } else if (!r && chainData) html += `<div class="warn">Not in the chain shown (strike / expiry changed).</div>`;
    $("op-sel").innerHTML = html;
  }

  function renderLive() {
    const E = clock(), front = E === null || E >= frontE;
    $("op-buy").disabled = !settings.sel || !!pos || !front || premium() === null;
    $("op-exit").disabled = !pos || !front;
    for (const k of ["sl", "tp"]) if (document.activeElement !== $(`op-${k}`)) $(`op-${k}`).value = (pos || plan)[k] ?? "";
    let html = "";
    if (pos) {
      const px = pos.symbol === settings.sel ? premium() : null;
      const p = px === null ? null : pnlOf(pos, Math.max(0.05, px - settings.slip));
      html = `<div class="tr-pos long">LONG ${pos.lots} × ${pos.lot} ${label(pos.symbol)} @ ${num(pos.entry)}</div>`;
      if (p) html += `<div>P&amp;L <b class="${cls(p.net)}">${sgn(p.net)} ₹</b> <span class="muted">· premium ${num(px)} (${sgn(((px - pos.entry) / pos.entry) * 100, 1)}%) · costs ${num(p.cost, 0)}</span></div>`;
      html += `<div class="muted">${pos.sl != null ? `max loss ≈ ₹${num((pos.entry - pos.sl) * pos.qty, 0)}` : `no stop - max loss ₹${num(pos.entry * pos.qty, 0)} (premium)`} · since ${when(pos.entryTime)}</div>`;
      if (!front) html += `<div class="warn">Viewing the past</div>`;
    } else if (settings.sel && premium() !== null) {
      const cost = (premium() + settings.slip) * Math.max(1, settings.lots) * lotOf();
      html = `<div class="muted">Buy ${settings.lots} lot = ₹${num(cost, 0)} premium${plan.sl != null ? ` · risk ≈ ₹${num((premium() + settings.slip - plan.sl) * settings.lots * lotOf(), 0)}` : ""}</div>`;
      if (!front) html += `<div class="warn">Viewing the past - trading paused</div>`;
    }
    $("op-live").innerHTML = html;
  }

  function renderStats() {
    const n = trades.length;
    if (!n) { $("op-stats").innerHTML = `<div class="muted">No option trades yet.</div>`; $("op-log").innerHTML = ""; return; }
    const wins = trades.filter((t) => t.pnl > 0), net = trades.reduce((s, t) => s + t.pnl, 0);
    const gw = wins.reduce((s, t) => s + t.pnl, 0), gl = -trades.filter((t) => t.pnl <= 0).reduce((s, t) => s + t.pnl, 0);
    const costs = trades.reduce((s, t) => s + t.cost, 0);
    const row = (k, v) => `<div><span class="muted">${k}</span> ${v}</div>`;
    $("op-stats").innerHTML = `<div class="tr-stats">` + row("Trades", n) + row("Win rate", `${num((wins.length / n) * 100, 0)}%`) +
      row("Net ₹", `<b class="${cls(net)}">${sgn(net, 0)}</b>`) + row("Per trade", `<span class="${cls(net)}">${sgn(net / n, 0)}</span>`) +
      row("Profit factor", gl > 0 ? num(gw / gl) : "∞") + row("Costs paid", num(costs, 0)) + `</div>`;
    $("op-log").innerHTML = `<table><tr><th>Exit</th><th>Contract</th><th>In→Out</th><th>₹</th></tr>` +
      trades.slice(-100).reverse().map((t) => `<tr title="${t.lots} lot × ${t.lot} · in ${when(t.entryTime)} · ${t.reason} · ${t.source}${t.spot ? ` · spot at entry ${num(t.spot)}` : ""} · best ${t.mfe} / worst ${t.mae} ₹">
        <td>${when(t.exitTime).slice(5)}</td><td>${label(t.symbol).replace("NIFTY ", "")}</td><td>${num(t.entry)}→${num(t.exit)}</td>
        <td class="${cls(t.pnl)}">${sgn(t.pnl, 0)}</td></tr>`).join("") + `</table>`;
  }

  function exportCsv() {
    const cols = ["symbol", "source", "lots", "lot", "qty", "entryTime", "entry", "initSl", "tp", "exitTime", "exit", "reason", "pnl", "cost", "spot", "delta", "mfe", "mae"];
    const val = (t, c) => (c.endsWith("Time") ? when(t[c]) : t[c] ?? "");
    const csv = [cols.join(","), ...trades.map((t) => cols.map((c) => JSON.stringify(val(t, c))).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `option_trades_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click(); URL.revokeObjectURL(a.href);
  }

  function setLine(k, price, color, title, style) {
    if (!series || price == null) { if (priceLines[k] && series) series.removePriceLine(priceLines[k]); delete priceLines[k]; return; }
    const o = { price, color, title, lineWidth: 1, lineStyle: style, axisLabelVisible: true };
    if (priceLines[k]) priceLines[k].applyOptions(o); else priceLines[k] = series.createPriceLine(o);
  }
  function drawLines() {
    const mine = pos && pos.symbol === settings.sel ? pos : null, lv = mine || plan;
    setLine("entry", mine?.entry, BLUE, mine ? `BUY ${mine.lots}L` : "", LW.LineStyle.Solid);
    setLine("sl", lv.sl, RED, "SL", LW.LineStyle.Dashed);
    setLine("tp", lv.tp, GREEN, "TP", LW.LineStyle.Dashed);
    if (!markers) return;
    const times = new Set(vis.map((b) => b.time)), out = [];
    for (const t of trades.filter((t) => t.symbol === settings.sel)) {
      if (times.has(t.entryBar)) out.push({ time: t.entryBar, position: "belowBar", shape: "arrowUp", color: BLUE, text: `B ${num(t.entry)}` });
      if (times.has(t.exitBar)) out.push({ time: t.exitBar, position: "aboveBar", shape: "arrowDown", color: "#ff9800", text: sgn(t.pnl, 0) });
    }
    if (mine && times.has(mine.entryBar)) out.push({ time: mine.entryBar, position: "belowBar", shape: "arrowUp", color: BLUE, text: `B ${num(mine.entry)}` });
    markers.setMarkers(out.sort((a, b) => a.time - b.time));
  }

  function render() { renderSel(); renderLive(); renderStats(); drawLines(); }

  // ---------------------------------------------------------------- public
  let seq = 0;
  async function update() {
    if (!rp?.meta || !settings.open) return;
    const E = clock();
    if (E === null) return;
    const my = ++seq;
    clockNow = E;
    await refreshExpiries(E);
    if (my !== seq) return;
    requestChain(E);
    if (live()) {
      if (ow.key !== key() && !ow.loading) await loadWindow(E);
      await liveTick();
    }
    await syncPane(E);
    if (my !== seq) return;
    if (E > frontE && visE === E) { advance(E); frontE = E; }
    render();
  }

  // A new main-chart window: rebuild the pane (live mode reloads every minute and only moves forward - keep it).
  function onLoad(isLive) { if (!isLive) { frontE = -Infinity; ow.key = null; } shownK = -2; }

  function init(replay) {
    rp = replay; chart = replay.chart;
    build();
    setVisible(settings.open);
    renderStats();
  }
  function setVisible(v) {
    settings.open = v; save();
    $("opt").style.display = v ? "" : "none";
    $("opt-btn")?.classList.toggle("active", v);
    if (!v) dropSeries(); else { ow.key = null; update(); }
  }

  return { init, update, onLoad, setVisible, get visible() { return settings.open; }, get position() { return pos; } };
})();
