// Alerts panel: the assist alerts (OPEN, GAP, TREND, RANGE, BREAKOUT, CUTOFF) and the practice setup (BASEBREAK
// since 21-Oct; PULLBACK before), revealed
// as the replay clock passes their bar-close time (live mode: alerts of bars closed so far, refreshed every 30 s).
//
// NO-LOOKAHEAD: the server returns the whole day's alerts (computed causally, bar by bar); this panel only ever
// shows those with time <= clock. Take / Skip on a setup signal is accepted only within 3 minutes of the
// alert and only at the furthest point reached, and is logged by the server (basebreak_decisions.csv) so
// `python basebreak.py report` can compare your picks with the rule's paper outcome.
window.AlertsLayer = (() => {
  const SETTINGS = "replay.alerts.settings.v1", DECIDED = "replay.alerts.decided.v1";
  const DECIDE_S = 180;
  const $ = (id) => document.getElementById(id);
  let rp = null, el = null;
  let settings = { open: true, sound: true, pause: true };
  let decided = {};                 // "<mode>|<alert epoch>" -> "take" | "skip"
  let day = null, alerts = [], loading = null, liveTimer = null;
  let front = -Infinity;            // furthest clock reached (beeps / decisions only there)
  let shownN = 0;

  try { settings = { ...settings, ...JSON.parse(localStorage.getItem(SETTINGS) || "{}") }; } catch {}
  try { decided = JSON.parse(localStorage.getItem(DECIDED) || "{}"); } catch {}
  const save = () => { try { localStorage.setItem(SETTINGS, JSON.stringify(settings)); localStorage.setItem(DECIDED, JSON.stringify(decided)); } catch {} };
  const live = () => !!rp?.live?.on;
  const mode = () => (live() ? "live" : "replay");

  function clock() {
    if (live()) return rp.live.now;
    if (!rp?.bars.length) return null;
    return rp.sub !== null ? rp.form.partial[rp.sub].end : rp.ends[rp.cursor];
  }

  function beep(hi) {
    if (!settings.sound) return;
    try {
      const ctx = beep.ctx || (beep.ctx = new AudioContext());
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = hi ? 880 : 520; g.gain.value = 0.08;
      o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + (hi ? 0.35 : 0.15));
    } catch {}
  }

  async function load(E, force = false) {
    const d = Math.floor(E / 86400);
    if (d === day && !force) return;
    const key = `${d}|${live()}`;
    if (loading === key) return;
    loading = key;
    try {
      const r = await fetch(`/api/alerts?${new URLSearchParams({ time: E, live: live() ? 1 : 0 })}`).then((x) => x.json());
      if (r.detail) throw new Error(r.detail);
      if (d !== day) { shownN = 0; front = -Infinity; }
      day = d; alerts = r.alerts;
    } catch (e) { msg(`Alerts error: ${e.message}`, "err"); }
    loading = null;
    render();
  }

  const msg = (t, c = "") => { const m = $("al-msg"); if (m) { m.textContent = t; m.className = c; } };
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

  async function decide(a, choice) {
    const E = clock();
    try {
      const r = await fetch(`/api/decision?${new URLSearchParams({ alert: a.time, side: a.side, choice, clock: E, mode: mode(), kind: a.kind })}`).then((x) => x.json());
      if (r.detail) throw new Error(r.detail);
      decided[`${mode()}|${a.time}`] = choice; save();
      msg(`Logged: ${choice.toUpperCase()} ${a.side} ${a.hhmm}. Outcome shows when the paper trade exits.`, "live");
      if (settings.pause && !live() && choice) rp.play?.();
    } catch (e) { msg(`Decision not logged: ${e.message}`, "err"); }
    render();
  }

  function render() {
    if (!el || !settings.open) return;
    const E = clock();
    const vis = E === null ? [] : alerts.filter((a) => a.time <= E);
    const atFront = E !== null && E >= front;
    // newly revealed alerts at the front: beep, and pause on a setup signal
    if (atFront && vis.length > shownN) {
      const fresh = vis.slice(shownN);
      const sig = fresh.some((a) => a.side);
      beep(sig);
      if (sig && settings.pause && !live() && rp.playing) rp.pause();
      shownN = vis.length;
    }
    if (E !== null && E > front) front = E;
    const rows = vis.slice().reverse().map((a) => {
      const k = `${mode()}|${a.time}`, d = decided[k];
      let act = "";
      if (a.side) {
        const open = atFront && E - a.time <= DECIDE_S;
        act = d ? `<span class="al-dec ${d}">${d.toUpperCase()}</span>`
          : open ? `<button class="al-take" data-t="${a.time}">Take</button><button class="al-skip" data-t="${a.time}">Skip</button>`
          : `<span class="muted">no decision</span>`;
      }
      const cls = (a.kind === "PULLBACK" || a.kind === "BASEBREAK") ? (a.side ? "sig" : "exit") : a.kind.toLowerCase();
      return `<div class="al-row ${cls}"><div class="al-h"><b>${a.hhmm} ${a.kind}</b>${act}</div><div class="al-t">${esc(a.text)}</div></div>`;
    }).join("");
    $("al-list").innerHTML = rows || `<div class="muted">No alerts yet${E === null ? " - load a chart" : ""}.</div>`;
    el.querySelectorAll(".al-take, .al-skip").forEach((b) => {
      b.onclick = () => decide(alerts.find((a) => a.time === +b.dataset.t), b.classList.contains("al-take") ? "take" : "skip");
    });
  }

  function update() {
    const E = clock();
    if (E === null) return;
    if (Math.floor(E / 86400) !== day) load(E);
    render();
  }

  function liveLoop() {
    clearTimeout(liveTimer);
    if (live() && clock() !== null) load(clock(), true);
    liveTimer = setTimeout(liveLoop, 30000);
  }

  function setVisible(v) {
    settings.open = v; save();
    el.style.display = v ? "flex" : "none";
    $("alerts-btn")?.classList.toggle("active", v);
    if (v) update();
  }

  function init(replay) {
    rp = replay; el = $("alerts");
    el.innerHTML = `
      <div class="tr-head">Alerts <span class="muted">assist + BASEBREAK practice setup</span></div>
      <div class="al-opts">
        <label><input type="checkbox" id="al-sound"> Sound</label>
        <label title="Replay pauses on each BASEBREAK signal so you can decide"><input type="checkbox" id="al-pause"> Pause on signal</label>
      </div>
      <div id="al-msg" class="muted">BASEBREAK = your base -> higher lows / lower highs -> break -> first retest setup. Take or Skip within 3 min; your picks are compared with the rule later.</div>
      <div id="al-list"></div>
      <div class="muted tr-note">Practice only, not a tested edge (basebreak lab: 0/62 configs, ~0 before costs). Report: <code>python basebreak.py report</code></div>`;
    $("al-sound").checked = settings.sound; $("al-pause").checked = settings.pause;
    $("al-sound").onchange = (e) => { settings.sound = e.target.checked; save(); };
    $("al-pause").onchange = (e) => { settings.pause = e.target.checked; save(); };
    setVisible(settings.open);
    liveLoop();
  }

  return { init, update, setVisible, get visible() { return settings.open; } };
})();
