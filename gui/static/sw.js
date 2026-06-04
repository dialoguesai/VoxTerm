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
  "/static/backend-remote.js",
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

  // Navigations: network-first so you always get the latest index.html when online;
  // offline, fall back to the cached shell (ignoreSearch so "/?token=..." resolves to "/").
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/", { ignoreSearch: true })));
    return;
  }

  // Static shell (js/css/icons/manifest): stale-while-revalidate — serve cache instantly,
  // refresh it in the background, so a changed app.js/style.css is picked up on the NEXT
  // load WITHOUT having to bump CACHE by hand (avoids the permanently-stale-shell trap).
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((res) => {
        if (res && res.ok && res.type === "basic") {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});
