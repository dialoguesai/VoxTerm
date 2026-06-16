"use strict";
// On-device LLM analyzer backend (mobile only). When the native `voxllm` plugin reports a model is
// loaded, this REPLACES window.VOX_ANALYZE (the offline heuristic from analyze.js) with one that runs
// a small local LLM to produce the conversation graph + interruptions — real topic/retort/counter
// structure and speaker-inferred interruptions that the single-speaker heuristic can't.
//
// Division of labour (so the Kotlin plugin stays a generic, reusable LLM runner):
//   Kotlin/native  — "given this prompt, generate text" (start_generate / poll_generate), nothing
//                    domain-specific. Also exposes llm_available.
//   JS (this file) — prompt engineering, JSON extraction/repair, and mapping the model's compact JSON
//                    into the exact {graph, interruptions} contract analyze.js defines. On ANY failure
//                    it falls back to the heuristic, so a flaky model never breaks the modes.
//
// Loaded after analyze.js (so the heuristic exists to wrap) and after the backend script (so
// window.VOX_ONDEVICE is set). No-ops entirely off-device.

(function () {
  if (!window.VOX_ONDEVICE || !(window.__TAURI__ && window.__TAURI__.core)) return;
  const invoke = (cmd, args) => window.__TAURI__.core.invoke(cmd, args);
  const heuristic = window.VOX_ANALYZE;          // keep the offline fallback analyzer
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const CFG = {
    // The bundled Qwen2.5-0.5B .task has an effective KV cache of 1280 tokens (prompt + output), so
    // keep the transcript small: ~1400 chars ≈ 400 tokens, leaving room for the schema + JSON output.
    maxChars: 1400,        // cap transcript fed to the model
    maxTokens: 512,        // advisory generation cap (the native side fixes the model's total window)
    temperature: 0.2,      // near-greedy: we want structure, not creativity
    topK: 40,
    pollMs: 500,
    timeoutMs: 180000,     // first call also loads ~550 MB of weights — allow generous headroom
  };

  // ---- transcript -> prompt --------------------------------------------------
  function transcriptText(turns) {
    const lines = turns.map((t) => {
      const ts = t.t_offset_hms ? `[${t.t_offset_hms}] ` : "";
      const spk = t.speaker ? `${t.speaker}: ` : "";
      return `${ts}${spk}${(t.text || "").replace(/\s+/g, " ").trim()}`;
    });
    let body = lines.join("\n");
    if (body.length > CFG.maxChars) body = body.slice(body.length - CFG.maxChars); // keep the most recent
    return body;
  }

  // Compact, small-model-friendly schema (NOT the node/edge graph directly — that's too referential
  // for a 0.5-1.5B model to keep consistent). We transform this into the contract in JS.
  function buildPrompt(turns) {
    return [
      "You analyze a conversation transcript. The transcript may be a single ASR speaker label even",
      "when several people talk — INFER distinct speakers from content and turn-taking.",
      "",
      "Return ONLY a JSON object (no prose, no markdown fence) with this exact shape:",
      "{",
      '  "topics": [',
      '    { "title": "<=6 word topic label",',
      '      "points": [',
      '        { "speaker": "inferred name or A/B/C", "kind": "claim|question|statement|retort|counter",',
      '          "t": "mm:ss or empty", "text": "<=12 word paraphrase of what was said" } ] } ],',
      '  "interruptions": [',
      '    { "type": "overlap|rapid", "t": "mm:ss", "by": "speaker who cut in", "of": "speaker cut off",',
      '      "note": "<=8 words" } ]',
      "}",
      "",
      "Rules: kind=retort when a speaker pushes back on the previous point; kind=counter for a direct",
      "counter-argument; kind=question for a question. type=overlap when someone speaks over another;",
      "type=rapid for a fast back-and-forth handoff. Use timestamps from the transcript. Do not invent",
      "content not in the transcript. Keep it concise. Output JSON only.",
      "",
      'Transcript:\n"""\n' + transcriptText(turns) + '\n"""',
    ].join("\n");
  }

  // ---- run the native LLM ----------------------------------------------------
  let gen = 0;   // bumped per request so a stale poll loop aborts when a newer analyze starts
  async function generate(prompt) {
    const my = ++gen;
    await invoke("plugin:voxllm|start_generate", {
      prompt, maxTokens: CFG.maxTokens, temperature: CFG.temperature, topK: CFG.topK,
    });
    const t0 = Date.now();
    for (;;) {
      await sleep(CFG.pollMs);
      if (my !== gen) throw new Error("superseded");
      let st;
      try { st = await invoke("plugin:voxllm|poll_generate"); } catch (e) { continue; }
      if (st.phase === "done") return st.text || "";
      if (st.phase === "error") throw new Error(st.error || "llm generation failed");
      if (Date.now() - t0 > CFG.timeoutMs) throw new Error("llm timeout");
    }
  }

  // ---- JSON extraction / repair ----------------------------------------------
  function parseModelJson(text) {
    if (!text) return null;
    let s = text.replace(/```json/gi, "").replace(/```/g, "").trim();
    const a = s.indexOf("{"), b = s.lastIndexOf("}");
    if (a < 0 || b <= a) return null;
    s = s.slice(a, b + 1).replace(/,\s*([}\]])/g, "$1");   // strip trailing commas
    try { return JSON.parse(s); } catch (_) { return null; }
  }

  // ---- compact JSON -> {graph, interruptions} contract -----------------------
  const hmsToSec = (t) => {
    const p = String(t || "").trim().split(":").map(Number);
    if (!p.length || p.some(Number.isNaN)) return undefined;
    return p.reduce((acc, n) => acc * 60 + n, 0);
  };
  const hms = (sec) => {
    sec = Math.max(0, Math.round(sec || 0));
    const p = (n) => String(n).padStart(2, "0");
    return `${p(Math.floor(sec / 3600))}:${p(Math.floor((sec % 3600) / 60))}:${p(sec % 60)}`;
  };
  const KIND = { claim: "statement", statement: "statement", question: "question", retort: "retort", counter: "counter" };

  function toContract(parsed, doc) {
    const turns = doc.turns || [];
    const durationSec = turns.reduce((m, t) => Math.max(m, typeof t.t_offset === "number" ? t.t_offset : 0), 0);
    const speakersSeen = new Set();

    const nodes = [{ id: "root", type: "root", label: "Conversation" }];
    const edges = [];
    const topics = Array.isArray(parsed.topics) ? parsed.topics : [];
    topics.forEach((tp, ti) => {
      const tid = `t${ti}`;
      nodes.push({ id: tid, type: "topic", label: String(tp.title || "(topic)").slice(0, 48) });
      edges.push({ from: "root", to: tid, kind: "topic" });
      let prevId = null, prevSpk = null;
      (Array.isArray(tp.points) ? tp.points : []).forEach((p, pi) => {
        const uid = `u${ti}_${pi}`;
        const type = KIND[String(p.kind || "").toLowerCase()] || "statement";
        const sec = hmsToSec(p.t);
        if (p.speaker) speakersSeen.add(String(p.speaker));
        nodes.push({
          id: uid, type, label: String(p.text || "").slice(0, 120),
          t_offset: sec, speaker: p.speaker || null,
        });
        edges.push({ from: tid, to: uid, kind: "contains" });
        if (prevId) {
          const rebut = type === "retort" || type === "counter" || (p.speaker && prevSpk && p.speaker !== prevSpk);
          edges.push({ from: prevId, to: uid, kind: rebut ? "rebuts" : "reply" });
        }
        prevId = uid; prevSpk = p.speaker || null;
      });
    });

    const overlap = [], rapidSwitch = [];
    (Array.isArray(parsed.interruptions) ? parsed.interruptions : []).forEach((e) => {
      const sec = hmsToSec(e.t);
      const rec = { t_offset: sec, t_hms: sec != null ? hms(sec) : String(e.t || ""), by: e.by, of: e.of, from: e.of, note: e.note };
      if (String(e.type || "").toLowerCase() === "rapid") rapidSwitch.push(rec); else overlap.push(rec);
    });

    return {
      graph: { nodes, edges, topicCount: topics.length },
      interruptions: {
        overlap, rapidSwitch,
        overlapCount: overlap.length, rapidCount: rapidSwitch.length, total: overlap.length + rapidSwitch.length,
        durationSec, multiSpeaker: speakersSeen.size > 1,
      },
    };
  }

  // ---- install the LLM analyzer (with heuristic fallback) --------------------
  function installLlmAnalyzer(model) {
    window.VOX_ANALYZE = {
      kind: "llm",
      model: model || "on-device LLM",
      async analyze(doc) {
        const turns = (doc && doc.turns) || [];
        if (!turns.length) return heuristic.analyze(doc);
        try {
          const parsed = parseModelJson(await generate(buildPrompt(turns)));
          if (!parsed || !Array.isArray(parsed.topics)) throw new Error("model returned no usable JSON");
          return toContract(parsed, doc);
        } catch (e) {
          if (String(e && e.message) === "superseded") throw e;   // a newer analyze is already running
          const h = await heuristic.analyze(doc);                 // graceful fallback — modes still work
          h._llmFallback = String((e && e.message) || e);
          return h;
        }
      },
    };
    if (window.VOX_CONV && window.VOX_CONV.refresh) window.VOX_CONV.refresh();   // redraw if a mode is open
  }

  // Probe the plugin once at startup; swap in the LLM analyzer only if a model actually loaded.
  (async () => {
    try {
      const s = await invoke("plugin:voxllm|llm_available");
      if (s && s.available) installLlmAnalyzer(s.model);
    } catch (_) { /* plugin absent / model missing → stay on the heuristic */ }
  })();
})();
