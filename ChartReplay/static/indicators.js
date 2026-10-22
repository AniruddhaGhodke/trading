// Indicators layer: settings panel (saved in localStorage) + chart series fed by server-computed values.
//
// Values arrive aligned 1:1 with replay.bars, but only points [0..cursor] are ever handed to the chart
// (mount -> setData(slice), push(i) -> update(point i), pop -> pop(1)), same as the candles.
window.IndicatorLayer = (() => {
  const LW = LightweightCharts;
  const STORE = "replay.indicators.v1";
  const DEFAULTS = [
    { key: "ema", params: [20], on: true },
    { key: "ema", params: [50], on: true },
    { key: "sma", params: [200], on: false },
    { key: "bb", params: [20, 2], on: false },
    { key: "vwap", params: [], on: false },
    { key: "vol", params: [], on: true },
    { key: "rsi", params: [14], on: true },
    { key: "macd", params: [12, 26, 9], on: false },
  ];
  const UP = "rgba(38,166,154,0.5)", DOWN = "rgba(239,83,80,0.5)";
  // 2nd, 3rd... instance of the same moving average gets the next colour (1st keeps the server colour).
  const PALETTE = ["#f5a623", "#ff4081", "#00e676", "#ffee58", "#40c4ff", "#e040fb"];

  let registry = {};            // key -> {label, params:[{name, default}], pane}
  let config = load();
  let mounted = [];             // [{entry, lines:[{line, series}]}]
  let chart = null, onChange = () => {};

  function load() {
    try {
      const v = JSON.parse(localStorage.getItem(STORE));
      if (Array.isArray(v)) return v;
    } catch {}
    return structuredClone(DEFAULTS);
  }
  function save() { try { localStorage.setItem(STORE, JSON.stringify(config)); } catch {} }

  // "ema:20,rsi:14" for the active instances, in panel order.
  function query() {
    return config.filter((c) => c.on && registry[c.key]).map((c) => [c.key, ...c.params].join(":")).join(",");
  }

  function pointV(line, v, time, up) {
    if (v === null || v === undefined) return { time };
    const p = { time, value: v };
    if (line.kind === "hist_sign") p.color = v >= 0 ? UP : DOWN;
    if (line.kind === "hist_updown") p.color = up ? UP : DOWN;
    return p;
  }
  const point = (line, i, time) => pointV(line, line.values[i], time, line.up?.[i]);
  // Value of a line at bar i, using the partial state if bar i is the forming candle.
  let formNow = null;  // {i, k, f}
  function valueAt(entry, line, i) {
    if (formNow && formNow.i === i) return formNow.f.indicators?.[entry.id]?.[line.name]?.[formNow.k] ?? null;
    return line.values[i];
  }

  // ---- chart side ----
  function unmount() {
    for (const m of mounted) for (const l of m.lines) chart.removeSeries(l.series);
    mounted = [];  // empty panes are removed automatically by the library
  }

  function mount(ch, data, cursor) {
    chart = ch;
    formNow = null;
    unmount();
    const seen = {};
    for (const entry of data.indicators || []) {
      if (entry.skipped) continue;
      // Indices (e.g. NIFTY) have no volume: all zeros would draw a flat line, so skip with a note.
      if (entry.key === "vol" && !entry.lines[0].values.some((v) => v)) {
        entry.skipped = "Volume - not available for this symbol (index)";
        continue;
      }
      const nth = (seen[entry.key] = (seen[entry.key] ?? -1) + 1);
      if (nth > 0 && entry.lines.length === 1 && entry.lines[0].kind === "line") {
        const base = PALETTE.indexOf(entry.lines[0].color);
        entry.lines[0].color = PALETTE[(Math.max(base, 0) + nth) % PALETTE.length];
      }
      let pane = 0;
      if (entry.pane === "own") pane = chart.panes().length;
      const lines = entry.lines.map((line) => {
        const common = { priceLineVisible: false, lastValueVisible: entry.pane === "own", title: "" };
        let series;
        if (line.kind === "line") {
          series = chart.addSeries(LW.LineSeries, { ...common, color: line.color, lineWidth: 1.5,
            crosshairMarkerVisible: false }, pane);
        } else if (entry.key === "vol") {
          series = chart.addSeries(LW.HistogramSeries, { ...common, priceScaleId: "vol",
            priceFormat: { type: "volume" } }, 0);
          series.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        } else {
          series = chart.addSeries(LW.HistogramSeries, common, pane);
        }
        const pts = [];
        for (let i = 0; i <= cursor; i++) pts.push(point(line, i, data.bars[i].time));
        series.setData(pts);
        return { line, series };
      });
      for (const lvl of entry.levels) {
        lines[0].series.createPriceLine({ price: lvl, color: "#787b86", lineWidth: 1,
          lineStyle: LW.LineStyle.Dashed, axisLabelVisible: false });
      }
      if (entry.pane === "own") chart.panes()[pane].setStretchFactor(0.35);
      mounted.push({ entry, lines });
    }
  }

  function push(i, time) { formNow = null; for (const m of mounted) for (const l of m.lines) l.series.update(point(l.line, i, time)); }
  function pop() { formNow = null; for (const m of mounted) for (const l of m.lines) l.series.pop(1); }
  // Bar i is forming: show partial state k (values computed server-side from sub-bars 0..k only).
  function form(i, k, f, bar) {
    formNow = { i, k, f };
    const up = bar.close >= bar.open;
    for (const m of mounted) for (const l of m.lines) l.series.update(pointV(l.line, valueAt(m.entry, l.line, i), bar.time, up));
  }

  function legend(i) {
    // Prices / indicator values in full (abbreviating 22,512 as "22.5K" hid every change); volume abbreviated.
    const fmt = (v) => (v === null || v === undefined ? "–"
      : v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
    const fmtVol = (v) => (v === null || v === undefined ? "–"
      : v >= 1e7 ? (v / 1e7).toFixed(2) + " Cr" : v >= 1e5 ? (v / 1e5).toFixed(2) + " L" : v >= 1e3 ? (v / 1e3).toFixed(1) + " K" : String(v));
    return mounted.map(({ entry, lines }) => {
      const vals = lines.filter((l) => entry.key === "vol" || l.line.kind === "line")
        .map((l) => `<span style="color:${l.line.color || "#d1d4dc"}">${(entry.key === "vol" ? fmtVol : fmt)(valueAt(entry, l.line, i))}</span>`).join(" ");
      return `<div><b>${entry.label}</b> ${vals}</div>`;
    }).concat((window.replay.meta?.indicators || []).filter((e) => e.skipped)
      .map((e) => `<div class="muted">${e.skipped}</div>`)).join("");
  }

  // ---- settings panel ----
  function renderPanel(el) {
    const rows = config.map((c, idx) => {
      const r = registry[c.key];
      if (!r) return "";
      const inputs = r.params.map((p, j) =>
        `<input type="number" min="${p.name === "mult" ? 0.1 : 1}" step="${p.name === "mult" ? 0.1 : 1}" ` +
        `title="${p.name}" data-idx="${idx}" data-p="${j}" value="${c.params[j] ?? p.default}">`).join("");
      return `<div class="ind-row"><label><input type="checkbox" data-idx="${idx}" ${c.on ? "checked" : ""}> ` +
        `${r.label}</label>${inputs}<button data-del="${idx}" title="Remove">×</button></div>`;
    }).join("");
    const opts = Object.entries(registry).map(([k, r]) => `<option value="${k}">${r.label}</option>`).join("");
    el.innerHTML = rows + `<div class="ind-row"><select id="ind-add">${opts}</select>` +
      `<button id="ind-add-btn">+ Add</button><button id="ind-reset" title="Restore defaults">Reset</button></div>`;
  }

  function wirePanel(el) {
    el.addEventListener("change", (e) => {
      const t = e.target, idx = +t.dataset.idx;
      if (t.type === "checkbox") config[idx].on = t.checked;
      else if (t.type === "number") {
        const v = +t.value;
        if (!(v > 0)) { t.value = config[idx].params[+t.dataset.p]; return; }
        config[idx].params[+t.dataset.p] = v;
      } else return;
      save(); onChange();
    });
    el.addEventListener("click", (e) => {
      const t = e.target;
      if (t.dataset.del !== undefined) config.splice(+t.dataset.del, 1);
      else if (t.id === "ind-add-btn") {
        const key = el.querySelector("#ind-add").value;
        config.push({ key, params: registry[key].params.map((p) => p.default), on: true });
      } else if (t.id === "ind-reset") config = structuredClone(DEFAULTS);
      else return;
      save(); renderPanel(el); onChange();
    });
  }

  function init(meta, panelEl, changed) {
    registry = Object.fromEntries(meta.map((m) => [m.key, m]));
    for (const c of config) {  // fill params missing from older saved configs
      const r = registry[c.key];
      if (r) c.params = r.params.map((p, j) => c.params[j] ?? p.default);
    }
    onChange = changed;
    renderPanel(panelEl);
    wirePanel(panelEl);
  }

  return { init, query, mount, unmount, push, pop, form, legend, get mounted() { return mounted; }, get config() { return config; } };
})();
