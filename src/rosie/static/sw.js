// Caches the app shell so the home-screen app opens instantly (and shows a
// clear "can't reach the server" message away from home). API calls are never
// cached. Network first, so updates show up at home.
const CACHE = "shell-v15";
const SHELL = ["/", "/static/app.js", "/static/style.css", "/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function withTimeout(p, ms) {
  return Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), ms))]);
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    withTimeout(fetch(e.request), 3000)
      .then((resp) => {
        if (resp.ok && SHELL.includes(url.pathname)) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(url.pathname, copy));
        }
        return resp;
      })
      .catch(() => caches.match(url.pathname, { ignoreSearch: true }))
  );
});
