/**
 * Virtual clock — lets the dashboard "rewind" to a past date so that all
 * rolling-window computations (90j volume, training load, records filter…)
 * are anchored on a simulated "now" instead of the real wall clock.
 *
 * The simulated value is persisted in localStorage and exposed via a
 * useSyncExternalStore hook so React consumers re-render automatically when
 * it changes.
 */

import { useSyncExternalStore } from 'react'

const STORAGE_KEY = 'garmin_virtual_now'

// one-shot migration: strava_virtual_now → garmin_virtual_now
try {
  const old = localStorage.getItem('strava_virtual_now')
  if (old) { localStorage.setItem('garmin_virtual_now', old); localStorage.removeItem('strava_virtual_now') }
} catch {}

let _virtualNow = null
let _realNow = Date.now()
let _realTimer = null
const _listeners = new Set()

// Hydrate from localStorage on module load.
try {
  if (typeof localStorage !== 'undefined') {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (typeof parsed === 'number' && parsed > 0) {
        _virtualNow = parsed
        console.log('[CLOCK] hydrated virtual now =', new Date(parsed).toISOString())
      }
    }
  }
} catch (e) {
  console.warn('[CLOCK] hydrate failed:', e)
}

/** Effective "now" in ms — virtual if set, otherwise a stable real-time snapshot. */
export function getNow() {
  return _virtualNow ?? _realNow
}

/** Raw simulated value (null if not set). */
export function getVirtualNow() {
  return _virtualNow
}

/** Set or clear the simulated now. Pass null to restore the real clock. */
export function setVirtualNow(ts) {
  const next = ts && Number(ts) > 0 ? Number(ts) : null
  if (next === _virtualNow) return
  _virtualNow = next
  if (next == null) {
    _realNow = Date.now()
  }
  try {
    if (next == null) localStorage.removeItem(STORAGE_KEY)
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch (e) {
    console.warn('[CLOCK] persist failed:', e)
  }
  syncRealClockTimer()
  console.log('[CLOCK] virtual now ->', next ? new Date(next).toISOString() : 'real')
  _listeners.forEach(fn => { try { fn(next) } catch {} })
}

function clearRealClockTimer() {
  if (_realTimer) {
    clearTimeout(_realTimer)
    _realTimer = null
  }
}

function syncRealClockTimer() {
  clearRealClockTimer()
  if (_virtualNow != null || _listeners.size === 0) return

  const now = Date.now()
  const nextMidnight = new Date(now)
  nextMidnight.setHours(24, 0, 0, 50)

  _realTimer = setTimeout(() => {
    _realNow = Date.now()
    syncRealClockTimer()
    _listeners.forEach(fn => { try { fn(_realNow) } catch {} })
  }, Math.max(1000, nextMidnight.getTime() - now))
}

function subscribe(fn) {
  _listeners.add(fn)
  if (!_realTimer) syncRealClockTimer()
  return () => {
    _listeners.delete(fn)
    if (_listeners.size === 0) clearRealClockTimer()
  }
}

/**
 * React hook returning the effective "now" timestamp. Components that read
 * this re-render when the simulated date changes, and when the real day rolls
 * over at local midnight.
 */
export function useNow() {
  return useSyncExternalStore(subscribe, getNow, getNow)
}

/** Hook returning the explicit virtual now (or null). */
export function useVirtualNow() {
  return useSyncExternalStore(subscribe, getVirtualNow, getVirtualNow)
}
