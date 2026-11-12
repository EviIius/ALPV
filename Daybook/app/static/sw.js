// Daybook service worker: caches the app shell so it opens instantly and
// offline. API responses (your notes) are never cached here.
const VERSION = 'daybook-2.2.2';
const SHELL = ['/', '/index.html', '/css/base.css', '/css/app.css', '/css/editor.css', '/css/reader.css', '/js/theme.js', '/js/main.js',
  '/vendor/preact.js', '/vendor/md.js', '/icons/sprite.svg', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);
  // Your notes (the API) and the private calendar feed are never cached here.
  if (req.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/') || url.pathname.startsWith('/feed/')) return;
  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match('/index.html')));
    return;
  }
  // Stale-while-revalidate for static files.
  event.respondWith(caches.open(VERSION).then(async (cache) => {
    const cached = await cache.match(req);
    const fresh = fetch(req).then((res) => { if (res.ok) cache.put(req, res.clone()); return res; }).catch(() => cached);
    return cached || fresh;
  }));
});
