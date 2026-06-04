/* VoxTerm GUI service worker — offline app shell, network-only API.
 *
 * Caches the static shell so the app opens instantly / offline. Never caches /api
 * (live status + recordings must always hit the server), and never caches the SSE
 * stream. Bumping CACHE drops the old shell on activate.
 */
"use strict";
const CACHE = "voxterm-shell-v1";
const SHELL = [
  "/",
  "/static/app.js",
  "/static/style.css",
  "/static/icon.svg",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/manifest.webmanifest",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;                 // never touch POSTs (record/stop)
  if (url.pathname.startsWith("/api/")) return;           // API + SSE: always network
  // App shell: cache-first, fall back to network and warm the cache.
  e.respondWith(
    caches.match(e.request).then((hit) =>
      hit || fetch(e.request).then((res) => {
        if (res && res.ok && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => hit)
    )
  );
});
