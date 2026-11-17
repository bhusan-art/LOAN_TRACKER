// Minimal service worker: makes the app installable and caches the static
// shell. Pages and data are always fetched live (money must never be stale).
const CACHE = "loans-static-v1";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/static/app.css", "/static/icon.svg"])));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
  }
});
