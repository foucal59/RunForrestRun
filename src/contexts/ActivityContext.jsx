import React, { createContext, useContext, useState, useCallback, useEffect, useMemo, useRef } from 'react'
import posthog from 'posthog-js'
import {
  loadActivitiesFromServer, loadComputedPRs, loadSyncStatus,
  getCachedShoes, fetchShoes, deleteActivity as apiDeleteActivity,
  loadGear, checkFreshness,
} from '../api'
import { parseLocalDate, localDateStr } from '../lib/compute'
import { useNow } from '../lib/clock'

const ActivityContext = createContext(null)
const DEFAULT_DATE_RANGE = { presetDays: 365 }

// Progressive loading: non-overlapping date segments, newest first. Each is a
// small bounded [since, before) query that never hits the serverless gateway
// timeout — the full history arrives in slices that are merged as they land,
// instead of one giant get_all_activities() that 504s on large DBs.
//   sinceDays=null  → no lower bound (everything older)
//   beforeDays=null → up to now
const SEGMENTS = [
  { sinceDays: 7,    beforeDays: null },  // last 7 days   (first, blocking)
  { sinceDays: 30,   beforeDays: 7 },     // 7–30 days
  { sinceDays: 90,   beforeDays: 30 },    // 30–90 days
  { sinceDays: 365,  beforeDays: 90 },    // 90–365 days
  { sinceDays: null, beforeDays: 365 },   // everything older than 1 year
]

const daysAgoStr = d => localDateStr(new Date(Date.now() - d * 86400000))
const segParams = seg => ({
  since: seg.sinceDays != null ? daysAgoStr(seg.sinceDays) : undefined,
  before: seg.beforeDays != null ? daysAgoStr(seg.beforeDays) : undefined,
})
// Merge a freshly-loaded slice into the accumulator map; return a new array
// sorted newest-first (matches the DB's ORDER BY start_date_local DESC).
const mergeById = (map, list) => {
  list.forEach(a => map.set(a.id, a))
  return Array.from(map.values()).sort((a, b) =>
    (b.start_date_local || '').localeCompare(a.start_date_local || ''))
}

function freshnessWarning(result) {
  if (!result) return 'Synchro Garmin impossible. Vérifiez la connexion puis réessayez.'
  if (result.reauth_required) return 'Session Garmin expirée. Reconnectez-vous pour relancer la synchro.'
  if (result.skipped && result.skipped !== 'no_session') return `Synchro Garmin interrompue (${result.skipped}).`
  if (result.database_sync && !result.database_sync.ok) {
    const done = (result.database_sync.synchronized || []).join(', ') || 'la base active'
    return `Runs synchronisés sur ${done}, mais une autre base est indisponible. Réessayez quand elle sera accessible.`
  }
  return null
}

export function ActivityProvider({ initialShoes = [], children }) {
  const [allActivities, setAllActivities] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState(null)
  const [dateRange, setDateRange] = useState(DEFAULT_DATE_RANGE)
  const [shoes, setShoes] = useState(() => {
    const cached = getCachedShoes()
    return initialShoes.length > 0 ? initialShoes : cached
  })
  const [computedPRs, setComputedPRs] = useState({})
  const [gearDetails, setGearDetails] = useState([])
  const [backfillStatus, setBackfillStatus] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncWarning, setSyncWarning] = useState(null)
  const activeLoadIdRef = useRef(0)
  const bootstrapStartedRef = useRef(false)

  const startLoad = useCallback(() => {
    activeLoadIdRef.current += 1
    return activeLoadIdRef.current
  }, [])

  const isCurrentLoad = useCallback(loadId => activeLoadIdRef.current === loadId, [])

  // ── Load secondary data (PRs, shoes, gear) — non-blocking ──
  const loadSecondaryData = useCallback(async (loadId = activeLoadIdRef.current) => {
    const jobs = [
      loadComputedPRs().then(prs => {
        const total = Object.values(prs).reduce((s, a) => s + a.length, 0)
        if (total > 0 && isCurrentLoad(loadId)) setComputedPRs(prs)
      }).catch(e => console.warn('[CONTEXT] PRs load error:', e)),

      fetchShoes().then(s => {
        if (isCurrentLoad(loadId)) setShoes(s)
      })
        .catch(e => console.warn('[CONTEXT] Shoes load error:', e)),

      loadGear().then(gear => {
        if (gear?.length && isCurrentLoad(loadId)) setGearDetails(gear)
        console.log(`[CONTEXT] ${gear?.length || 0} gear items loaded`)
      }).catch(e => console.warn('[CONTEXT] Gear load error:', e)),
    ]
    await Promise.allSettled(jobs)
  }, [isCurrentLoad])

  // ── Progressive segment loading ──
  // Loads SEGMENTS[1..] (everything before the first, already-loaded slice),
  // merging each bounded slice into `byId` as it arrives. A failed segment is
  // logged and skipped — we keep whatever already landed rather than blanking.
  const loadRemainingSegments = useCallback(async (byId, loadId) => {
    setLoadingMore(true)
    try {
      for (let i = 1; i < SEGMENTS.length; i++) {
        if (!isCurrentLoad(loadId)) return
        const seg = SEGMENTS[i]
        const { since, before } = segParams(seg)
        try {
          const { activities } = await loadActivitiesFromServer({ since, before })
          if (!isCurrentLoad(loadId)) return
          if (activities.length) {
            const merged = mergeById(byId, activities)
            setAllActivities(merged)
            console.log(`[CONTEXT] Segment ${seg.sinceDays ?? 'all'}–${seg.beforeDays ?? 'now'}d: +${activities.length} → ${merged.length} total`)
          }
        } catch (e) {
          console.warn(`[CONTEXT] Segment ${seg.sinceDays ?? 'all'}–${seg.beforeDays ?? 'now'}d failed (keeping what we have):`, e?.message || e)
        }
      }
      // Sync/backfill status — fetched separately so we never need a full load.
      loadSyncStatus().then(s => {
        if (s && isCurrentLoad(loadId)) setBackfillStatus(s)
      })
    } finally {
      if (isCurrentLoad(loadId)) setLoadingMore(false)
    }
  }, [isCurrentLoad])

  // ── Load from server ──
  const loadFromServer = useCallback(async ({ incremental = false } = {}) => {
    const loadId = startLoad()
    try {
      // First slice: last 7 days — ultra-fast, no COUNT, unblocks the UI.
      const { since, before } = segParams(SEGMENTS[0])
      const { activities: first } = await loadActivitiesFromServer({ since, before })
      if (!isCurrentLoad(loadId)) return 0
      const byId = new Map(first.map(a => [a.id, a]))
      setAllActivities(first)
      console.log(`[CONTEXT] First segment (last ${SEGMENTS[0].sinceDays}d): ${first.length} activities`)

      if (incremental) {
        // Remaining segments fire immediately (sequential, so they queue naturally).
        // Secondary data (PRs, shoes, gear) is delayed 8s to avoid racing with
        // segments 2-5 on Neon connections during cold start.
        loadRemainingSegments(byId, loadId)
        setTimeout(() => {
          if (isCurrentLoad(loadId)) loadSecondaryData(loadId)
        }, 8000)
        return first.length
      }

      // Non-incremental (refresh / freshness): await the full history before
      // returning, so callers see the complete dataset.
      await loadRemainingSegments(byId, loadId)
      if (!isCurrentLoad(loadId)) return 0
      await loadSecondaryData(loadId)
      console.log(`[CONTEXT] Full reload complete: ${byId.size} activities`)
      return byId.size
    } catch (e) {
      console.error('[CONTEXT] Load error:', e)
      throw e
    }
  }, [isCurrentLoad, loadSecondaryData, loadRemainingSegments, startLoad])

  // ── On-open freshness probe ──
  // Asks the backend to pull any runs from Garmin newer than the latest one
  // in Neon. Works the same on localhost and Vercel (see
  // /api/data/freshness-check). Runs after the initial DB read so the
  // dashboard is never gated on a Garmin round-trip.
  const runFreshnessCheck = useCallback(async ({ allDatabases = false } = {}) => {
    console.log(`[CONTEXT] Running freshness check against Garmin${allDatabases ? ' and all databases' : ''}…`)
    const result = await checkFreshness({ allDatabases })
    if (!result) {
      console.log('[CONTEXT] freshness check: no result (auth or network issue)')
      setSyncWarning(freshnessWarning(result))
      return 0
    }
    setSyncWarning(freshnessWarning(result))
    const added = result.added || 0
    console.log(`[CONTEXT] freshness: added=${added} checked=${result.checked} skipped=${result.skipped || 'none'} latest=${result.latest || 'n/a'}`)
    console.log(`[CONTEXT] freshness diag: queried after=${result.after_iso || '?'} | garmin_returned=${result.garmin_returned ?? '?'} garmin_runs=${result.garmin_runs ?? '?'} new_after_dedup=${result.new_after_dedup ?? '?'}`)
    if (added > 0) {
      console.log(`[CONTEXT] ${added} new run(s) — reloading from DB`)
      try {
        await loadFromServer()
      } catch (e) {
        console.warn('[CONTEXT] reload after freshness failed:', e?.message || e)
      }
    }
    return added
  }, [loadFromServer])

  // ── Initial load ──
  // Read the active primary DB, then kick off a non-blocking Garmin freshness
  // probe. The backend replicates any new writes to its optional secondary DB.
  useEffect(() => {
    if (bootstrapStartedRef.current) return
    bootstrapStartedRef.current = true
    let cancelled = false

    async function bootstrap() {
      setLoading(true)
      setError(null)

      try {
        // Pas de probe /api/data/ready préalable — un round-trip pour une
        // valeur de log ; loadFromServer échoue vite si Neon est injoignable.
        if (cancelled) return
        await loadFromServer({ incremental: true })
      } catch (e) {
        if (!cancelled) setError(e.message)
      } finally {
        if (!cancelled) setLoading(false)
      }

      // Fire-and-forget: don't block the dashboard render on Garmin.
      // Delayed 12s so segments + secondary data have time to load from Neon
      // before the freshness check adds connection pressure.
      if (!cancelled) {
        let retries = 0
        const doCheck = async () => {
          try {
            const added = await runFreshnessCheck()
            if (added === 0 && retries < 5 && !cancelled) {
              retries++
              console.log(`[BOOTSTRAP] no new runs yet — will re-check in 2 min (attempt ${retries}/5)`)
              setTimeout(doCheck, 2 * 60 * 1000)
            }
          } catch (e) {
            console.warn('[BOOTSTRAP] freshness check threw:', e?.message || e)
          }
        }
        setTimeout(doCheck, 12000)
      }
    }

    bootstrap()
    return () => { cancelled = true }
  }, [loadFromServer, runFreshnessCheck])

  // Fetch shoes if empty (one-shot, reads from DB)
  useEffect(() => {
    if (shoes.length > 0 || loading) return
    let cancelled = false
    fetchShoes().then(s => {
      if (!cancelled && s.length > 0) setShoes(s)
    })
    return () => { cancelled = true }
  }, [loading]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Delete a single activity from local DB ──
  const deleteActivity = useCallback(async (activityId) => {
    console.log('[CONTEXT] Deleting activity', activityId)
    const target = allActivities.find(activity =>
      Number(activity.id) === Number(activityId) ||
      (Array.isArray(activity.merged_ids) && activity.merged_ids.some(id => Number(id) === Number(activityId)))
    )
    const idsToDelete = Array.from(new Set(
      Array.isArray(target?.merged_ids) && target.merged_ids.length
        ? target.merged_ids.map(id => Number(id))
        : [Number(activityId)]
    ))
    const results = await Promise.allSettled(idsToDelete.map(id => apiDeleteActivity(id)))
    const deletedIds = idsToDelete.filter((_, idx) => results[idx].status === 'fulfilled')
    if (!deletedIds.length) {
      throw new Error(`Delete failed for activity ${activityId}`)
    }
    // Remove immediately from local state (no need to re-fetch everything)
    setAllActivities(prev => prev.filter(activity => {
      const mergedIds = Array.isArray(activity.merged_ids) && activity.merged_ids.length
        ? activity.merged_ids.map(id => Number(id))
        : [Number(activity.id)]
      return !mergedIds.some(id => deletedIds.includes(id))
    }))
    console.log('[CONTEXT] Activity group', idsToDelete, 'removed from state')
  }, [allActivities])

  // ── Manual refresh ──
  // Le bouton "synchro" fait les trois opérations : poller Garmin, converger
  // toutes les bases disponibles, puis recharger la base active.
  // runFreshnessCheck recharge déjà tout seul quand added > 0 ; sinon on
  // force un loadFromServer pour que le clic ait toujours un effet visible.
  const refresh = useCallback(async () => {
    if (syncing) return
    console.log('[CONTEXT] Manual refresh: Garmin + all databases + reload')
    setSyncing(true)
    let added = 0
    try {
      added = await runFreshnessCheck({ allDatabases: true })
    } catch (e) {
      console.warn('[CONTEXT] Manual refresh: freshness failed:', e?.message || e)
    }
    posthog.capture('garmin_data_refreshed', { activities_added: added })
    if (added === 0) {
      try {
        await loadFromServer()
      } catch (e) {
        console.error('[CONTEXT] Refresh error:', e)
      }
    }
    setSyncing(false)
  }, [loadFromServer, runFreshnessCheck, syncing])

  // ── Virtual now (date simulation) ──
  // The clock module handles localStorage persistence; we just hook into it
  // here so the activities filter and effectiveDateRange recompute when the
  // user picks a simulated date.
  const now = useNow()

  // ── Resolve dateRange to absolute {from, to} bounds ──
  // Supports two shapes:
  //   { presetDays: 90 }   → rolling window relative to current `now`
  //   { from: ts, to: ts } → explicit slider drag (legacy; to=null means now)
  const effectiveDateRange = useMemo(() => {
    if (!dateRange) return null
    if (dateRange.presetDays != null) {
      const to = now
      const from = to - dateRange.presetDays * 86400000
      return { from, to, presetDays: dateRange.presetDays }
    }
    return {
      from: dateRange.from,
      to: dateRange.to ?? now,
    }
  }, [dateRange, now])

  // ── Filtered activities ──
  // Always returns a fresh array reference whenever `now` changes — even when
  // no date filter is active — so that downstream useMemo([activities]) calls
  // recompute time-anchored values (TSB/CTL/ATL, rolling windows, etc.) when
  // the user simulates a different "today".
  const activities = useMemo(() => {
    if (!effectiveDateRange) {
      console.log('[FILTER] No dateRange, returning all', allActivities.length, 'activities (now=', new Date(now).toISOString().slice(0,10), ')')
      return allActivities.slice()
    }
    const { from, to } = effectiveDateRange
    const filtered = allActivities.filter(a => {
      const t = parseLocalDate(a.start_date_local).getTime()
      return t >= from && t <= to
    })
    console.log('[FILTER]', filtered.length, '/', allActivities.length, 'activities (now=', new Date(now).toISOString().slice(0,10), ')')
    return filtered
  }, [allActivities, effectiveDateRange, now])

  return (
    <ActivityContext.Provider value={{
      activities, allActivities, loading, loadingMore, error, syncing,
      shoes, gearDetails,
      syncWarning,
      refresh, deleteActivity,
      dateRange, setDateRange, effectiveDateRange,
      computedPRs, backfillStatus,
      now,
    }}>
      {children}
    </ActivityContext.Provider>
  )
}

export function useActivities() {
  const ctx = useContext(ActivityContext)
  if (!ctx) throw new Error('useActivities must be used within ActivityProvider')
  return ctx
}
