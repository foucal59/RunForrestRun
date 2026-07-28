/**
 * API client — reads from the Neon PostgreSQL DB only.
 *
 * The DB is the single source of truth. The backend only calls Garmin to
 * top up the delta on open (see `checkFreshness`). All other calls are DB reads.
 */

const SHOES_CACHE_STORAGE = 'garmin-shoes-v2'
const DAILY_PLAN_SOURCE = 'marathon-template'
const DAILY_PLAN_DESCRIPTION = 'Coach Marathon (modèle de 16 semaines à personnaliser)'
const DAILY_PLAN_BASIS = 'Adapte sur les 10 derniers entrainements charges'

// ── Helpers ──

async function fetchAPI(path, options = {}, { retries = 0 } = {}) {
  const method = (options.method || 'GET').toUpperCase()
  // Only replay safe (idempotent) reads. Never retry a POST/DELETE — a 500 may
  // mean the write half-applied.
  const idempotent = method === 'GET' || method === 'HEAD'
  for (let attempt = 0; attempt <= retries; attempt++) {
    let resp
    try {
      resp = await fetch(path, {
        ...options,
        credentials: 'include',
        headers: { ...options.headers },
      })
    } catch (netErr) {
      if (idempotent && attempt < retries) {
        const wait = 1000 * (attempt + 1)
        console.warn(`[API] ${path} network error, retrying in ${wait}ms (${attempt + 1}/${retries})…`)
        await new Promise(r => setTimeout(r, wait))
        continue
      }
      throw netErr
    }
    if (resp.ok) return resp.json()
    // Retry transient server errors (500/502/503/504) on idempotent reads.
    // Neon cold start takes 5-15s — retry delays must be long enough to let it wake.
    if (resp.status >= 500 && idempotent && attempt < retries) {
      const wait = 5000 * (attempt + 1)  // 5s, 10s, 15s
      console.warn(`[API] ${path} → ${resp.status}, retrying in ${wait}ms (${attempt + 1}/${retries})…`)
      await new Promise(r => setTimeout(r, wait))
      continue
    }
    let detail = ''
    try {
      const body = await resp.clone().json()
      detail = body?.detail || ''
    } catch {
      // Keep the generic status below when the backend did not send JSON.
    }
    throw new Error(detail || `API ${resp.status}`)
  }
}

const WEATHER_HOURLY = [
  'temperature_2m',
  'apparent_temperature',
  'relative_humidity_2m',
  'precipitation',
  'weather_code',
  'wind_speed_10m',
  'wind_gusts_10m',
].join(',')

const WEATHER_CACHE = new Map()

const WMO_WEATHER = {
  0: ['Ciel clair', '☀️'],
  1: ['Plutôt clair', '🌤️'],
  2: ['Partiellement nuageux', '⛅'],
  3: ['Couvert', '☁️'],
  45: ['Brouillard', '🌫️'],
  48: ['Brouillard givrant', '🌫️'],
  51: ['Bruine légère', '🌦️'],
  53: ['Bruine', '🌦️'],
  55: ['Bruine dense', '🌧️'],
  56: ['Bruine verglaçante', '🌧️'],
  57: ['Bruine verglaçante dense', '🌧️'],
  61: ['Pluie faible', '🌧️'],
  63: ['Pluie', '🌧️'],
  65: ['Pluie forte', '🌧️'],
  66: ['Pluie verglaçante', '🌧️'],
  67: ['Pluie verglaçante forte', '🌧️'],
  71: ['Neige faible', '🌨️'],
  73: ['Neige', '🌨️'],
  75: ['Neige forte', '❄️'],
  77: ['Grains de neige', '❄️'],
  80: ['Averses faibles', '🌦️'],
  81: ['Averses', '🌧️'],
  82: ['Averses fortes', '⛈️'],
  85: ['Averses de neige', '🌨️'],
  86: ['Averses de neige fortes', '❄️'],
  95: ['Orage', '⛈️'],
  96: ['Orage avec grêle', '⛈️'],
  99: ['Orage avec grêle forte', '⛈️'],
}

function parseRunWeatherTime(activity) {
  const value = String(activity?.start_date_local || '')
  const match = value.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2})/)
  if (!match) return null
  return { dateIso: match[1], hour: Number(match[2]) }
}

function weatherEndpoint(dateIso) {
  const start = new Date(`${dateIso}T00:00:00`)
  const ageDays = Math.floor((Date.now() - start.getTime()) / 86400000)
  return ageDays > 5
    ? 'https://archive-api.open-meteo.com/v1/archive'
    : 'https://api.open-meteo.com/v1/forecast'
}

// Météo stockée en base (colonnes weather_* remplies par le backfill
// Open-Meteo) — même forme que le retour du fetch loadRunWeather.
export function runWeatherFromActivity(activity) {
  const temp = Number(activity?.weather_temperature)
  if (!Number.isFinite(temp)) return null
  const code = Number(activity?.weather_code)
  const [label, emoji] = WMO_WEATHER[code] || ['Météo', '🌡️']
  return {
    source: String(activity?.weather_source || '').includes('archive')
      ? 'Open-Meteo historique'
      : 'Open-Meteo prévision',
    code,
    label,
    emoji,
    temperature: temp,
    apparentTemperature: Number(activity?.weather_apparent_temperature),
    humidity: Number(activity?.weather_humidity),
    precipitation: Number(activity?.weather_precipitation),
    windSpeed: Number(activity?.weather_wind_speed),
    windGusts: Number(activity?.weather_wind_gusts),
  }
}

export async function loadRunWeather(activity) {
  const fromDb = runWeatherFromActivity(activity)
  if (fromDb) {
    console.log('[loadRunWeather] météo depuis la base pour run', activity?.id, '·', fromDb.temperature, '°C', fromDb.label)
    return fromDb
  }

  const coords = activity?.start_latlng
  const lat = Number(coords?.[0])
  const lon = Number(coords?.[1])
  const time = parseRunWeatherTime(activity)
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !time) return null

  const cacheKey = `${activity.id}:${time.dateIso}:${time.hour}:${lat.toFixed(4)}:${lon.toFixed(4)}`
  if (WEATHER_CACHE.has(cacheKey)) return WEATHER_CACHE.get(cacheKey)

  const endpoint = weatherEndpoint(time.dateIso)
  const params = new URLSearchParams({
    latitude: String(Math.round(lat * 100000) / 100000),
    longitude: String(Math.round(lon * 100000) / 100000),
    hourly: WEATHER_HOURLY,
    start_date: time.dateIso,
    end_date: time.dateIso,
    // garmin_timezone_id est un ID numérique Garmin (ex: 124), pas une zone
    // IANA — l'envoyer tel quel fait répondre 400 à Open-Meteo.
    timezone: /\//.test(String(activity.garmin_timezone_id || '')) ? activity.garmin_timezone_id : 'Europe/Paris',
  })
  const resp = await fetch(`${endpoint}?${params.toString()}`)
  if (!resp.ok) throw new Error(`Weather ${resp.status}`)
  const data = await resp.json()
  const hourly = data?.hourly || {}
  const target = `${time.dateIso}T${String(time.hour).padStart(2, '0')}:00`
  const idx = Array.isArray(hourly.time)
    ? (hourly.time.indexOf(target) >= 0 ? hourly.time.indexOf(target) : Math.min(time.hour, hourly.time.length - 1))
    : -1
  if (idx < 0) return null

  const code = Number(hourly.weather_code?.[idx])
  const [label, emoji] = WMO_WEATHER[code] || ['Météo', '🌡️']
  const weather = {
    source: endpoint.includes('archive') ? 'Open-Meteo historique' : 'Open-Meteo prévision',
    time: hourly.time?.[idx] || target,
    code,
    label,
    emoji,
    temperature: Number(hourly.temperature_2m?.[idx]),
    apparentTemperature: Number(hourly.apparent_temperature?.[idx]),
    humidity: Number(hourly.relative_humidity_2m?.[idx]),
    precipitation: Number(hourly.precipitation?.[idx]),
    windSpeed: Number(hourly.wind_speed_10m?.[idx]),
    windGusts: Number(hourly.wind_gusts_10m?.[idx]),
  }
  WEATHER_CACHE.set(cacheKey, weather)
  return weather
}

// ── Public: Cache management ──

export function clearTokens() {
  localStorage.removeItem(SHOES_CACHE_STORAGE)
}

export function clearAllCache() {
  const keysToRemove = []
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key && (key.startsWith('garmin_') || key.startsWith('strava_'))) keysToRemove.push(key)
  }
  keysToRemove.forEach(k => localStorage.removeItem(k))
}

export function getCachedShoes() {
  try {
    const raw = localStorage.getItem(SHOES_CACHE_STORAGE)
    return raw ? JSON.parse(raw) : []
  } catch { return [] }
}

export async function fetchShoes() {
  try {
    const data = await fetchAPI('/api/data/shoes')
    const shoes = data?.shoes || []
    // one-shot migration from the legacy storage name
    localStorage.removeItem('strava_shoes_v2')
    localStorage.setItem(SHOES_CACHE_STORAGE, JSON.stringify(shoes))
    return shoes
  } catch { return [] }
}

// ── DB reads ──

/**
 * Load activities from the DB.
 * - since / before: ISO date strings → bounded date segment (no COUNT, fast).
 *   Either or both may be set; a small segment never hits the gateway timeout.
 * - none: full load + sync status (legacy)
 */
export async function loadActivitiesFromServer({ since, before } = {}) {
  const qs = []
  if (since) qs.push(`since=${since}`)
  if (before) qs.push(`before=${before}`)
  const params = qs.length ? `?${qs.join('&')}` : ''
  const data = await fetchAPI(`/api/data/activities${params}`, {}, { retries: 3 })
  console.log(`[API] ${data.count} activities from server (since=${since || '-'}, before=${before || '-'}, partial=${data.partial || false})`)
  return { activities: data.activities, total: data.total, sync: data.sync, partial: !!data.partial }
}

/**
 * Fetch sync/backfill status from the DB (lightweight, count-only queries).
 * Used to populate backfillStatus without a full activity load.
 */
export async function loadSyncStatus() {
  try {
    return await fetchAPI('/api/data/status', {}, { retries: 3 })
  } catch (e) {
    console.warn('[API] sync status load failed:', e?.message || e)
    return null
  }
}

/**
 * Load computed PRs from server.
 */
export async function loadComputedPRs() {
  const data = await fetchAPI('/api/data/prs', {}, { retries: 3 })
  const total = Object.values(data.prs).reduce((s, a) => s + a.length, 0)
  console.log(`[API] ${total} computed PRs from server`)
  return data.prs
}

/**
 * Load gear details from server.
 */
export async function loadGear() {
  const data = await fetchAPI('/api/data/gear')
  console.log(`[API] ${data.count} gear items from server`)
  return data.gear
}

/** VO2max evolution (Garmin) → { history:[{date,vo2max}], latest, count }. */
export async function loadVo2max() {
  try {
    const data = await fetchAPI('/api/data/vo2max')
    console.log(`[API] ${data?.count ?? 0} VO2max points (latest=${data?.latest ?? '?'})`)
    return data || { history: [], latest: null, count: 0 }
  } catch (e) {
    console.warn('[API] loadVo2max failed:', e?.message || e)
    return { history: [], latest: null, count: 0 }
  }
}

/** Garmin-native training status → { status, trainingLoad?, acwr?, fitnessTrend?, raw? }. */
export async function loadTrainingStatus() {
  try {
    const data = await fetchAPI('/api/data/training-status')
    console.log('[API] training status:', data?.status ?? 'null')
    return data || { status: null }
  } catch (e) {
    console.warn('[API] loadTrainingStatus failed:', e?.message || e)
    return { status: null }
  }
}

function activityDateKey(activity) {
  return String(activity?.start_date_local || activity?.date || '').slice(0, 10)
}

function isRunActivity(activity) {
  const type = String(activity?.type || activity?.sport_type || '').toLowerCase()
  return type === 'run'
}

export function recentTrainingRunsFromActivities(activities, day, limit = 10) {
  const targetDay = String(day || new Date().toISOString().slice(0, 10)).slice(0, 10)
  return (activities || [])
    .filter(activity => isRunActivity(activity))
    .map(activity => {
      const date = activityDateKey(activity)
      const distanceM = Number(activity.distance_m ?? activity.distance ?? 0) || 0
      const movingTime = Number(activity.moving_time ?? 0) || 0
      return {
        id: activity.id,
        name: activity.name || 'Run',
        start_date_local: activity.start_date_local || date,
        date,
        distance_m: distanceM,
        distance_km: Math.round((distanceM / 1000) * 100) / 100,
        moving_time: movingTime,
        pace_sec_per_km: distanceM > 0 && movingTime > 0 ? movingTime / (distanceM / 1000) : null,
        average_heartrate: activity.average_heartrate != null ? Number(activity.average_heartrate) : null,
        max_heartrate: activity.max_heartrate != null ? Number(activity.max_heartrate) : null,
      }
    })
    .filter(run => run.date && run.date <= targetDay)
    .sort((a, b) => String(b.start_date_local || '').localeCompare(String(a.start_date_local || '')))
    .slice(0, limit)
}

// Meme jour + memes runs recents => meme seance. On memoise le resultat le
// temps de la session SPA pour ne PAS recreer/recalculer l'entrainement a
// chaque retour sur la page Cockpit (navigation interne). Un vrai changement
// (nouveau run, changement de jour) change la cle et declenche un recalcul.
const DAILY_TRAINING_CACHE = new Map()

function dailyTrainingCacheKey(day, recentRuns = []) {
  const sig = (recentRuns || [])
    .map(run => `${run.id}:${run.start_date_local || run.date}:${run.distance_m}:${run.moving_time}`)
    .join('|')
  return `${day || 'today'}::${sig}`
}

/**
 * Lecture synchrone du cache (sans fetch). Permet a Cockpit d'hydrater son etat
 * initial au remontage sans repasser par le serveur ni afficher de spinner.
 * Retourne null si rien n'est encore en cache pour ce (jour, runs).
 */
export function peekDailyTraining(day, recentRuns = []) {
  return DAILY_TRAINING_CACHE.get(dailyTrainingCacheKey(day, recentRuns)) || null
}

/** Daily training guidance → observations + adjustment + sessions for J to J+7. */
export async function loadDailyTraining(day, recentRuns = []) {
  const cacheKey = dailyTrainingCacheKey(day, recentRuns)
  if (DAILY_TRAINING_CACHE.has(cacheKey)) {
    return DAILY_TRAINING_CACHE.get(cacheKey)
  }
  try {
    const qs = day ? `?day=${encodeURIComponent(day)}` : ''
    if (recentRuns?.length) {
      try {
        const data = await fetchAPI('/api/data/daily-training', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ day, recentRuns }),
        })
        if (data) { DAILY_TRAINING_CACHE.set(cacheKey, data); return data }
        console.warn('[API] loadDailyTraining POST returned empty, falling back to DB read')
      } catch (postError) {
        console.warn('[API] loadDailyTraining POST failed, falling back to DB read:', postError?.message || postError)
      }
    }
    const data = await fetchAPI(`/api/data/daily-training${qs}`, {}, { retries: 2 })
    if (data) { DAILY_TRAINING_CACHE.set(cacheKey, data); return data }
    console.warn('[API] loadDailyTraining returned empty, falling back to coach snapshot')
    return loadDailyTrainingFromCoachSnapshot(day)
  } catch (e) {
    console.warn('[API] loadDailyTraining failed, falling back to coach snapshot:', e?.message || e)
    return loadDailyTrainingFromCoachSnapshot(day)
  }
}

/** Full marathon plan with per-session paces, HR targets and fueling. */
export async function loadPlanOverview() {
  console.log('[API] loadPlanOverview → /api/data/plan-overview')
  return fetchAPI('/api/data/plan-overview', {}, { retries: 2 })
}

/** Create the structured workout directly in Garmin Connect. */
export async function sendWorkoutToGarmin(day, workout = null) {
  const qs = day ? `?day=${encodeURIComponent(day)}` : ''
  const options = { method: 'POST' }
  if (workout) {
    options.headers = { 'Content-Type': 'application/json' }
    options.body = JSON.stringify({ workout })
  }
  return fetchAPI(`/api/data/workout-garmin${qs}`, options)
}

function formatCoachDateLabel(dateString) {
  if (!dateString) return ''
  const [year, month, day] = dateString.split('-').map(Number)
  if (!year || !month || !day) return dateString
  return new Date(year, month - 1, day).toLocaleDateString('fr-FR', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

function addDays(dateString, days) {
  const [year, month, day] = dateString.split('-').map(Number)
  if (!year || !month || !day) return dateString
  const d = new Date(year, month - 1, day)
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

function coachSessionTitle(text) {
  const clean = String(text || '').trim()
  if (!clean) return 'Séance coach'
  const [prefix] = clean.split(/\s*:\s*/)
  return prefix.length <= 34 ? prefix : clean.slice(0, 34).trim()
}

function coachSnapshotToDailyTraining(snapshot, requestedDay) {
  if (!snapshot) return null
  const targetDay = requestedDay || snapshot.seance_du_jour?.date || snapshot.genere_le
  const rawItems = [
    snapshot.seance_du_jour,
    ...(snapshot.projection || []),
  ].filter(item => item?.date && item?.seance)

  const byDate = new Map()
  for (const item of rawItems) {
    if (!byDate.has(item.date)) byDate.set(item.date, item)
  }

  const sessions = []
  for (let offset = 0; offset < 8; offset += 1) {
    const date = addDays(targetDay, offset)
    const item = byDate.get(date)
    if (!item) continue
    sessions.push({
      date,
      dateLabel: formatCoachDateLabel(date),
      relativeLabel: offset === 0 ? "Aujourd'hui" : `J+${offset}`,
      planSource: DAILY_PLAN_SOURCE,
      planDescription: DAILY_PLAN_DESCRIPTION,
      planBasis: DAILY_PLAN_BASIS,
      status: 'scheduled',
      statusLabel: 'A faire',
      title: coachSessionTitle(item.seance),
      session: {
        main: item.seance,
      },
    })
  }

  if (!sessions.length && rawItems.length) {
    rawItems.slice(0, 8).forEach((item, index) => {
      sessions.push({
        date: item.date,
        dateLabel: formatCoachDateLabel(item.date),
        relativeLabel: index === 0 ? "Aujourd'hui" : `J+${index}`,
        planSource: DAILY_PLAN_SOURCE,
        planDescription: DAILY_PLAN_DESCRIPTION,
        planBasis: DAILY_PLAN_BASIS,
        status: 'scheduled',
        statusLabel: 'A faire',
        title: coachSessionTitle(item.seance),
        session: {
          main: item.seance,
        },
      })
    })
  }

  const latestRun = snapshot.derniers_runs?.[0]
  const current = sessions[0]
  if (!current) return null

  return {
    ...current,
    planSource: DAILY_PLAN_SOURCE,
    planDescription: DAILY_PLAN_DESCRIPTION,
    planBasis: DAILY_PLAN_BASIS,
    planPeriod: null,
    dataThrough: latestRun?.date || snapshot.genere_le || '',
    observations: latestRun
      ? `Dernier run analyse: ${latestRun.date}, ${latestRun.distance_km} km a ${latestRun.allure}, FC moy ${latestRun.fc_moy || '-'} bpm.`
      : 'Snapshot coach charge.',
    adjustment: snapshot.regle_ajustement || 'Plan issu du snapshot coach public.',
    sleep: null,
    recentRuns: snapshot.derniers_runs || [],
    sessions,
  }
}

async function loadDailyTrainingFromCoachSnapshot(day) {
  try {
    const snapshot = await loadCoachSnapshot()
    return coachSnapshotToDailyTraining(snapshot, day)
  } catch (snapshotError) {
    console.warn('[API] coach snapshot fallback failed:', snapshotError?.message || snapshotError)
    return null
  }
}

async function loadCoachSnapshot() {
  return fetchAPI('/api/coach/journal', {}, { retries: 2 })
}

/**
 * On-open freshness probe: asks the backend to pull any runs from Garmin
 * that are newer than the latest one in Neon. Returns `{ added, details_fetched, ... }`.
 * Fails soft — returns null on any error, callers should keep rendering.
 */
export async function checkFreshness() {
  console.log('[API] Checking Garmin for newer activities…')
  try {
    const data = await fetchAPI('/api/data/freshness-check', { method: 'POST' })
    console.log(`[API] freshness result: added=${data?.added ?? '?'} skipped=${data?.skipped || 'none'} checked=${data?.checked}`)
    return data
  } catch (e) {
    console.warn('[API] freshness check failed:', e?.message || e)
    return null
  }
}

/**
 * Delete an activity from the Neon DB. Does NOT delete on Garmin.
 */
export async function deleteActivity(activityId) {
  console.log('[API] Deleting activity', activityId, 'from local DB')
  const data = await fetchAPI(`/api/data/activities/${activityId}`, { method: 'DELETE' })
  console.log('[API] Delete result:', data)
  return data
}

// ── Activity Streams (read from server DB, never directly from Garmin) ──

export async function getActivityStreams(activityId) {
  console.log(`[API] Loading streams for activity ${activityId} from DB`)
  const data = await fetchAPI(`/api/data/streams/${activityId}`)
  console.log(`[API] Streams loaded for ${activityId}`)
  return data
}
