"use strict";
// VoxTerm mobile entry. Externalized from index.html so the CSP can use `script-src 'self'`.
// On the Android app this redirects to the on-device GUI (app/ — the same gui/static the desktop
// runs, backed by the native Whisper plugin). A plain phone browser instead gets the PAIRING form
// to connect to a VoxTerm desktop on the LAN.

const $ = (id) => document.getElementById(id);

// Prefill host/port from the last pairing. The token is a secret and is NOT kept at rest
// on the phone — you re-enter it (or the desktop shell navigates here with it in the URL).
try {
  const s = JSON.parse(localStorage.getItem("voxterm.pair") || "{}");
  if (s.host) $("host").value = s.host;
  if (s.port) $("port").value = s.port;
} catch (_) {}

// The form starts hidden behind a "Connecting…" loader (index.html).
const IS_TAURI = !!(window.__TAURI_INTERNALS__ || window.__TAURI__);
let _revealed = false;
function showPairForm() {
  if (_revealed) return;
  _revealed = true;
  if ($("loader")) $("loader").hidden = true;
  $("pairform").hidden = false;
}

// Where to go on load:
//  - Android app   → the on-device GUI (app/ = the staged gui/static + native Whisper plugin).
//  - desktop app   → the Rust shell navigates this window to the Python engine within ~1s; if that
//                    hasn't happened (engine failed to start), fall back to the pairing form.
//  - plain browser → the pairing form (connect to a VoxTerm desktop on the LAN).
if (/Android/i.test(navigator.userAgent) && IS_TAURI && window.__TAURI__ && window.__TAURI__.core) {
  window.location.replace("app/index.html");
} else if (IS_TAURI) {
  setTimeout(showPairForm, 2000);
} else {
  showPairForm();
}

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
// On-device transcription is the full GUI under app/ (loaded by revealMobileHome on Android) —
// it drives the native voxasr plugin through the LocalBackend, so there's no inline flow here.
