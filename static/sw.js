// Минимальный service worker: кэш оболочки, данные всегда из сети.
const CACHE = "dungeon-v1";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/icon-192.png"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  // сеть в приоритете, кэш — если ноут недоступен
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok && url.origin === location.origin) caches.open(CACHE).then((c) => c.put(e.request, r.clone()));
    return r;
  }).catch(() => caches.match(e.request, { ignoreSearch: true })));
});
