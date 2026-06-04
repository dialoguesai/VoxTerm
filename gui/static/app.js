"use strict";
const $ = (id) => document.getElementById(id);
const PALETTE = ["#5eead4", "#f0566a", "#fbbf24", "#a78bfa", "#60a5fa", "#34d399", "#fb923c", "#f472b6"];

let OPTS = { models: [], languages: {} };
let CUR = null;            // current doc (agent_json parsed)
let RENAMES = {};          // speaker_id -> custom name (view + export)
let lastJobState = "idle";

// ---------- helpers ----------
// When opened via http://host/?token=… (LAN mode) every API call carries the token.
const TOKEN = new URLSearchParams(location.search).get("token") || "";
function authUrl(u) { return TOKEN ? u + (u.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : u; }
async function getJSON(url, opts) {
  opts = opts || {};
  if (TOKEN) opts.headers = Object.assign({ "X-VoxTerm-Token": TOKEN }, opts.headers || {});
  try {
    const r = await fetch(url, opts);
    return await r.json();
  } catch (e) {                                  // server down / non-JSON / offline
    toast("Network error — is the server running?");
    return { ok: false, error: "network" };
  }
}
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2200);
}
function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  const h = Math.floor(m / 60);
  return h ? `${h}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}`
           : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
function colorFor(sid) { return PALETTE[((sid || 0) % PALETTE.length + PALETTE.length) % PALETTE.length]; }
// localStorage wrappers — private/incognito mode can throw on access, so swallow it.
const LS_MODEL = "voxterm.model", LS_LANG = "voxterm.language";
function lsGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function lsSet(key, val) { try { localStorage.setItem(key, val); } catch { /* private mode */ } }
// Sidebar drawer (mobile): keep body class + aria-expanded in sync.
function setNav(open) {
  document.body.classList.toggle("nav-open", open);
  $("navToggle").setAttribute("aria-expanded", open ? "true" : "false");
}
function nameFor(turn) {
  if (turn.peer) return turn.peer_name ? `${turn.speaker} · ${turn.peer_name}` : turn.speaker;
  if (RENAMES[turn.speaker_id]) return RENAMES[turn.speaker_id];
  return turn.speaker || "(unattributed)";
}

// ---------- init ----------
async function init() {
  const o = await getJSON("/api/options");
  OPTS = { models: o.models || [], languages: o.languages || {} };
  const mSel = $("model"), lSel = $("language");
  OPTS.models.forEach((m) => { const o = document.createElement("option"); o.value = m; o.textContent = m; if (m === "fw-small") o.selected = true; mSel.appendChild(o); });
  Object.entries(OPTS.languages).forEach(([code, name]) => { const o = document.createElement("option"); o.value = code; o.textContent = name; if (code === "en") o.selected = true; lSel.appendChild(o); });

  // Restore remembered Model/Language (keep the fw-small/en defaults if nothing saved
  // or the saved value is no longer offered by the server).
  const savedModel = lsGet(LS_MODEL);
  if (savedModel && OPTS.models.includes(savedModel)) mSel.value = savedModel;
  const savedLang = lsGet(LS_LANG);
  if (savedLang && Object.prototype.hasOwnProperty.call(OPTS.languages, savedLang)) lSel.value = savedLang;
  mSel.addEventListener("change", () => lsSet(LS_MODEL, mSel.value));
  lSel.addEventListener("change", () => lsSet(LS_LANG, lSel.value));

  $("recBtn").addEventListener("click", toggleRecord);
  $("refreshSessions").addEventListener("click", loadSessions);
  $("navToggle").addEventListener("click", () => setNav(!document.body.classList.contains("nav-open")));
  $("copyAgent").addEventListener("click", copyForAI);
  $("summarizeAi").addEventListener("click", summarizeForAI);
  $("dlMd").addEventListener("click", () => { if (!CUR) return; download(buildMarkdown(), `${CUR.session.id}-agent.md`, "text/markdown"); });
  $("dlJson").addEventListener("click", () => { if (!CUR) return; download(buildJson(), `${CUR.session.id}-agent.json`, "application/json"); });
  $("dlSrt").addEventListener("click", () => { if (!CUR) return; download(buildSrt(), `${CUR.session.id}.srt`, "application/x-subrip"); });
  $("dlVtt").addEventListener("click", () => { if (!CUR) return; download(buildVtt(), `${CUR.session.id}.vtt`, "text/vtt"); });
  setExportEnabled(false);

  // close the mobile drawer when clicking outside it (the toggle handles its own click)
  document.addEventListener("click", (e) => {
    if (!document.body.classList.contains("nav-open")) return;
    if ($("sidebar").contains(e.target) || $("navToggle").contains(e.target)) return;
    setNav(false);
  });
  // global keyboard: Escape closes drawer; Space / r toggle record (not while typing in a control)
  document.addEventListener("keydown", onKeydown);

  await loadSessions();
  openEvents();
}

function onKeydown(e) {
  if (e.key === "Escape") { setNav(false); return; }
  const el = document.activeElement;
  const tag = el && el.tagName;
  const typing = tag === "SELECT" || tag === "INPUT" || tag === "TEXTAREA" || (el && el.isContentEditable);
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const isSpace = e.code === "Space" || e.key === " ";
  // Let Space activate a focused button/session instead of hijacking it for record/stop.
  if (isSpace && el && (tag === "BUTTON" || el.getAttribute("role") === "button")) return;
  if (isSpace || e.key === "r" || e.key === "R") {
    e.preventDefault();
    toggleRecord();
  }
}

// Export/copy actions are meaningless without a loaded transcript — disable them outright.
function setExportEnabled(on) {
  ["copyAgent", "summarizeAi", "dlJson", "dlMd", "dlSrt", "dlVtt"].forEach((id) => { $(id).disabled = !on; });
}

// ---------- live status (SSE) ----------
function openEvents() {
  const es = new EventSource(authUrl("/api/events"));
  es.onmessage = (e) => {
    let s; try { s = JSON.parse(e.data); } catch { return; }
    applyStatus(s);
  };
  es.onerror = () => {/* browser auto-reconnects */};
}
function applyStatus(s) {
  document.body.classList.toggle("recording", !!s.recording);
  $("recBtn").setAttribute("aria-label", s.recording ? "Stop recording" : "Start recording");
  if (s.recording) {
    $("timer").textContent = fmtClock(s.elapsed);
    $("recState").textContent = "Recording…";
    // level ring (0..~0.3 typical) -> 0..360deg
    const deg = Math.min(360, (s.level || 0) / 0.25 * 360);
    $("ring").style.background = `conic-gradient(var(--rec) ${deg}deg, var(--line) ${deg}deg)`;
    $("model").disabled = $("language").disabled = true;
  } else {
    $("ring").style.background = "";
    $("model").disabled = $("language").disabled = false;
    if (lastJobState === "idle" && s.job.state === "idle") { $("recState").textContent = "Ready to record"; $("timer").textContent = "00:00"; }
  }
  const job = s.job || { state: "idle" };
  const working = job.state === "transcribing";
  // While transcribing: the calm "working" affordance + a hard-disabled record button
  // (recording can't begin until the job resolves). The progress bar shows precise state.
  document.body.classList.toggle("working", working);
  if (working) {
    $("progress").classList.remove("hidden");
    $("progressMsg").textContent = job.msg || "Transcribing…";
    const pct = Math.round((job.frac || 0) * 100);
    $("progressPct").textContent = pct + "%";
    $("barFill").style.width = pct + "%";
    $("recState").textContent = "Transcribing…";
    $("recBtn").disabled = true;
  }
  if (job.state === "done" && lastJobState !== "done") {
    $("progress").classList.add("hidden");
    $("recState").textContent = "Ready to record";
    $("recBtn").disabled = false;
    toast(`Done — ${job.n_turns} turns, ${job.n_speakers} speaker(s)`);
    if (job.stem) loadSession(job.stem);
    loadSessions();
  }
  if (job.state === "error" && lastJobState !== "error") {
    $("progress").classList.add("hidden");
    $("recBtn").disabled = false;
    toast("Error: " + (job.error || "transcription failed"));
    $("recState").textContent = "Ready to record";
  }
  lastJobState = job.state;
}

// ---------- record ----------
async function toggleRecord() {
  const recording = document.body.classList.contains("recording");
  if (!recording) {
    const r = await getJSON("/api/record/start", { method: "POST" });
    if (!r.ok) toast(r.error ? "Mic error: " + r.error : "Could not start (mic busy?)");
  } else {
    $("recBtn").disabled = true;
    await getJSON("/api/record/stop", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: $("model").value, language: $("language").value }),
    });
    setTimeout(() => { $("recBtn").disabled = false; }, 600);
  }
}

// ---------- sessions ----------
async function loadSessions() {
  const sessions = (await getJSON("/api/sessions")).sessions || [];
  const ul = $("sessions"); ul.innerHTML = "";
  if (!sessions.length) { ul.innerHTML = `<li class="sessions-empty">No sessions yet — record one to get started.</li>`; return; }
  sessions.forEach((s) => {
    const li = document.createElement("li"); li.className = "session"; li.dataset.stem = s.stem;
    li.tabIndex = 0; li.setAttribute("role", "button");
    const has = []; if (s.agent_md) has.push("AI"); if (s.transcript) has.push("md");
    li.innerHTML = `<div class="s-title">${escapeHtml(prettyStem(s.stem))}</div>
      <div class="s-sub">${has.map((h) => `<span class="tag">${h}</span>`).join("")}</div>`;
    li.addEventListener("click", () => loadSession(s.stem, s.dir));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); loadSession(s.stem, s.dir); } });
    ul.appendChild(li);
  });
}
function prettyStem(stem) {
  const m = stem.match(/(\d{4})-?(\d{2})-?(\d{2})[_-]?(\d{2})(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
  return stem;
}

async function loadSession(stem, dir) {
  const dq = dir ? `&dir=${encodeURIComponent(dir)}` : "";
  // prefer the structured JSON; fall back to the markdown if the AI export is missing
  let res = await getJSON(`/api/session?stem=${encodeURIComponent(stem)}&kind=agent_json${dq}`);
  if (!res.ok) {
    res = await getJSON(`/api/session?stem=${encodeURIComponent(stem)}&kind=transcript${dq}`);
    if (res.ok) return showRawMarkdown(stem, res.text);
    return toast("Could not load session");
  }
  try { CUR = JSON.parse(res.text); } catch { return toast("Bad session JSON"); }
  RENAMES = {};
  render();
  document.querySelectorAll(".session").forEach((el) => el.classList.toggle("active", el.dataset.stem === stem));
  setNav(false);
}

// ---------- render ----------
function render() {
  $("empty").classList.add("hidden");
  $("transcriptView").classList.remove("hidden");
  setExportEnabled(true);
  const s = CUR.session;
  $("tvTitle").textContent = prettyStem(s.id);
  $("tvMeta").textContent = `${CUR.turns.length} turns · ${CUR.speakers.length} speaker(s) · ${s.duration_hms || ""} · ${s.model || ""}`;

  // legend (click to rename)
  const leg = $("speakerLegend"); leg.innerHTML = "";
  CUR.speakers.filter((sp) => !sp.peer).forEach((sp) => {
    const el = document.createElement("button"); el.className = "lg";
    el.innerHTML = `<span class="dot" style="background:${colorFor(sp.id)}"></span><span>${escapeHtml(RENAMES[sp.id] || sp.label)}</span>`;
    el.title = "Click to rename this speaker";
    el.addEventListener("click", () => renameSpeaker(sp.id));
    leg.appendChild(el);
  });

  const wrap = $("turns"); wrap.innerHTML = "";
  CUR.turns.forEach((t) => {
    const row = document.createElement("div"); row.className = "turn" + (t.confidence_uncertain ? " uncertain" : "");
    const c = t.peer ? "#7aa2f7" : colorFor(t.speaker_id);
    const mk = (t.markers || []).map((m) => `<span class="mk">${m}</span>`).join("");
    const spk = t.peer
      ? `<span class="t-spk"><span class="dot" style="background:${c}"></span>${escapeHtml(nameFor(t))}</span>`
      : `<span class="t-spk"><span class="dot" style="background:${c}"></span><button data-sid="${t.speaker_id}">${escapeHtml(nameFor(t))}</button></span>`;
    row.innerHTML = `<div class="t-time">${t.t_offset_hms}</div>
      <div class="t-body">${spk}${mk}<div class="t-text">${escapeHtml(t.text)}</div></div>`;
    const btn = row.querySelector("button[data-sid]");
    if (btn) btn.addEventListener("click", () => renameSpeaker(t.speaker_id));
    wrap.appendChild(row);
  });
}
function showRawMarkdown(stem, text) {
  CUR = null;
  setExportEnabled(false);  // no structured JSON behind a raw-markdown view
  $("empty").classList.add("hidden"); $("transcriptView").classList.remove("hidden");
  $("tvTitle").textContent = prettyStem(stem); $("tvMeta").textContent = "(no AI export — raw transcript)";
  $("speakerLegend").innerHTML = "";
  $("turns").innerHTML = `<pre style="white-space:pre-wrap;font:inherit;color:var(--muted)">${escapeHtml(text)}</pre>`;
}
function renameSpeaker(sid) {
  const cur = RENAMES[sid] || (CUR.speakers.find((x) => x.id === sid) || {}).label || `Speaker ${sid}`;
  const name = prompt("Rename speaker (applies to this view + your copy/export):", cur);
  if (name && name.trim()) { RENAMES[sid] = name.trim(); render(); toast("Renamed — included when you copy/export"); }
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

// ---------- export (rename-aware, built from the JSON source of truth) ----------
function buildJson() {
  const d = JSON.parse(JSON.stringify(CUR));
  d.turns.forEach((t) => { if (!t.peer && RENAMES[t.speaker_id]) t.speaker = RENAMES[t.speaker_id]; });
  d.speakers.forEach((sp) => { if (!sp.peer && RENAMES[sp.id]) sp.label = RENAMES[sp.id]; });
  return JSON.stringify(d, null, 2) + "\n";
}
function buildMarkdown() {
  const s = CUR.session;
  // JSON.stringify of a string is a valid YAML double-quoted scalar — mirrors the
  // server's _yaml_scalar so a rename/peer_name with a quote or newline can't break
  // the front-matter or inject keys.
  const y = (v) => JSON.stringify(String(v == null ? "" : v));
  const spk = CUR.speakers.map((sp) => sp.peer
    ? `  - { id: 0, label: ${y(sp.label)}, turns: ${sp.turns}, peer: true, peer_name: ${y(sp.peer_name)} }`
    : `  - { id: ${sp.id}, label: ${y(RENAMES[sp.id] || sp.label)}, turns: ${sp.turns}, peer: false }`).join("\n");
  const fm = ["---", "voxterm_export_version: 1", "kind: voxterm-transcript",
    `session_id: ${y(s.id)}`, `date: ${(s.started_at || "").slice(0, 10) || "null"}`,
    `duration: ${y(s.duration_hms || "")}`, `model: ${y(s.model || "")}`, `language: ${y(s.language || "")}`,
    "speakers:", spk, `turns: ${CUR.turns.length}`,
    "notes:", '  - "Speaker labels are diarization clusters / your renames, not verified identities."', "---", ""].join("\n");
  const body = ["> VoxTerm session — timestamps are [mm:ss] into the recording; [~]=uncertain, [overlap], [new-voice], [peer].", "", "## Transcript", ""];
  CUR.turns.forEach((t) => {
    const who = t.peer ? `**${nameFor(t)}** (peer: ${t.peer_name})`
      : (t.speaker_id ? `**${nameFor(t)}** (#${t.speaker_id})` : "**(unattributed)**");
    let line = `[${t.t_offset_hms}] ${who}: ${t.text}`;
    if (t.markers && t.markers.length) line += "  " + t.markers.map((m) => `[${m}]`).join(" ");
    body.push(line, "");
  });
  return fm + body.join("\n").trim() + "\n";
}
// ---------- subtitles (client-side, rename-aware) — must byte-match the server's to_srt/to_vtt ----------
// Single-line, cue-safe text (mirror export.py _cue_text): collapse newlines/blank lines
// + neutralize the "-->" marker, either of which would corrupt SRT/VTT cue boundaries.
function cueText(s) { return String(s == null ? "" : s).trim().replace(/\s*\n\s*/g, " ").replace(/-->/g, "->"); }
// End of a turn: prefer t_offset_end; else the next (filtered) turn's start; else +2s.
// Clamp end>start with a 0.5s minimum span — matches the backend _cue_times exactly.
function turnEnd(t, next) {
  let end = (typeof t.t_offset_end === "number") ? t.t_offset_end
          : (next && typeof next.t_offset === "number") ? next.t_offset
          : (t.t_offset || 0) + 2.0;
  const start = t.t_offset || 0;
  if (!(end > start)) end = start + 0.5;
  return end;
}
// Mirror the backend _cue_label exactly: peer = "<speaker> (peer)" (NOT nameFor's
// "speaker · peer_name"); local = the rename-aware speaker name. Keeps client downloads
// byte-identical to the server artifact (renames are an intentional client-only delta).
function cueLabel(t) { return cueText(t.peer ? `${t.speaker || "(unattributed)"} (peer)` : nameFor(t)); }
// seconds -> "HH:MM:SS" + sep + "mmm" (sep is "," for SRT, "." for VTT).
function tsParts(sec, sep) {
  sec = Math.max(0, sec || 0);
  const ms = Math.round(sec * 1000);
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const millis = ms % 1000;
  const p2 = (n) => String(n).padStart(2, "0");
  return `${p2(h)}:${p2(m)}:${p2(s)}${sep}${String(millis).padStart(3, "0")}`;
}
// Only turns with non-empty cue text become cues — exactly like the backend, so the
// downloaded file matches the server artifact (no blank cues / index drift).
function cueTurns() { return CUR.turns.filter((t) => cueText(t.text)); }
function buildSrt() {
  const cues = cueTurns(), out = [];
  cues.forEach((t, i) => {
    const start = tsParts(t.t_offset || 0, ",");
    const end = tsParts(turnEnd(t, cues[i + 1]), ",");
    out.push(String(i + 1), `${start} --> ${end}`, `${cueLabel(t)}: ${cueText(t.text)}`, "");
  });
  return out.join("\n");
}
function buildVtt() {
  const cues = cueTurns(), out = ["WEBVTT", ""];
  cues.forEach((t, i) => {
    const start = tsParts(t.t_offset || 0, ".");
    const end = tsParts(turnEnd(t, cues[i + 1]), ".");
    out.push(`${start} --> ${end}`, `${cueLabel(t)}: ${cueText(t.text)}`, "");
  });
  return out.join("\n");
}

async function copyForAI() {
  if (!CUR) return toast("Load a transcript first");
  const md = buildMarkdown();
  try { await navigator.clipboard.writeText(md); toast("Copied AI transcript to clipboard"); }
  catch { download(md, `${CUR.session.id}-agent.md`, "text/markdown"); toast("Clipboard blocked — downloaded instead"); }
}
// A ready-to-paste prompt: a strong summarization instruction followed by the transcript.
function summaryPrompt() {
  return [
    "## Task",
    "",
    "You are given a transcript of a recorded conversation (below). Read it in full, then produce:",
    "",
    "1. **Summary** — a concise overview (3-5 sentences) of what the conversation was about.",
    "2. **Key decisions** — a bullet list of decisions reached, or \"None\" if there were none.",
    "3. **Action items** — a bullet list of follow-ups, each with the owner if one is identifiable.",
    "4. **Per-speaker highlights** — for each speaker, 1-2 bullets on their main points or positions.",
    "",
    "Stick to what the transcript actually says. Do not invent details. Speaker labels are diarization",
    "clusters or manual renames, not verified identities — treat them as such.",
    "",
    "---",
    "",
    buildMarkdown(),
  ].join("\n");
}
async function summarizeForAI() {
  if (!CUR) return toast("Load a transcript first");
  const text = summaryPrompt();
  try { await navigator.clipboard.writeText(text); toast("Copied summary prompt to clipboard"); }
  catch { download(text, `${CUR.session.id}-summarize.md`, "text/markdown"); toast("Clipboard blocked — downloaded instead"); }
}
function download(text, filename, mime) {
  const blob = new Blob([text], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

init().catch((e) => toast("Init failed: " + e));

// PWA: register the offline app-shell service worker (script-src 'self' allows this).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
