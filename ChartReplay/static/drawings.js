// Drawing tools layer: horizontal / vertical line, trend line, ray, rectangle, ruler.
//
// Drawings are stored as {t: epoch seconds, p: price} points (not bar indexes), per symbol, in localStorage,
// so they survive reloads, new replay windows and interval switches. On screen, t is mapped to a
// (fractional) logical bar index via the loaded window, which also lets points sit right of the last
// revealed candle (the "future" area) without showing anything about future prices.
//
// Mouse: pick a tool, then click-click (or press-drag-release) on the price pane. One-point tools place on
// the first click. Cursor mode: click a drawing to select it, drag it to move, drag a handle to reshape.
// Keys: Esc cancel/deselect, Delete remove selected, Alt+T trend, Alt+H horizontal, Alt+V vertical,
// Alt+R rectangle, Shift+click ruler.
window.DrawingLayer = (() => {
  const STORE = "replay.drawings.v1";
  const COLOR = "#2962ff", SEL = "#ffffff", HIT = 6, HANDLE = 5;
  const POINTS = { hline: 1, vline: 1, trend: 2, ray: 2, rect: 2, ruler: 2 };

  let chart = null, series = null, requestUpdate = () => {}, el = null;
  let symbol = null, drawings = [], selected = null;
  let tool = "cursor", pending = null, hover = null, ruler = null, drag = null, onTool = () => {};

  // ---- persistence ----
  function readAll() { try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch { return {}; } }
  function save() {
    if (!symbol) return;
    try { const all = readAll(); all[symbol] = drawings; localStorage.setItem(STORE, JSON.stringify(all)); } catch {}
  }

  // ---- time <-> logical index (fractional), using the loaded window's bar times ----
  const bars = () => window.replay.bars;
  function step() {
    const b = bars(), d = [];
    for (let i = 1; i < Math.min(b.length, 50); i++) d.push(b[i].time - b[i - 1].time);
    d.sort((x, y) => x - y);
    return d.length ? d[0] : 300;  // smallest gap = the bar interval (larger gaps are nights / weekends)
  }
  function timeToLogical(t) {
    const b = bars(), n = b.length;
    if (!n) return null;
    if (t <= b[0].time) return (t - b[0].time) / step();
    if (t >= b[n - 1].time) return n - 1 + (t - b[n - 1].time) / step();
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (b[m].time <= t) lo = m; else hi = m; }
    return lo + (t - b[lo].time) / (b[hi].time - b[lo].time);
  }
  function logicalToTime(l) {
    const b = bars(), n = b.length;
    if (l <= 0) return b[0].time + l * step();
    if (l >= n - 1) return b[n - 1].time + (l - (n - 1)) * step();
    const i = Math.floor(l), f = l - i;
    return b[i].time + f * (b[i + 1].time - b[i].time);
  }
  // logicalToCoordinate() returns 0 for fractional logicals, so interpolate between the neighbouring bars.
  function logicalX(l) {
    const ts = chart.timeScale(), i = Math.floor(l), f = l - i;
    const x0 = ts.logicalToCoordinate(i);
    return f === 0 || x0 === null ? x0 : x0 + f * (ts.logicalToCoordinate(i + 1) - x0);
  }
  const X = (pt) => { const l = timeToLogical(pt.t); return l === null ? null : logicalX(l); };
  const Y = (pt) => series.priceToCoordinate(pt.p);
  function fromXY(x, y) {
    const l = chart.timeScale().coordinateToLogical(x), p = series.coordinateToPrice(y);
    if (l === null || p === null) return null;
    return { t: logicalToTime(Math.round(l)), p: +p.toFixed(2) };  // snap time to a bar, keep price free
  }

  // ---- geometry ----
  function segDist(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1, len = dx * dx + dy * dy;
    const k = len ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len)) : 0;
    return Math.hypot(px - (x1 + k * dx), py - (y1 + k * dy));
  }
  function rayEnd(x1, y1, x2, y2, w) {
    if (x2 === x1) return [x2, y2 > y1 ? 1e5 : -1e5];
    const k = (y2 - y1) / (x2 - x1), xe = x2 > x1 ? w : 0;
    return [xe, y1 + k * (xe - x1)];
  }
  function hit(x, y) {
    const w = chart.timeScale().width();
    for (let i = drawings.length - 1; i >= 0; i--) {
      const d = drawings[i], xs = d.pts.map(X), ys = d.pts.map(Y);
      if (xs.some((v) => v === null) || ys.some((v) => v === null)) continue;
      for (let h = 0; h < d.pts.length && d === selected; h++) {
        if (Math.hypot(x - xs[h], y - ys[h]) <= HANDLE + 3) return { d, handle: h };
      }
      let dist = Infinity;
      if (d.type === "hline") dist = Math.abs(y - ys[0]);
      else if (d.type === "vline") dist = Math.abs(x - xs[0]);
      else if (d.type === "trend") dist = segDist(x, y, xs[0], ys[0], xs[1], ys[1]);
      else if (d.type === "ray") { const [xe, ye] = rayEnd(xs[0], ys[0], xs[1], ys[1], w); dist = segDist(x, y, xs[0], ys[0], xe, ye); }
      else if (d.type === "rect") {
        const [l, r] = [Math.min(...xs), Math.max(...xs)], [t, b] = [Math.min(...ys), Math.max(...ys)];
        dist = x >= l - HIT && x <= r + HIT && y >= t - HIT && y <= b + HIT ? 0 : Infinity;
      }
      if (dist <= HIT) return { d, handle: null };
    }
    return null;
  }

  // ---- rendering ----
  function line(ctx, x1, y1, x2, y2) { ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }
  function handles(ctx, xs, ys) {
    ctx.fillStyle = "#131722"; ctx.strokeStyle = SEL; ctx.lineWidth = 1.5;
    xs.forEach((x, i) => { ctx.beginPath(); ctx.arc(x, ys[i], HANDLE, 0, 2 * Math.PI); ctx.fill(); ctx.stroke(); });
  }
  function drawShape(ctx, size, d, isSel) {
    const xs = d.pts.map(X), ys = d.pts.map(Y);
    if (xs.some((v) => v === null) || ys.some((v) => v === null)) return;
    ctx.strokeStyle = d.color || COLOR; ctx.lineWidth = isSel ? 2 : 1.5; ctx.setLineDash([]);
    if (d.type === "hline") line(ctx, 0, ys[0], size.width, ys[0]);
    else if (d.type === "vline") line(ctx, xs[0], 0, xs[0], size.height);
    else if (d.type === "trend") line(ctx, xs[0], ys[0], xs[1], ys[1]);
    else if (d.type === "ray") { const [xe, ye] = rayEnd(xs[0], ys[0], xs[1], ys[1], size.width); line(ctx, xs[0], ys[0], xe, ye); }
    else if (d.type === "rect") {
      ctx.fillStyle = "rgba(41,98,255,0.15)";
      ctx.fillRect(Math.min(...xs), Math.min(...ys), Math.abs(xs[1] - xs[0]), Math.abs(ys[1] - ys[0]));
      ctx.strokeRect(Math.min(...xs), Math.min(...ys), Math.abs(xs[1] - xs[0]), Math.abs(ys[1] - ys[0]));
    }
    if (isSel && d.type !== "hline" && d.type !== "vline") handles(ctx, xs, ys);
    else if (isSel) handles(ctx, d.type === "hline" ? [size.width / 2] : xs, d.type === "vline" ? [size.height / 2] : ys);
  }
  function span(sec) {
    sec = Math.abs(sec);
    const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.round((sec % 3600) / 60);
    return [d && `${d}d`, h && `${h}h`, (m || (!d && !h)) && `${m}m`].filter(Boolean).join(" ");
  }
  function measure(a, b) {
    const dp = b.p - a.p, pct = (dp / a.p) * 100;
    const bars = Math.round(timeToLogical(b.t) - timeToLogical(a.t));
    return { dp, pct, bars, text: `${dp >= 0 ? "+" : ""}${dp.toFixed(2)} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)`,
             sub: `${bars} bars, ${span(b.t - a.t)}` };
  }
  function drawRuler(ctx, a, b) {
    const x1 = X(a), y1 = Y(a), x2 = X(b), y2 = Y(b);
    if ([x1, y1, x2, y2].some((v) => v === null)) return;
    const m = measure(a, b), up = m.dp >= 0, col = up ? "#26a69a" : "#ef5350";
    ctx.fillStyle = up ? "rgba(38,166,154,0.18)" : "rgba(239,83,80,0.18)";
    ctx.fillRect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
    ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
    line(ctx, x1, y1, x2, y2); ctx.setLineDash([]);
    ctx.font = "12px system-ui, sans-serif";
    const w = Math.max(ctx.measureText(m.text).width, ctx.measureText(m.sub).width) + 16;
    const bx = (x1 + x2) / 2 - w / 2, by = up ? Math.min(y1, y2) - 44 : Math.max(y1, y2) + 6;
    ctx.fillStyle = col; ctx.beginPath(); ctx.roundRect(bx, by, w, 38, 4); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.textAlign = "center";
    ctx.fillText(m.text, bx + w / 2, by + 16); ctx.fillText(m.sub, bx + w / 2, by + 31);
    ctx.textAlign = "start";
  }
  function render(ctx, size) {
    for (const d of drawings) drawShape(ctx, size, d, d === selected);
    if (pending && hover) {
      const pts = [pending.pts[0], hover];
      if (pending.type === "ruler") drawRuler(ctx, ...pts);
      else drawShape(ctx, size, { type: pending.type, pts }, false);
    }
    if (ruler) drawRuler(ctx, ruler.a, ruler.b);
  }

  const primitive = {
    attached(p) { requestUpdate = p.requestUpdate; },
    paneViews() { return [{ zOrder: () => "top", renderer: () => ({
      draw: (target) => target.useMediaCoordinateSpace(({ context, mediaSize }) => render(context, mediaSize)) }) }]; },
    priceAxisViews() {
      return drawings.filter((d) => d.type === "hline").map((d) => ({
        coordinate: () => Y(d.pts[0]) ?? -100, text: () => d.pts[0].p.toFixed(2),
        textColor: () => "#fff", backColor: () => d.color || COLOR, visible: () => true, tickVisible: () => true }));
    },
    hitTest(x, y) {
      if (tool !== "cursor") return null;
      const h = hit(x, y);
      return h ? { externalId: "drawing", zOrder: "top", cursorStyle: h.handle !== null ? "grab" : "pointer" } : null;
    },
  };

  // ---- interaction ----
  function local(e) {
    const r = el.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    const inPane = x >= 0 && x <= chart.timeScale().width() && y >= 0 && y <= chart.panes()[0].getHeight();
    return { x, y, inPane };
  }
  function setTool(t) {
    tool = t; pending = null; hover = null;
    if (t !== "cursor") selected = null;
    onTool(tool); requestUpdate();
  }
  function finish(pts, type) {
    if (type === "ruler") { ruler = { a: pts[0], b: pts[1] }; pending = null; setTool("cursor"); return; }
    const d = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), type, pts, color: COLOR };
    drawings.push(d); selected = d; pending = null; save(); setTool("cursor"); selected = d; requestUpdate();
  }

  function onDown(e) {
    if (!chart || !window.replay.bars.length || e.button !== 0) return;
    const { x, y, inPane } = local(e);
    if (!inPane) return;
    const pt = fromXY(x, y);
    if (!pt) return;
    ruler = null;
    if (pending) { e.stopPropagation(); e.preventDefault(); finish([pending.pts[0], pt], pending.type); return; }
    let type = tool;
    if (tool === "cursor" && e.shiftKey) type = "ruler";
    if (type === "cursor") {
      const h = hit(x, y);
      selected = h ? h.d : null;
      if (h) {
        e.stopPropagation(); e.preventDefault();  // don't let the chart pan while dragging a drawing
        drag = { d: h.d, handle: h.handle, start: pt, orig: h.d.pts.map((q) => ({ ...q })), x, y };
      }
      requestUpdate();
      return;
    }
    e.stopPropagation(); e.preventDefault();
    if (POINTS[type] === 1) { finish([pt], type); return; }
    pending = { type, pts: [pt], x, y }; hover = pt; requestUpdate();
  }
  function onMove(e) {
    if (!chart || !window.replay.bars.length) return;
    const { x, y } = local(e);
    if (drag) {
      const pt = fromXY(x, y); if (!pt) return;
      const d = drag.d;
      if (drag.handle !== null) d.pts[drag.handle] = pt;
      else {
        const dl = Math.round(timeToLogical(pt.t) - timeToLogical(drag.start.t)), dp = pt.p - drag.start.p;
        d.pts = drag.orig.map((q) => ({ t: logicalToTime(timeToLogical(q.t) + dl), p: +(q.p + dp).toFixed(2) }));
      }
      requestUpdate();
    } else if (pending) {
      hover = fromXY(x, y) || hover; requestUpdate();
    }
  }
  function onUp(e) {
    if (drag) { drag = null; save(); return; }
    if (!pending) return;
    const { x, y } = local(e);
    if (Math.hypot(x - pending.x, y - pending.y) > 5) {  // press-drag-release draws in one gesture
      const pt = fromXY(x, y); if (pt) finish([pending.pts[0], pt], pending.type);
    }
  }
  function remove(d) { drawings = drawings.filter((q) => q !== d); if (selected === d) selected = null; save(); requestUpdate(); }

  function onKey(e) {
    if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName)) return false;
    if (e.code === "Escape") { ruler = null; selected = null; setTool("cursor"); return true; }
    if ((e.code === "Delete" || e.code === "Backspace") && selected) { remove(selected); return true; }
    const keys = { KeyT: "trend", KeyH: "hline", KeyV: "vline", KeyR: "rect" };
    if (e.altKey && keys[e.code]) { e.preventDefault(); setTool(keys[e.code]); return true; }
    return false;
  }

  // ---- layer interface (see replay.js) ----
  function init(chartApi, candleSeries, container, toolChanged) {
    chart = chartApi; series = candleSeries; el = container; onTool = toolChanged;
    series.attachPrimitive(primitive);
    el.addEventListener("mousedown", onDown, true);   // capture: runs before the chart's own pan handling
    el.addEventListener("pointerdown", (e) => { if (tool !== "cursor" || e.shiftKey || (local(e).inPane && hit(local(e).x, local(e).y))) e.stopPropagation(); }, true);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }
  function mount(_chart, data) {
    if (data.symbol !== symbol) {
      symbol = data.symbol; drawings = readAll()[symbol] || []; selected = null; pending = null; ruler = null;
    }
    requestUpdate();
  }
  const push = () => requestUpdate(), pop = () => requestUpdate(), form = () => requestUpdate();

  return {
    init, mount, push, pop, form, setTool, onKey,
    deleteSelected() { if (selected) remove(selected); },
    clearAll() { drawings = []; selected = null; ruler = null; save(); requestUpdate(); },
    measure,
    get tool() { return tool; }, get drawings() { return drawings; }, get selected() { return selected; },
    get ruler() { return ruler; },
    _timeToLogical: timeToLogical, _logicalToTime: logicalToTime,
  };
})();
