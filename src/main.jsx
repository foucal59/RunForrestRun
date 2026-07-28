import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import posthog from 'posthog-js'
import App from './App'
import './index.css'

if (import.meta.env.PROD && import.meta.env.VITE_POSTHOG_TOKEN) {
  posthog.init(import.meta.env.VITE_POSTHOG_TOKEN, {
    person_profiles: 'identified_only',
  })
}

const RootWrapper = import.meta.env.DEV ? React.Fragment : React.StrictMode

// Vite dev serves modules at unhashed URLs, so a cache-first SW would pin
// users to stale code. Strip any prior registration when running on the dev
// server, and only register in production builds.
if ('serviceWorker' in navigator) {
  if (import.meta.env.DEV) {
    navigator.serviceWorker.getRegistrations()
      .then(regs => regs.forEach(r => r.unregister()))
      .catch(err => console.warn('[SW] dev unregister failed:', err))
    if ('caches' in window) {
      caches.keys()
        .then(keys => keys.forEach(k => caches.delete(k)))
        .catch(err => console.warn('[SW] dev cache purge failed:', err))
    }
  } else {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' })
        .then(reg => {
          reg.update().catch(err => console.warn('[SW] Update check failed:', err))
          console.log('[SW] Registered:', reg.scope)
        })
        .catch(err => console.warn('[SW] Registration failed:', err))
    })
  }
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <RootWrapper>
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <App />
    </BrowserRouter>
  </RootWrapper>
)
