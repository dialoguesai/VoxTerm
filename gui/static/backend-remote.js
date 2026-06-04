"use strict";
// Default VoxTerm backend: speaks HTTP + Server-Sent Events to a VoxTerm engine, same-origin,
// carrying the optional ?token= auth (present when opened via http://host/?token=… in LAN
// mode). This is exactly the behavior the app shipped before the backend seam existed —
// factored behind one object so a future on-device (in-webview) engine can replace it with
// no UI change. A different backend just sets window.VOX_BACKEND before app.js loads.
class RemoteBackend {
  constructor() {
    this.token = new URLSearchParams(location.search).get("token") || "";
  }
  // Append the token to a URL — used for EventSource, which can't send custom headers.
  authUrl(u) {
    return this.token ? u + (u.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(this.token) : u;
  }
  // Fetch JSON. Throws on network/parse error; the caller (app.js getJSON) handles it.
  async getJSON(url, opts) {
    opts = opts || {};
    if (this.token) opts.headers = Object.assign({ "X-VoxTerm-Token": this.token }, opts.headers || {});
    const r = await fetch(url, opts);
    return await r.json();
  }
  // The live status stream — an EventSource-like object exposing onmessage / onerror.
  events() {
    return new EventSource(this.authUrl("/api/events"));
  }
}

window.VOX_BACKEND = window.VOX_BACKEND || new RemoteBackend();
