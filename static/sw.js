// Service Worker mínimo — necessário para o Chrome permitir instalação PWA
// Não faz cache agressivo pois a app precisa de SSE em tempo real

const CACHE_NAME = 'claude-monitor-v1';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Passa todos os pedidos à rede — sem cache offline
// (a app não faz sentido offline pois depende do servidor local)
self.addEventListener('fetch', (event) => {
  event.respondWith(fetch(event.request));
});
