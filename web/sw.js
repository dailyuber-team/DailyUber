// Fonctionnement hors connexion : on sert toujours la version en ligne si possible,
// sinon la dernière version gardée en cache (interface et offres du jour).
const CACHE = "dailyuber-v3";
const SHELL = ["./", "index.html", "style.css", "app.js", "engine.js", "manifest.webmanifest",
  "icons/icon-192.png", "icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { cache: "reload" })))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // no-cache : on revalide auprès du serveur (réponse 304 légère) plutôt que de risquer une
  // app.js récente avec une engine.js encore en cache HTTP (GitHub Pages : 10 min).
  e.respondWith(fetch(e.request, { cache: "no-cache" })
    .then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return res;
    })
    .catch(() => caches.match(e.request, { ignoreSearch: true })));
});
