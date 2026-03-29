// Minimal service worker — required for Chrome to allow PWA installation
// No aggressive caching since the app relies on real-time SSE

const CACHE_NAME = 'claude-monitor-v1';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Pass all requests to the network — no offline cache
// (the app makes no sense offline since it depends on the local server)
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
