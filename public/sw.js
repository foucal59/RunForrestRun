/**
 * Service Worker — offline support for Garmin Running Dashboard.
 *
 * Strategy:
 *   - Static assets (JS, CSS, fonts): cache-first, update in background
 *   - API data endpoints (/api/data/*): network-first, cache fallback (offline viewing)
 *   - Auth/sync/streams: always network, never cache
 */

// v9 : purge des caches v8, qui pouvaient contenir du HTML stocke sous une URL
// de chunk JS (voir isHtmlResponse plus bas) — un appareil empoisonne restait
// bloque sur /plan tant que son cache n'etait pas vide.
const CACHE_VERSION = 'v9'
const STATIC_CACHE = `static-${CACHE_VERSION}`
const DATA_CACHE = `data-${CACHE_VERSION}`

// Vite dev serves unhashed module URLs; a cache-first SW would pin users to
// stale code. Disable the SW entirely on those origins.
const IS_DEV_ORIGIN = self.location.port === '5173' || self.location.port === '5174'

// API data routes to cache for offline use
const DATA_PREFIXES = ['/api/data/activities', '/api/data/prs', '/api/data/photos']

// Routes to never cache (auth tokens, live sync, streams)
const SKIP_CACHE_PREFIXES = ['/api/auth', '/api/data/sync', '/api/streams', '/api/health']

/** Une reponse HTML servie pour un asset = fallback SPA, jamais du vrai asset. */
function isHtmlResponse(response) {
  return (response?.headers?.get('content-type') || '').includes('text/html')
}

self.addEventListener('install', () => {
  console.log('[SW] Installed', CACHE_VERSION, 'dev=', IS_DEV_ORIGIN)
  self.skipWaiting()
})

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    if (IS_DEV_ORIGIN) {
      try { await self.registration.unregister() }
      catch (e) { console.warn('[SW] self-unregister failed', e) }
      return
    }
    const keys = await caches.keys()
    await Promise.all(
      keys.filter(k => k !== STATIC_CACHE && k !== DATA_CACHE)
          .map(k => caches.delete(k))
    )
    await self.clients.claim()
  })())
})

self.addEventListener('fetch', event => {
  if (IS_DEV_ORIGIN) return

  const { request } = event
  const url = new URL(request.url)

  // Only handle same-origin GET requests
  if (request.method !== 'GET' || url.origin !== location.origin) return

  // Never touch auth/sync/streams — OAuth uses 302 redirects and SW-proxied
  // navigations break the redirect chain (opaqueredirect responses don't
  // always follow through reliably across browsers).
  if (SKIP_CACHE_PREFIXES.some(p => url.pathname.startsWith(p))) return

  // HTML navigations should always try network first so deployments are not stuck
  // behind an old cached index.html.
  if (request.mode === 'navigate' || request.destination === 'document') {
    event.respondWith(
      fetch(request)
        .then(res => {
          if (res.ok && res.type === 'basic') {
            const clone = res.clone()
            caches.open(STATIC_CACHE).then(c => c.put(request, clone))
          }
          return res
        })
        .catch(() => caches.match(request))
    )
    return
  }

  // Network-first for API data (offline fallback to cache)
  if (DATA_PREFIXES.some(p => url.pathname.startsWith(p))) {
    event.respondWith(
      fetch(request)
        .then(res => {
          if (res.ok) {
            const clone = res.clone()
            caches.open(DATA_CACHE).then(c => c.put(request, clone))
          }
          return res
        })
        .catch(() => {
          console.log('[SW] Offline — serving cached', url.pathname)
          return caches.match(request)
        })
    )
    return
  }

  // Skip remaining API routes (not cached)
  if (url.pathname.startsWith('/api/')) return

  // Cache-first for static assets (JS/CSS/fonts have hashed names → auto-invalidate)
  event.respondWith(
    caches.match(request).then(cached => {
      // Un chunk absent (deploiement en cours, build precedent) est reecrit par
      // Vercel en /index.html avec un 200 : sans ce garde-fou on cache du HTML
      // sous une URL .js, et l'import dynamique de la page casse *definitivement*
      // sur cet appareil.
      if (cached && isHtmlResponse(cached)) {
        console.warn('[SW] Poisoned cache entry (HTML) for', url.pathname, '— dropping')
        caches.open(STATIC_CACHE).then(c => c.delete(request))
        cached = null
      }
      const networkFetch = fetch(request).then(res => {
        if (res.ok && !isHtmlResponse(res)) {
          const clone = res.clone()  // clone synchronously before res body is consumed
          caches.open(STATIC_CACHE).then(c => c.put(request, clone))
        } else if (res.ok) {
          console.warn('[SW] Not caching HTML served for', url.pathname)
        }
        return res
      })
      // Serve cache immediately when present; refresh in the background but
      // swallow failures so a network hiccup (504 cold start / offline) never
      // surfaces as an unhandled rejection.
      if (cached) {
        networkFetch.catch(() => {})
        return cached
      }
      // Not cached: must hit the network. On failure, resolve to a proper error
      // Response instead of letting the rejection escape respondWith.
      return networkFetch.catch(() => Response.error())
    })
  )
})
