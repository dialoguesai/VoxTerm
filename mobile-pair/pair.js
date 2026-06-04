"use strict";
// VoxTerm pairing page. Externalized from index.html so the Content-Security-Policy can
// use `script-src 'self'` with no 'unsafe-inline'. The phone never records — it connects
// to a VoxTerm desktop on your LAN, which does all capture and transcription.

const $ = (id) => document.getElementById(id);

// Prefill host/port from the last pairing. The token is a secret and is NOT kept at rest
// on the phone — you re-enter it (or the desktop shell navigates here with it in the URL).
try {
  const s = JSON.parse(localStorage.getItem("voxterm.pair") || "{}");
  if (s.host) $("host").value = s.host;
  if (s.port) $("port").value = s.port;
} catch (_) {}

// A token already in THIS page's URL (desktop Tauri navigated here, or `adb reverse` dev)
// is carried through so the loopback auto-connect authenticates whether or not the
// desktop requires a token.
const PAGE_TOKEN = new URLSearchParams(location.search).get("token") || "";

// The form starts hidden behind a "Connecting…" loader (index.html). On the desktop app the
// Tauri shell navigates this window to the running engine within ~1s, so the form never appears
// there; on a phone (no local engine) we fall back to it. Hold briefly under Tauri to avoid a
// form-flash before the desktop shell navigates away.
const IS_TAURI = !!(window.__TAURI_INTERNALS__ || window.__TAURI__);
let _revealed = false;
function revealForm() {
  if (_revealed) return;
  _revealed = true;
  if ($("loader")) $("loader").hidden = true;
  if ($("pairform")) $("pairform").hidden = false;
}

// Dev/test convenience: if a VoxTerm backend answers on this device's localhost
// (`adb reverse tcp:8740`, or the desktop shell before it navigates), connect to it —
// carrying any page token so it works whether or not loopback is token-gated. On a normal
// phone this fails fast and the form just stays.
(function probeLoopback() {
  const p = ($("port").value.trim() || "8740");
  const base = "http://localhost:" + p;
  const headers = PAGE_TOKEN ? { "X-VoxTerm-Token": PAGE_TOKEN } : {};
  const c = new AbortController();
  const timer = setTimeout(() => c.abort(), 800);
  fetch(base + "/api/options", { signal: c.signal, headers })
    .then((r) => {
      if (r.ok) {
        window.location.href = base + "/" + (PAGE_TOKEN ? "?token=" + encodeURIComponent(PAGE_TOKEN) : "");
      } else {
        setTimeout(revealForm, IS_TAURI ? 3000 : 0);   // no engine here → show the pairing form
      }
    })
    .catch(() => setTimeout(revealForm, IS_TAURI ? 3000 : 0))
    .finally(() => clearTimeout(timer));
})();

function connect() {
  const host = $("host").value.trim();
  const port = ($("port").value.trim() || "8740");
  const token = $("token").value.trim();
  if (!host) { $("err").textContent = "Enter your desktop's address."; return; }
  if (!token) { $("err").textContent = "Enter the access token VoxTerm printed."; return; }
  if (!/^[0-9]+$/.test(port)) { $("err").textContent = "Port must be a number."; return; }
  // Persist host/port only — never the token at rest on the device.
  try { localStorage.setItem("voxterm.pair", JSON.stringify({ host, port })); } catch (_) {}
  // The desktop serves the full UI + API + SSE from this one origin and app.js reads the
  // token from the query string — everything works once we land there.
  window.location.href = "http://" + host + ":" + port + "/?token=" + encodeURIComponent(token);
}

function forgetPairing() {
  try { localStorage.removeItem("voxterm.pair"); } catch (_) {}
  $("host").value = "";
  $("token").value = "";
  $("err").textContent = "Pairing forgotten on this device.";
}

$("go").addEventListener("click", connect);
$("token").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
$("forget").addEventListener("click", forgetPairing);
