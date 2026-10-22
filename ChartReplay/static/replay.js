// Bar replay: the server sends a window of bars; the client reveals them one at a time.
//
// NO-LOOKAHEAD RULE: anything drawn or computed (indicators, overlays, practice trades) must use
// replay.visible() == bars[0..cursor] only - never replay.bars directly. Two hooks:
//   replay.onStep(fn)  - fn(visibleBars, cursor) after every load / next / prev (UI, stats)
//   LAYERS             - chart layers with mount(chart, data, cursor) / push(i, time) / pop()
//                        (indicators.js, drawings.js)
(() => {
  const $ = (id) => document.getElementById(id);

  const chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { color: "#131722" }, textColor: "#d1d4dc" },
    grid: { vertLines: { color: "#1f2430" }, horzLines: { color: "#1f2430" } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 8 },
  });
  const candles = chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: "#26a69a", downColor: "#ef5350", borderVisible: false,
    wickUpColor: "#26a69a", wickDownColor: "#ef5350",
  });
  const LAYERS = [IndicatorLayer, DrawingLayer];

  // Forming candles: with a Tick size smaller than the interval, each candle is revealed in steps built
  // from its sub-bars (sub = index of the partial state shown; null = the candle is complete). The forming
  // candle only ever shows sub-bars 0..sub, and indicators use the same partial state (server-computed).
  const replay = {
    bars: [], ends: [], cursor: -1, sub: null, form: null, tick: "auto", minutes: {},
    playing: false, speedMs: 1000, timer: null, meta: null, busy: false, gen: 0, cache: new Map(),
    chart, series: candles, listeners: [], fetchForming: null,
    live: { on: false, now: null, timer: null, info: null },   // live mode: follow the market (Nifty + options)

    // Bar i as currently shown (the forming bar uses its partial state).
    barAt(i) {
      if (i !== this.cursor || this.sub === null) return this.bars[i];
      const p = this.form.partial[this.sub];
      return { time: this.bars[i].time, open: p.open, high: p.high, low: p.low, close: p.close, volume: p.volume };
    },
    visible() {
      const v = this.bars.slice(0, Math.max(0, this.cursor));
      if (this.cursor >= 0) v.push(this.barAt(this.cursor));
      return v;
    },
    onStep(fn) { this.listeners.push(fn); },
    _emit() { const v = this.visible(); this.listeners.forEach((fn) => fn(v, this.cursor)); },

    tickMinutes() { return this.tick === "auto" ? (this.meta?.has_1m ? 1 : 5) : this.minutes[this.tick]; },
    canForm() {
      return !!this.meta && this.tick !== "off" && this.tickMinutes() < this.minutes[this.meta.interval];
    },
    formingFor(i) {
      if (i < 0 || i >= this.bars.length) return Promise.resolve(null);
      const t = this.bars[i].time;
      if (!this.cache.has(t)) this.cache.set(t, this.fetchForming(t, this.tick).catch(() => null));
      return this.cache.get(t);
    },
    resetForming() { this.cache = new Map(); this.gen++; },

    load(data) {
      this.pause();
      this.resetForming();
      this.meta = data; this.bars = data.bars; this.ends = data.ends; this.cursor = data.cursor;
      this.sub = null; this.form = null; this.live.extra = false;
      window.OptionsLayer?.onLoad(this.live.on);
      candles.setData(this.visible());
      LAYERS.forEach((l) => l.mount(chart, data, this.cursor));
      chart.timeScale().scrollToRealTime();
      if (this.canForm()) this.formingFor(this.cursor + 1);
      this._emit();
    },
    // Live mode: add newly completed bars (and their indicator values) to the chart in place - no setData, no
    // layer remount, no scroll jump. Returns false when the new window doesn't continue this one (then reload).
    appendLive(data) {
      const old = this.meta, n = this.bars.length;
      // The live candle drawn after the last bar must go first: the chart can't insert bars before it.
      if (this.live.extra) { candles.pop(1); this.live.extra = false; }
      if (!old || !n || data.symbol !== old.symbol || data.interval !== old.interval || this.cursor !== n - 1 || this.sub !== null) return false;
      const oi = old.indicators || [], ni = data.indicators || [];
      if (oi.length !== ni.length || oi.some((e, k) => e.lines.length !== ni[k].lines.length)) return false;
      const j0 = data.bars.findIndex((b) => b.time === this.bars[n - 1].time);
      if (j0 < 0) return false;
      const lines = oi.flatMap((e, k) => e.lines.map((l, m) => [l, ni[k].lines[m]]));
      const same = (a, b) => a.open === b.open && a.high === b.high && a.low === b.low && a.close === b.close && a.volume === b.volume;
      for (let j = j0; j <= data.cursor; j++) {
        if (j === j0) {
          if (same(this.bars[n - 1], data.bars[j])) continue;
          this._popBar(); this.bars.pop(); this.ends.pop();                // partly built last bar: replace it
          for (const [l] of lines) { l.values.pop(); l.up?.pop(); }
        }
        this.bars.push(data.bars[j]); this.ends.push(data.ends[j]);
        for (const [l, nl] of lines) { l.values.push(nl.values[j]); if (l.up) l.up.push(nl.up?.[j]); }
        this._nextBar();
      }
      this._emit();
      return true;
    },
    // Swap layer data (e.g. indicator settings changed) for the SAME window, keeping cursor and zoom.
    async remount(data) {
      this.resetForming();
      this.meta.indicators = data.indicators;
      const forming = this.sub !== null;
      // A forming candle's completed indicator value must not be shown: mount up to the bar before it,
      // then re-apply the partial state with the new indicator settings.
      LAYERS.forEach((l) => l.mount(chart, { ...data, bars: this.bars }, forming ? this.cursor - 1 : this.cursor));
      if (forming) {
        const gen = this.gen;
        const f = await this.fetchForming(this.bars[this.cursor].time, this.form.tick).catch(() => null);
        if (gen !== this.gen || this.sub === null) return;
        if (f && f.partial.length === this.form.partial.length) { this.form = f; this._showPartial(); }
        else this._complete();
      }
      this._emit();
    },
    setTick(t) { this.tick = t; this.resetForming(); if (this.canForm()) this.formingFor(this.cursor + 1); this._emit(); },

    atEnd() { return this.cursor >= this.bars.length - 1 && this.sub === null; },
    _showPartial() {
      const b = this.barAt(this.cursor);
      candles.update(b);
      LAYERS.forEach((l) => l.form(this.cursor, this.sub, this.form, b));
    },
    _complete() {
      this.sub = null; this.form = null;
      const b = this.bars[this.cursor];
      candles.update(b);
      LAYERS.forEach((l) => l.push(this.cursor, b.time));
    },
    _nextBar() {
      this.cursor++;
      const b = this.bars[this.cursor];
      candles.update(b);
      LAYERS.forEach((l) => l.push(this.cursor, b.time));
    },
    _popBar() {
      this.cursor--; this.sub = null; this.form = null;
      candles.pop(1);
      LAYERS.forEach((l) => l.pop());
    },
    async _guard(fn) {
      if (this.busy) return null;
      this.busy = true;
      try { return await fn(); } finally { this.busy = false; }
    },

    // One step forward: the next tick of the forming candle, or the next candle.
    next() {
      return this._guard(async () => {
        if (this.sub !== null) {
          if (this.sub < this.form.partial.length - 2) { this.sub++; this._showPartial(); } else this._complete();
        } else {
          if (this.atEnd()) { this.pause(); return false; }
          let f = null;
          if (this.canForm()) {
            const gen = this.gen;
            f = await this.formingFor(this.cursor + 1);
            if (gen !== this.gen) return null;  // reloaded meanwhile
          }
          if (f && f.partial.length > 1) {
            this.cursor++; this.sub = 0; this.form = f; this._showPartial();
          } else this._nextBar();
          if (this.canForm()) { this.formingFor(this.cursor + 1); this.formingFor(this.cursor + 2); }  // prefetch
        }
        this._emit();
        return true;
      });
    },
    // One step back: the previous tick (stepping back into a completed candle re-opens its last partial).
    prev() {
      return this._guard(async () => {
        if (this.sub !== null) {
          if (this.sub > 0) { this.sub--; this._showPartial(); } else this._popBar();
        } else {
          if (this.cursor <= 0) return false;
          let f = null;
          if (this.canForm()) {
            const gen = this.gen;
            f = await this.formingFor(this.cursor);
            if (gen !== this.gen) return null;
          }
          if (f && f.partial.length > 1) { this.sub = f.partial.length - 2; this.form = f; this._showPartial(); }
          else this._popBar();
        }
        this._emit();
        return true;
      });
    },
    // Whole-candle steps (Shift+arrows): finish the forming candle / drop it.
    nextCandle() {
      return this._guard(async () => {
        if (this.sub !== null) this._complete();
        else if (this.atEnd()) return false;
        else this._nextBar();
        if (this.canForm()) this.formingFor(this.cursor + 1);
        this._emit();
        return true;
      });
    },
    prevCandle() {
      return this._guard(async () => {
        if (this.cursor <= 0) return false;
        this._popBar();
        this._emit();
        return true;
      });
    },

    // Speed = time per candle; while forming, that time is split across the candle's ticks.
    stepDelay() {
      if (this.sub !== null) return Math.max(30, this.speedMs / this.form.partial.length);
      if (this.canForm()) return Math.max(30, this.speedMs / (this.minutes[this.meta.interval] / this.tickMinutes()));
      return this.speedMs;
    },
    play() {
      if (this.playing || this.atEnd()) return;
      this.playing = true;
      const loop = async () => {
        if (!this.playing) return;
        const ok = await this.next();
        if (!this.playing) return;
        if (ok === false || this.atEnd()) { this.pause(); return; }
        this.timer = setTimeout(loop, this.stepDelay());
      };
      this.timer = setTimeout(loop, this.stepDelay());
      this._emit();
    },
    pause() {
      clearTimeout(this.timer); this.timer = null;
      if (this.playing) { this.playing = false; this._emit(); }
    },
    toggle() { this.playing ? this.pause() : this.play(); },
    setSpeed(ms) { this.speedMs = ms; },
  };
  window.replay = replay;

  // ---- UI wiring ----
  const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const fmt = (t) => { const d = new Date(t * 1000); return `${DAYS[d.getUTCDay()]} ${d.toISOString().slice(0, 16).replace("T", " ")}`; };
  const num = (v) => v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const vol = (v) => (v >= 1e7 ? (v / 1e7).toFixed(2) + " Cr" : v >= 1e5 ? (v / 1e5).toFixed(2) + " L" : v >= 1e3 ? (v / 1e3).toFixed(1) + " K" : String(v));
  const setMsg = (text, cls = "") => { $("msg").textContent = text; $("msg").className = cls; };

  // Quote strip + indicator legend for bar i: the hovered bar, or the current replay bar.
  let hoverIdx = null;
  function showBar(i) {
    const b = replay.barAt(i);
    if (!b) return;
    const p = replay.bars[i - 1], cls = b.close >= b.open ? "up" : "down";
    let chg = "";
    if (p) {
      const d = b.close - p.close, pct = (d / p.close) * 100, c = d >= 0 ? "up" : "down", s = d >= 0 ? "+" : "";
      chg = `<span class="${c}">${s}${num(d)} (${s}${pct.toFixed(2)}%)</span>`;
    }
    const kv = (k, v) => `<span class="k">${k}</span><span class="${cls}">${num(v)}</span>`;
    $("quote").classList.toggle("hovering", i !== replay.cursor);
    $("quote").innerHTML =
      `<span class="sym">${replay.meta.symbol.replace(/\.NS$/, "")}</span><span class="chip">${replay.meta.interval}</span>` +
      `<span class="time">${fmt(b.time)}</span>` +
      `<span class="ohlc">${kv("O", b.open)}${kv("H", b.high)}${kv("L", b.low)}${kv("C", b.close)}</span>${chg}` +
      `<span class="k">Vol</span><span class="vol">${vol(b.volume)}</span>`;
    $("legend").innerHTML = IndicatorLayer.legend(i) + PatternLayer.legend(i, replay);
  }
  const hhmm = (t) => new Date(t * 1000).toISOString().slice(11, 16);

  chart.subscribeCrosshairMove((param) => {
    if (!replay.bars.length) return;
    let i = null;
    if (param.point && param.logical !== undefined) {
      const k = Math.round(param.logical);
      if (k >= 0 && k <= replay.cursor) i = k;  // hovering the empty future area shows the current bar
    }
    hoverIdx = i;
    showBar(i ?? replay.cursor);
  });

  PatternLayer.init(candles);
  replay.onStep(() => PatternLayer.update(replay));  // registered first: showBar's legend reads its state
  replay.onStep(() => TradeLayer.update());
  replay.onStep(() => OptionsLayer.update());
  replay.onStep(() => AlertsLayer.update());
  replay.onStep((vis, cursor) => {
    const loaded = replay.bars.length > 0, lv = replay.live.on;
    $("prev").disabled = !loaded || cursor <= 0 || lv;
    $("next").disabled = !loaded || replay.atEnd() || lv;
    $("play").disabled = !loaded || (replay.atEnd() && !replay.playing) || lv;
    $("play").textContent = replay.playing ? "⏸" : "▶";
    if (!loaded) return;
    if (hoverIdx !== null && hoverIdx > cursor) hoverIdx = null;
    showBar(hoverIdx ?? cursor);
    if (lv) liveMsg();
    else if (replay.atEnd()) {
      // Resume point: Load again continues from this exact candle (start = its close time).
      $("start").value = new Date(replay.ends[cursor] * 1000).toISOString().slice(0, 16);
      setMsg(`bar ${cursor + 1}/${replay.bars.length} · end of loaded data - click Load to continue`, "warn");
    } else if (replay.sub !== null) {
      const f = replay.form;
      setMsg(`bar ${cursor + 1}/${replay.bars.length} · forming ${replay.sub + 1}/${f.partial.length} ` +
        `(${f.tick} ticks, to ${hhmm(f.partial[replay.sub].end)})`, "live");
    } else setMsg(`bar ${cursor + 1}/${replay.bars.length}`);
  });

  async function init() {
    const r = await fetch("/api/symbols").then((x) => x.json());
    $("symbol").innerHTML = r.symbols.map((s) => `<option>${s}</option>`).join("");
    if (r.symbols.includes("IDX_NSEI")) $("symbol").value = "IDX_NSEI";  // default: NIFTY 50 index, 5m
    $("interval").innerHTML = r.intervals.map((s) => `<option>${s}</option>`).join("");
    if (r.intervals.includes("5m")) $("interval").value = "5m";
    IndicatorLayer.init(r.indicators, $("ind-panel"), refreshIndicators);
    replay.minutes = r.minutes;
    let saved = "auto";
    try { saved = localStorage.getItem("replay.tick") || "auto"; } catch {}
    $("tick").innerHTML = ["off", ...r.ticks]
      .map((t) => `<option value="${t}">${t === "off" ? "Off" : t === "auto" ? "Auto" : t}</option>`).join("");
    $("tick").value = r.ticks.includes(saved) || saved === "off" ? saved : "auto";
    replay.tick = $("tick").value;
    syncTicks();
  }

  // Tick sizes must be smaller than the chart interval.
  function syncTicks() {
    const iv = replay.minutes[$("interval").value];
    for (const o of $("tick").options) o.disabled = !["off", "auto"].includes(o.value) && replay.minutes[o.value] >= iv;
    if ($("tick").selectedOptions[0]?.disabled) { $("tick").value = "auto"; replay.setTick("auto"); }
  }

  replay.fetchForming = async (time, tick) => {
    const q = { symbol: replay.meta.symbol, interval: replay.meta.interval, time, tick, ind: IndicatorLayer.query() };
    const res = await fetch(`/api/forming?${new URLSearchParams(q)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    return data;
  };

  TradeLayer.init(replay, candles, $("chart"));
  AlertsLayer.init(replay);
  OptionsLayer.init(replay);  // before DrawingLayer: its SL/TP dragging takes priority
  DrawingLayer.init(chart, candles, $("chart"), (tool) => {
    document.querySelectorAll("#tools [data-tool]").forEach((b) => b.classList.toggle("active", b.dataset.tool === tool));
  });
  document.querySelectorAll("#tools [data-tool]").forEach((b) => { b.onclick = () => DrawingLayer.setTool(b.dataset.tool); });
  $("tool-delete").onclick = () => DrawingLayer.deleteSelected();
  $("tool-clear").onclick = () => {
    if (DrawingLayer.drawings.length && confirm(`Delete all ${DrawingLayer.drawings.length} drawings on ${replay.meta.symbol}?`)) DrawingLayer.clearAll();
  };

  async function fetchBars(params) {
    const res = await fetch(`/api/bars?${new URLSearchParams({ ...params, ind: IndicatorLayer.query() })}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail);
    return data;
  }

  async function loadReplay() {
    stopLive();
    const params = { symbol: $("symbol").value, interval: $("interval").value, start: $("start").value };
    setMsg("Loading…");
    try {
      const data = await fetchBars(params);
      data.request = params;
      replay.load(data);
    } catch (e) { setMsg(`Error: ${e.message}`, "err"); }
  }

  // Indicator settings changed: refetch the same window (same request -> same bars) and swap only the layer.
  let refreshSeq = 0;
  async function refreshIndicators() {
    if (!replay.meta) return;
    const seq = ++refreshSeq;
    try {
      const data = await fetchBars(replay.meta.request);
      if (seq !== refreshSeq) return;  // a newer change is in flight
      if (data.lo !== replay.meta.lo || data.hi !== replay.meta.hi) { data.request = replay.meta.request; replay.load(data); return; }
      replay.remount(data);
    } catch (e) { setMsg(`Error: ${e.message}`, "err"); }
  }

  $("ind-btn").onclick = () => $("ind-panel").classList.toggle("open");
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#ind-panel, #ind-btn")) $("ind-panel").classList.remove("open");
  });

  $("load").onclick = loadReplay;
  $("play").onclick = () => replay.toggle();
  $("next").onclick = (e) => { replay.pause(); e.shiftKey ? replay.nextCandle() : replay.next(); };
  $("prev").onclick = (e) => { replay.pause(); e.shiftKey ? replay.prevCandle() : replay.prev(); };
  $("interval").onchange = syncTicks;
  $("tick").onchange = (e) => {
    replay.setTick(e.target.value);
    try { localStorage.setItem("replay.tick", e.target.value); } catch {}
  };
  const refreshLegend = () => { if (replay.bars.length) showBar(hoverIdx ?? replay.cursor); };
  const togglePatterns = () => { PatternLayer.setShow(!PatternLayer.show); $("pat-btn").classList.toggle("active", PatternLayer.show); refreshLegend(); };
  $("patterns").value = PatternLayer.mode;
  $("pat-btn").classList.toggle("active", PatternLayer.show);
  $("pat-btn").onclick = togglePatterns;
  $("patterns").onchange = (e) => {
    PatternLayer.setMode(e.target.value);
    if (!PatternLayer.show) togglePatterns(); else refreshLegend();
  };
  $("trade-btn").onclick = () => TradeLayer.setVisible(!TradeLayer.visible);
  $("opt-btn").onclick = () => OptionsLayer.setVisible(!OptionsLayer.visible);
  $("alerts-btn").onclick = () => AlertsLayer.setVisible(!AlertsLayer.visible);
  $("live-btn").onclick = () => (replay.live.on ? stopLive() : startLive());

  // ---- live mode: Nifty and Nifty options only; the server polls Angel One during market hours ----
  const LIVE_OK = (s) => s === "IDX_NSEI" || /^NIFTY_\d{8}_\d+(CE|PE)$/.test(s);
  const isoAt = (t) => new Date(t * 1000).toISOString().slice(0, 16);
  function liveMsg() {
    const i = replay.live.info || {};
    const parts = [`LIVE ${i.now ? new Date(i.now * 1000).toISOString().slice(11, 19) : ""}`];
    parts.push(i.market ? "market open" : "market closed - showing the latest data");
    if (i.last_quote) parts.push(`last tick ${new Date(i.last_quote * 1000).toISOString().slice(11, 19)}`);
    if (i.error) parts.push(i.error);
    if (!i.enabled) parts.push("live feed off (needs SwingSystem/.env)");
    setMsg(parts.join(" · "), i.error || !i.enabled ? "warn" : "live");
  }
  // Window up to the last COMPLETE bar (start = its nominal end), so no partly built bar is ever loaded.
  async function liveLoad(now, lastComplete, append = false) {
    const mins = replay.minutes[$("interval").value] || 5;
    const t = lastComplete ? Math.min(now, lastComplete + mins * 60) : now;
    const params = { symbol: $("symbol").value, interval: $("interval").value, start: isoAt(t), after: 0 };
    const data = await fetchBars(params);
    data.request = params;
    replay.live.now = now;
    if (!(append && replay.appendLive(data))) replay.load(data);
    $("start").value = isoAt(t);
  }
  async function liveTick() {
    if (!replay.live.on) return;
    try {
      const r = await fetch(`/api/live?${new URLSearchParams({ symbol: replay.meta.symbol, interval: replay.meta.interval })}`).then((x) => x.json());
      if (!replay.live.on) return;
      if (r.detail) throw new Error(r.detail);
      replay.live.info = r; replay.live.now = r.now;
      if (r.last_complete !== replay.live.lastComplete) { replay.live.lastComplete = r.last_complete; await liveLoad(r.now, r.last_complete, true); }
      // The unfinished bar (may replace a partly built last bar of the same time).
      const lastT = replay.bars.length ? replay.bars[replay.bars.length - 1].time : -1;
      if (r.forming && r.forming.time >= lastT) {
        candles.update(r.forming);
        if (r.forming.time > lastT) replay.live.extra = true;     // an extra bar after the last one (see appendLive)
      }
      OptionsLayer.update();
      AlertsLayer.update();
      liveMsg();
    } catch (e) { setMsg(`Live error: ${e.message}`, "err"); }
    replay.live.timer = setTimeout(liveTick, replay.live.info?.market ? 2000 : 15000);
  }
  async function startLive() {
    if (!LIVE_OK($("symbol").value)) { setMsg("Live data is only for IDX_NSEI (Nifty) and Nifty option contracts", "warn"); return; }
    replay.pause();
    try {
      const r = await fetch(`/api/live?${new URLSearchParams({ symbol: $("symbol").value, interval: $("interval").value })}`).then((x) => x.json());
      if (r.detail) throw new Error(r.detail);
      replay.live.on = true; replay.live.info = r; replay.live.lastComplete = r.last_complete;
      $("live-btn").classList.add("active");
      await liveLoad(r.now, r.last_complete);
      liveTick();
    } catch (e) { stopLive(); setMsg(`Live error: ${e.message}`, "err"); }
  }
  function stopLive() {
    if (!replay.live.on) return;
    replay.live.on = false; clearTimeout(replay.live.timer);
    $("live-btn").classList.remove("active");
    replay._emit();
  }
  // Open a symbol (e.g. an option contract from the chain) on the main chart at the current clock.
  window.ReplayUI = {
    openSymbol(sym) {
      if (![...$("symbol").options].some((o) => o.value === sym)) $("symbol").insertAdjacentHTML("beforeend", `<option>${sym}</option>`);
      const t = replay.live.on ? replay.live.now : replay.bars.length ? (replay.sub !== null ? replay.form.partial[replay.sub].end : replay.ends[replay.cursor]) : null;
      $("symbol").value = sym;
      if (replay.live.on) { liveLoad(replay.live.now, null).catch((e) => setMsg(`Error: ${e.message}`, "err")); return; }
      if (t !== null) $("start").value = isoAt(t);
      loadReplay();
    },
  };
  $("speed").onchange = (e) => replay.setSpeed(+e.target.value);
  document.addEventListener("keydown", (e) => {
    if (["INPUT", "SELECT"].includes(e.target.tagName) || !replay.bars.length) return;
    if (replay.live.on && ["Space", "ArrowRight", "ArrowLeft"].includes(e.code)) return;
    if (DrawingLayer.onKey(e)) return;
    if (TradeLayer.onKey(e)) { e.preventDefault(); return; }
    if (e.code === "KeyP" && !e.ctrlKey && !e.altKey && !e.metaKey) { togglePatterns(); return; }
    if (e.code === "Space") { e.preventDefault(); replay.toggle(); }
    else if (e.code === "ArrowRight") { replay.pause(); e.shiftKey ? replay.nextCandle() : replay.next(); }
    else if (e.code === "ArrowLeft") { replay.pause(); e.shiftKey ? replay.prevCandle() : replay.prev(); }
  });

  init();
})();
