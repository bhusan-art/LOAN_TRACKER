// Minimal service worker: makes the app installable. It caches only the icon; pages, the
// app's code and all data are always fetched live (money must never be stale).
const CACHE = "ledgerly-static-v2";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/static/icon.svg"])));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname === "/static/icon.svg") {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
  }
});
