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
  const r = await fetch(url, opts); return r.json();
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
  OPTS = await getJSON("/api/options");
  const mSel = $("model"), lSel = $("language");
  OPTS.models.forEach((m) => { const o = document.createElement("option"); o.value = m; o.textContent = m; if (m === "fw-small") o.selected = true; mSel.appendChild(o); });
  Object.entries(OPTS.languages).forEach(([code, name]) => { const o = document.createElement("option"); o.value = code; o.textContent = name; if (code === "en") o.selected = true; lSel.appendChild(o); });

  $("recBtn").addEventListener("click", toggleRecord);
  $("refreshSessions").addEventListener("click", loadSessions);
  $("navToggle").addEventListener("click", () => setNav(!document.body.classList.contains("nav-open")));
  $("copyAgent").addEventListener("click", copyForAI);
  $("summarizeAi").addEventListener("click", summarizeForAI);
  $("dlMd").addEventListener("click", () => { if (!CUR) return; download(buildMarkdown(), `${CUR.session.id}-agent.md`, "text/markdown"); });
  $("dlJson").addEventListener("click", () => { if (!CUR) return; download(buildJson(), `${CUR.session.id}-agent.json`, "application/json"); });
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
  ["copyAgent", "summarizeAi", "dlJson", "dlMd"].forEach((id) => { $(id).disabled = !on; });
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
  if (job.state === "transcribing") {
    $("progress").classList.remove("hidden");
    $("progressMsg").textContent = job.msg || "Transcribing…";
    const pct = Math.round((job.frac || 0) * 100);
    $("progressPct").textContent = pct + "%";
    $("barFill").style.width = pct + "%";
    $("recState").textContent = "Transcribing…";
  }
  if (job.state === "done" && lastJobState !== "done") {
    $("progress").classList.add("hidden");
    $("recState").textContent = "Ready to record";
    toast(`Done — ${job.n_turns} turns, ${job.n_speakers} speaker(s)`);
    if (job.stem) loadSession(job.stem);
    loadSessions();
  }
  if (job.state === "error" && lastJobState !== "error") {
    $("progress").classList.add("hidden");
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
  const { sessions } = await getJSON("/api/sessions");
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
