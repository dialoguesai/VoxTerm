"use strict";
// Conversation modes: the Graph (topic/argument tree) and Interruptions views, plus the mode
// switcher that owns which content panel is visible. app.js stays in charge of the top-level state
// (empty / live / transcript); this module owns the panel-level switch between Transcript, Graph and
// Interruptions and renders the latter two from window.VOX_ANALYZE.analyze(activeDoc).
//
// Coupling with app.js is deliberately thin:
//   VOX_CONV.getDoc      — app.js sets this to a fn returning the active {turns, speakers, session}
//                          (the loaded transcript, or a synthesized doc while recording live).
//   VOX_CONV.setTop(s)   — app.js calls this from setView('empty'|'live'|'transcript').
//   VOX_CONV.refresh()   — app.js calls this when the active doc's content changes (new turns, live
//                          tail) so Graph/Interruptions re-analyze + redraw if they're showing.
// Seeking reuses window.VOX_SEEK(sec) (set by app.js) so nodes/events are click-to-play.

(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const SVGNS = "http://www.w3.org/2000/svg";

  let top = "empty";                 // 'empty' | 'live' | 'transcript'
  let mode = "transcript";           // 'transcript' | 'graph' | 'interruptions'
  let lastSig = "";                  // content signature — skip redundant re-analysis
  let analyzeTok = 0;                // guards against an out-of-order async analyze resolving late
  let debounce = null;

  function docSig(doc) {
    const turns = (doc && doc.turns) || [];
    const last = turns[turns.length - 1];
    return `${turns.length}|${last ? (last.text || "").length : 0}|${last ? last.t_offset : 0}`;
  }
  function seek(sec) { if (typeof sec === "number" && window.VOX_SEEK) window.VOX_SEEK(sec); }

  // ---- mode switch -----------------------------------------------------------
  function setTop(state) { top = state; applyMode(); }
  function setMode(m) { if (m === mode) return; mode = m; lastSig = ""; applyMode(); }

  function applyMode() {
    const live = top === "live", loaded = top === "transcript", content = live || loaded;
    $("modeTabs").classList.toggle("hidden", !content);
    // Transcript mode shows the existing turns/liveLines; Graph/Interruptions show their panels.
    $("turns").classList.toggle("hidden", !(content && mode === "transcript" && loaded));
    $("liveLines").classList.toggle("hidden", !(content && mode === "transcript" && live));
    $("graphPanel").classList.toggle("hidden", !(content && mode === "graph"));
    $("interruptPanel").classList.toggle("hidden", !(content && mode === "interruptions"));
    document.querySelectorAll("#modeTabs .mode-tab").forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle("active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (content && mode !== "transcript") scheduleAnalyze(true);
  }

  // Called by app.js when content changes. Only does work if a derived panel is actually showing.
  function refresh() { if ((top === "live" || top === "transcript") && mode !== "transcript") scheduleAnalyze(false); }

  function scheduleAnalyze(immediate) {
    clearTimeout(debounce);
    debounce = setTimeout(runAnalyze, immediate ? 0 : 350);
  }

  async function runAnalyze() {
    const doc = api.getDoc && api.getDoc();
    if (!doc || !(doc.turns || []).length) { renderEmpty(); return; }
    const sig = docSig(doc) + "|" + mode + "|" + (window.VOX_ANALYZE && window.VOX_ANALYZE.kind);
    if (sig === lastSig) return;     // nothing changed since last draw
    lastSig = sig;
    const tok = ++analyzeTok;
    let res;
    try { res = await window.VOX_ANALYZE.analyze(doc); }
    catch (e) { if (tok === analyzeTok) renderError(String((e && e.message) || e)); return; }
    if (tok !== analyzeTok) return;  // a newer analyze superseded this one
    if (mode === "graph") renderGraph(res.graph);
    else if (mode === "interruptions") renderInterruptions(res.interruptions);
  }

  function renderEmpty() {
    if (mode === "graph") $("graphPanel").innerHTML = `<p class="conv-empty">No conversation yet — the topic map appears as people talk.</p>`;
    if (mode === "interruptions") $("interruptPanel").innerHTML = `<p class="conv-empty">No interruptions yet — the counter updates live as people talk over each other.</p>`;
  }
  function renderError(msg) {
    const html = `<p class="conv-empty">Couldn't analyze this conversation: ${esc(msg)}</p>`;
    if (mode === "graph") $("graphPanel").innerHTML = html;
    if (mode === "interruptions") $("interruptPanel").innerHTML = html;
  }

  // ---- GRAPH: layered topic/argument tree (root → topics → utterances) -------
  const GEO = { M: 18, rootX: 18, rootW: 96, topicX: 170, topicW: 150, utterX: 388, utterW: 250, rowH: 58, nodeH: 42 };
  const EDGE_COLOR = { topic: "#3a3a40", contains: "#34343a", reply: "#40404a", rebuts: "#b56a5a", supports: "#5a8f7a" };
  // utterance left-accent by type
  const TYPE_COLOR = { statement: "#7d7d86", question: "#7f9cc4", retort: "#c48f6a", counter: "#c47a7a" };
  const TYPE_LABEL = { statement: "", question: "Q", retort: "↩", counter: "⚔" };

  function bezier(x1, y1, x2, y2) {
    const dx = Math.max(24, (x2 - x1) * 0.5);
    return `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`;
  }

  function renderGraph(graph) {
    const g = GEO;
    const nodes = graph.nodes || [], edges = graph.edges || [];
    const byId = {}; nodes.forEach((n) => (byId[n.id] = n));
    const utter = nodes.filter((n) => n.type !== "root" && n.type !== "topic");
    const topics = nodes.filter((n) => n.type === "topic");

    // Vertical layout: utterances stack in document order; a topic/root centers on its children.
    const y = {};
    utter.forEach((n, i) => (y[n.id] = g.M + i * g.rowH + g.nodeH / 2));
    const childYs = (parentId, kind) => edges.filter((e) => e.from === parentId && e.kind === kind)
      .map((e) => y[e.to]).filter((v) => typeof v === "number");
    topics.forEach((t) => { const ys = childYs(t.id, "contains"); y[t.id] = ys.length ? avg(ys) : g.M + g.nodeH / 2; });
    const tYs = topics.map((t) => y[t.id]); y.root = tYs.length ? avg(tYs) : g.M + g.nodeH / 2;

    const W = g.utterX + g.utterW + g.M;
    const H = Math.max(g.M * 2 + g.nodeH, g.M + utter.length * g.rowH + g.M);

    let paths = "", boxes = "";
    edges.forEach((e) => {
      const a = byId[e.from], b = byId[e.to];
      if (!a || !b || y[e.from] == null || y[e.to] == null) return;
      let x1, x2;
      if (e.kind === "topic") { x1 = g.rootX + g.rootW; x2 = g.topicX; }
      else if (e.kind === "contains") { x1 = g.topicX + g.topicW; x2 = g.utterX; }
      else { // reply / rebuts / supports — utterance→utterance, drawn on the column's left rail
        const yy1 = y[e.from] + g.nodeH / 2, yy2 = y[e.to] - g.nodeH / 2, xr = g.utterX - 9;
        paths += `<path d="M${xr} ${yy1} C${xr - 10} ${yy1 + 8} ${xr - 10} ${yy2 - 8} ${xr} ${yy2}" `
          + `fill="none" stroke="${EDGE_COLOR[e.kind] || EDGE_COLOR.reply}" stroke-width="${e.kind === "rebuts" ? 2.2 : 1.4}"/>`;
        return;
      }
      paths += `<path d="${bezier(x1, y[e.from], x2, y[e.to])}" fill="none" stroke="${EDGE_COLOR[e.kind]}" stroke-width="1.4"/>`;
    });

    // root + topic pills
    boxes += pill(g.rootX, y.root - g.nodeH / 2, g.rootW, g.nodeH, "Conversation", "root");
    topics.forEach((t) => { boxes += pill(g.topicX, y[t.id] - g.nodeH / 2, g.topicW, g.nodeH, t.label, "topic"); });
    // utterance cards
    utter.forEach((n) => { boxes += utterCard(g.utterX, y[n.id] - g.nodeH / 2, g.utterW, g.nodeH, n); });

    $("graphPanel").innerHTML =
      `<div class="conv-cap">Topic map · ${topics.length} topic${topics.length === 1 ? "" : "s"} · ${utter.length} turn${utter.length === 1 ? "" : "s"}`
      + (window.VOX_ANALYZE.kind === "heuristic" ? ` · <span class="conv-hint">heuristic — argument structure sharpens with the on-device LLM</span>` : "") + `</div>`
      + `<div class="graph-scroll"><svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" class="graph-svg" role="img" aria-label="Conversation topic map">`
      + paths + boxes + `</svg></div>`;
    wireSeeks($("graphPanel"));
  }
  function avg(a) { return a.reduce((s, v) => s + v, 0) / a.length; }

  function pill(x, y, w, h, label, cls) {
    return `<g class="gnode ${cls}"><rect x="${x}" y="${y}" rx="9" width="${w}" height="${h}"/>`
      + `<text x="${x + w / 2}" y="${y + h / 2}" text-anchor="middle" dominant-baseline="central">${esc(clip(label, cls === "root" ? 14 : 20))}</text></g>`;
  }
  function utterCard(x, y, w, h, n) {
    const col = TYPE_COLOR[n.type] || TYPE_COLOR.statement;
    const badge = TYPE_LABEL[n.type] || "";
    const seekAttr = typeof n.t_offset === "number" ? ` data-seek="${n.t_offset}" tabindex="0" role="button"` : "";
    return `<g class="gnode utter"${seekAttr}>`
      + `<rect x="${x}" y="${y}" rx="8" width="${w}" height="${h}"/>`
      + `<rect x="${x}" y="${y}" rx="8" width="5" height="${h}" fill="${col}"/>`
      + (badge ? `<text class="ubadge" x="${x + 16}" y="${y + h / 2}" dominant-baseline="central" fill="${col}">${esc(badge)}</text>` : "")
      + `<text class="utext" x="${x + (badge ? 30 : 14)}" y="${y + h / 2}" dominant-baseline="central">${esc(clip(n.label, 34))}</text></g>`;
  }
  function clip(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  // ---- INTERRUPTIONS: counters + timeline + list -----------------------------
  function renderInterruptions(ir) {
    const dur = ir.durationSec || 0;
    const cap = ir.multiSpeaker
      ? `Detected from speaker changes + timing across multiple speakers.`
      : `Single-speaker transcription — overlaps are inferred from cut-off cues only. With the on-device LLM (or a diarized recording) this gets much sharper.`;

    const stat = (n, label, cls) => `<div class="stat ${cls}"><div class="stat-n">${n}</div><div class="stat-l">${label}</div></div>`;
    const head = `<div class="ir-stats">`
      + stat(ir.total, "Interruptions", "total")
      + stat(ir.overlapCount, "Overlaps", "overlap")
      + stat(ir.rapidCount, "Rapid switches", "rapid")
      + `</div><p class="conv-cap">${esc(cap)}</p>`;

    // merge + sort events for the timeline and list
    const events = ir.overlap.map((e) => ({ ...e, cat: "overlap" }))
      .concat(ir.rapidSwitch.map((e) => ({ ...e, cat: "rapid" })))
      .sort((a, b) => (a.t_offset || 0) - (b.t_offset || 0));

    $("interruptPanel").innerHTML = head + timeline(events, dur) + perMinute(events, dur) + eventList(events);
    wireSeeks($("interruptPanel"));
  }

  function timeline(events, dur) {
    if (!dur || !events.length) return `<div class="ir-timeline-empty">No interruptions on the timeline yet.</div>`;
    const W = 1000, H = 46, pad = 8, y0 = 26;
    let ticks = "";
    events.forEach((e) => {
      const x = pad + (Math.min(e.t_offset || 0, dur) / dur) * (W - pad * 2);
      const col = e.cat === "overlap" ? "#c47a7a" : "#c4a86a";
      ticks += `<line class="ir-tick" data-seek="${e.t_offset}" x1="${x}" y1="${y0 - 12}" x2="${x}" y2="${y0 + 12}" stroke="${col}" stroke-width="2"><title>${esc(e.t_hms)} · ${e.cat}</title></line>`;
    });
    return `<div class="ir-timeline"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="ir-timeline-svg" role="img" aria-label="Interruption timeline">`
      + `<line x1="${pad}" y1="${y0}" x2="${W - pad}" y2="${y0}" stroke="#34343a" stroke-width="1.5"/>` + ticks + `</svg>`
      + `<div class="ir-axis"><span>00:00</span><span>${esc(hms(dur))}</span></div></div>`;
  }

  // interruptions-over-time histogram (adaptive bin width so short conversations still get bars)
  function perMinute(events, dur) {
    if (!dur || !events.length) return "";
    const bins = Math.min(24, Math.max(8, Math.ceil(dur / 5)));
    const o = new Array(bins).fill(0), r = new Array(bins).fill(0);
    events.forEach((e) => { const b = Math.min(bins - 1, Math.floor(((e.t_offset || 0) / dur) * bins)); (e.cat === "overlap" ? o : r)[b]++; });
    const max = Math.max(1, ...o.map((v, i) => v + r[i]));
    const W = 1000, H = 90, pad = 8, bw = (W - pad * 2) / bins, gap = Math.min(6, bw * 0.2);
    let bars = "";
    for (let i = 0; i < bins; i++) {
      const x = pad + i * bw + gap / 2, w = bw - gap;
      const ho = (o[i] / max) * (H - 16), hr = (r[i] / max) * (H - 16);
      bars += `<rect x="${x}" y="${H - ho}" width="${w}" height="${ho}" fill="#c47a7a"/>`;
      bars += `<rect x="${x}" y="${H - ho - hr}" width="${w}" height="${hr}" fill="#c4a86a"/>`;
    }
    return `<div class="ir-bars"><div class="conv-cap">Interruptions over time <span class="lg"><i class="sw o"></i>overlap <i class="sw r"></i>rapid switch</span></div>`
      + `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="ir-bars-svg" role="img" aria-label="Interruptions over time">${bars}</svg></div>`;
  }

  function eventList(events) {
    if (!events.length) return "";
    const rows = events.map((e) => {
      const cat = e.cat === "overlap" ? "Overlap" : "Rapid switch";
      const desc = e.cat === "overlap"
        ? `${e.by || "someone"} ${e.note || "over"} ${e.of || "the previous speaker"}`
        : `${e.gapSec != null ? e.gapSec + "s gap" : "fast handoff"}${e.from ? ` after ${e.from}` : ""}`;
      const seekAttr = typeof e.t_offset === "number" ? ` data-seek="${e.t_offset}" tabindex="0" role="button"` : "";
      return `<li class="ir-ev ${e.cat}"${seekAttr}><span class="ir-t">${esc(e.t_hms || "")}</span>`
        + `<span class="ir-badge">${cat}</span><span class="ir-desc">${esc(desc)}</span></li>`;
    }).join("");
    return `<ul class="ir-list">${rows}</ul>`;
  }

  function hms(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const p = (n) => String(n).padStart(2, "0");
    return `${p(Math.floor(sec / 60))}:${p(sec % 60)}`;
  }

  // click/Enter on any [data-seek] element seeks the audio player
  function wireSeeks(root) {
    root.querySelectorAll("[data-seek]").forEach((el) => {
      const sec = parseFloat(el.getAttribute("data-seek"));
      el.addEventListener("click", () => seek(sec));
      el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); seek(sec); } });
    });
  }

  const api = {
    getDoc: null,
    setTop, setMode, refresh,
    init() {
      document.querySelectorAll("#modeTabs .mode-tab").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    },
  };
  window.VOX_CONV = api;
})();
