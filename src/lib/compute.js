/**
 * Client-side activity data computation.
 * Replaces server-side computation to leverage cached activities.
 */

import { getNow } from './clock'

// --- Helpers ---
/**
 * Parse a Strava start_date_local string, stripping timezone suffixes
 * to avoid UTC→local offset issues (e.g. 23:30 UTC showing as next day in France).
 */
export function parseLocalDate(d) {
  if (!d) return new Date(0)
  if (d instanceof Date) return d
  return new Date(String(d).replace('Z', '').replace('+00:00', ''))
}

function parseDate(d) {
  return parseLocalDate(d)
}

/**
 * Format a Date as YYYY-MM-DD using LOCAL time (not UTC).
 * IMPORTANT: Never use date.toISOString().slice(0,10) as it converts to UTC
 * and can shift dates by ±1 day depending on timezone.
 */
export function localDateStr(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/**
 * Get the Monday of the ISO week containing the given date.
 * Monday-based weeks: Sunday(0) → previous Monday, otherwise subtract (day-1).
 * Returns a new Date set to Monday 00:00:00.
 */
export function getMonday(date) {
  const d = new Date(date)
  const day = d.getDay()
  d.setDate(d.getDate() - (day === 0 ? 6 : day - 1))
  d.setHours(0, 0, 0, 0)
  return d
}

export function fmtTime(seconds) {
  if (!seconds) return '-'
  seconds = Math.round(seconds)
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return h > 0 ? `${h}h${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${m}:${String(s).padStart(2,'0')}`
}

export function fmtPace(seconds) {
  if (!seconds) return '-'
  // Arrondir le total AVANT de séparer min/sec, sinon 299.7s donne "4:60" au lieu de "5:00"
  const total = Math.round(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2,'0')}`
}

// Target distances in meters for each race type
const TARGET_DIST = { '5k': 5000, '10k': 10000, 'semi': 21097.5, 'marathon': 42195 }
const DISTANCE_LABELS = { '5k': '5 km', '10k': '10 km', semi: 'Semi-marathon', marathon: 'Marathon' }
const RIEGEL_TARGETS = ['10k', 'semi', 'marathon']
// Source unique pour toutes les projections : le meilleur effort RÉCENT
// (fenêtre 90 j), en préférant les distances longues — un vieux record de
// 5 km ne reflète plus la forme du moment (ex: projection 10 km plus lente
// que le 10 km réellement couru).
const RIEGEL_SOURCE_ORDER = ['semi', '10k', 'marathon', '5k']
const RIEGEL_RECENT_WINDOW_DAYS = 90

// Effort names mapped to our distance types
const EFFORT_NAME_MAP = {
  '5k': '5k',
  '10k': '10k',
  'Half-Marathon': 'semi',
  'Marathon': 'marathon',
}

// A run is eligible for a distance record if it's at least the target distance
function isEligible(distM, type) {
  const target = TARGET_DIST[type]
  return target && distM >= target
}

// Get the best effort time from stored efforts for a given activity
// Returns the moving_time for the matching effort, or null if not found
function getBestEffortTime(activityId, type, bestEffortsMap) {
  if (!bestEffortsMap) return null
  const entry = bestEffortsMap[String(activityId)]
  if (!entry?.efforts) return null
  // Find the effort matching our type
  for (const e of entry.efforts) {
    if (EFFORT_NAME_MAP[e.name] === type) {
      return e.moving_time || e.elapsed_time
    }
  }
  return null
}

// Estimate time for a target distance based on average pace (fallback)
function estimateTime(movingTime, actualDist, type) {
  const target = TARGET_DIST[type]
  if (!target || !actualDist || !movingTime) return movingTime
  if (actualDist <= target * 1.02) return movingTime // within 2% → use actual time
  return Math.round(movingTime * target / actualDist)
}

export function riegel(t1, d1, d2) {
  return t1 * Math.pow(d2 / d1, 1.06)
}

export function paceForDist(timeS, distType) {
  const dists = { '5k': 5, '10k': 10, 'semi': 21.0975, 'marathon': 42.195 }
  const d = dists[distType]
  if (!d || !timeS) return ''
  const p = Math.round(timeS / d)
  return `${Math.floor(p / 60)}:${String(p % 60).padStart(2,'0')}/km`
}

/** Pace string ("M:SS") from a speed in m/s. */
export function fmtPaceFromSpeed(speedMs) {
  if (!speedMs || speedMs <= 0) return '-'
  return fmtPace(1000 / speedMs)
}

export function fmtSpeedKmh(speedMs, digits = 2) {
  if (!speedMs || speedMs <= 0) return '-'
  return new Intl.NumberFormat('fr-FR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(speedMs * 3.6)
}

export function fmtOrdinalFr(rank) {
  if (!rank || rank < 1) return '-'
  return rank === 1 ? '1er' : `${rank}e`
}

function distanceBucketKm(distanceM) {
  return Math.max(0, Math.floor((distanceM || 0) / 1000))
}

function runId(a) {
  return String(a?.id ?? '')
}

function rankSortedRuns(runs, valueFn) {
  return [...runs].sort((a, b) => {
    const delta = valueFn(b) - valueFn(a)
    if (delta !== 0) return delta
    return String(b.start_date_local || '').localeCompare(String(a.start_date_local || ''))
  })
}

// Index du premier élément strictement supérieur à value (tableau croissant).
function upperBoundAsc(sorted, value) {
  let lo = 0
  let hi = sorted.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (sorted[mid] <= value) lo = mid + 1
    else hi = mid
  }
  return lo
}

function insertSortedAsc(sorted, value) {
  sorted.splice(upperBoundAsc(sorted, value), 0, value)
}

export function computeRunRankingIndex(activities) {
  const valid = (activities || []).filter(a => a?.id != null)
  const byDistance = rankSortedRuns(
    valid.filter(a => (a.distance || 0) > 0),
    a => a.distance || 0
  )
  const distRank = {}
  byDistance.forEach((a, i) => { distRank[runId(a)] = i + 1 })

  // Nième la plus rapide : la vitesse de chaque run est comparée à TOUS les
  // runs de distance supérieure ou égale à la sienne (et non par tranche de
  // 1 km). Les runs de distance égale font partie du scope les uns des autres.
  const paceable = valid.filter(a => (a.average_speed || 0) > 0 && (a.distance || 0) >= 500)
  const byDistanceDesc = [...paceable].sort((a, b) => (b.distance || 0) - (a.distance || 0))
  const paceRank = {}
  const paceScope = {}
  const seenSpeeds = []
  let i = 0
  while (i < byDistanceDesc.length) {
    const groupDistance = byDistanceDesc[i].distance || 0
    let j = i
    while (j < byDistanceDesc.length && (byDistanceDesc[j].distance || 0) === groupDistance) j++
    for (let k = i; k < j; k++) insertSortedAsc(seenSpeeds, byDistanceDesc[k].average_speed || 0)
    for (let k = i; k < j; k++) {
      const a = byDistanceDesc[k]
      const fasterCount = seenSpeeds.length - upperBoundAsc(seenSpeeds, a.average_speed || 0)
      paceRank[runId(a)] = fasterCount + 1
      paceScope[runId(a)] = seenSpeeds.length
    }
    i = j
  }

  return { distRank, paceRank, paceScope, total: valid.length }
}

function buildDistanceDistribution(validRuns, currentRun) {
  const currentBucket = distanceBucketKm(currentRun.distance)
  const counts = new Map()
  validRuns.forEach(a => {
    const bucket = distanceBucketKm(a.distance)
    counts.set(bucket, (counts.get(bucket) || 0) + 1)
  })
  if (!counts.has(currentBucket)) counts.set(currentBucket, 0)
  // Tri croissant : les grandes distances à droite du graphique.
  return [...counts.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([bucket, count]) => ({
      key: `distance-${bucket}`,
      label: String(bucket),
      count,
      isCurrent: bucket === currentBucket,
      tooltip: `${bucket}-${bucket + 1} km`,
    }))
}

// Ladder de pas de classe en s/km : on privilégie une résolution FINE pour
// regrouper le moins possible les runs entre eux. Le plus petit pas « rond »
// qui garde un nombre de classes lisible est retenu.
const PACE_BIN_STEPS = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30]
const PACE_BIN_MAX = 44

function buildSpeedDistribution(similarRuns, currentRun) {
  // On raisonne en allure (s/km) et non en vitesse (m/s) : des classes
  // d'allure régulières sont l'unité lisible d'un coureur, et un pas linéaire
  // en vitesse écrase artificiellement les allures lentes.
  const paces = similarRuns
    .map(a => (a.average_speed > 0 ? 1000 / a.average_speed : 0))
    .filter(Boolean)
  const currentSpeed = currentRun.average_speed || 0
  const currentPace = currentSpeed > 0 ? 1000 / currentSpeed : 0
  if (!paces.length || !currentPace) return []

  const minPace = Math.min(...paces)
  const maxPace = Math.max(...paces)
  if (minPace === maxPace) {
    return [{
      key: 'pace-only',
      label: fmtPace(minPace),
      count: paces.length,
      isCurrent: true,
      tooltip: `${fmtPace(minPace)}/km`,
    }]
  }

  const span = maxPace - minPace
  const step = PACE_BIN_STEPS.find(s => span / s <= PACE_BIN_MAX)
    || PACE_BIN_STEPS[PACE_BIN_STEPS.length - 1]

  // Bornes alignées sur le pas pour des tranches nettes (4:35, 4:40, 4:45…).
  const lowEdge = Math.floor(minPace / step) * step
  const highEdge = Math.ceil(maxPace / step) * step
  const binCount = Math.max(1, Math.round((highEdge - lowEdge) / step))

  const bins = Array.from({ length: binCount }, (_, i) => {
    const lower = lowEdge + i * step // borne rapide (allure basse) de la tranche
    const upper = lower + step
    return {
      key: `pace-${i}`,
      lower,
      upper,
      count: 0,
      isCurrent: false,
      label: fmtPace(lower),
      tooltip: `${fmtPace(lower)}-${fmtPace(upper)}/km`,
    }
  })

  const binIndex = pace =>
    Math.max(0, Math.min(binCount - 1, Math.floor((pace - lowEdge) / step)))

  paces.forEach(pace => { bins[binIndex(pace)].count += 1 })
  bins[binIndex(currentPace)].isCurrent = true

  // Affichage du plus lent (gauche) au plus rapide (droite) : allure
  // décroissante, donc classes triées par borne d'allure décroissante.
  return bins.reverse()
}

// « 2026-06-28T11:07:11 » -> « 28/06/26 » pour les détails compacts des cartes.
function fmtDateShortFr(isoDate) {
  const d = String(isoDate || '').slice(0, 10)
  if (d.length !== 10) return ''
  return `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(2, 4)}`
}

export function buildRunRankingInsights(activity, activities) {
  if (!activity?.id) return null
  const valid = (activities || []).filter(a => a?.id != null)
  if (!valid.length) return null

  const rankingIndex = computeRunRankingIndex(valid)
  const id = runId(activity)
  const minDistance = activity.distance || 0
  // Scope « Nième la plus rapide » : tous les runs au moins aussi longs.
  const similarRuns = valid.filter(a =>
    (a.distance || 0) >= minDistance &&
    (a.average_speed || 0) > 0 &&
    (a.distance || 0) >= 500
  )

  // Détails de la carte allure : percentile, médiane du scope, record du
  // scope et prochain run à battre (avec écart en s/km vs ce run).
  const currentSpeed = activity.average_speed || 0
  const currentPaceS = currentSpeed > 0 ? 1000 / currentSpeed : null
  const gapLabel = other => {
    if (!currentPaceS || !(other?.average_speed > 0)) return ''
    const gap = Math.round(currentPaceS - 1000 / other.average_speed)
    return gap > 0 ? `à ${gap} s/km` : 'même allure'
  }
  const runRef = run => run ? {
    paceLabel: `${fmtPaceFromSpeed(run.average_speed)}/km`,
    dateLabel: fmtDateShortFr(run.start_date_local),
    distanceLabel: `${((run.distance || 0) / 1000).toFixed(2)} km`,
    gapLabel: gapLabel(run),
  } : null
  const speedsAsc = similarRuns.map(a => a.average_speed).sort((a, b) => a - b)
  const medianSpeed = speedsAsc.length ? speedsAsc[Math.floor(speedsAsc.length / 2)] : null
  const bestRun = similarRuns.reduce((best, a) => (!best || a.average_speed > best.average_speed ? a : best), null)
  const nextFasterRun = similarRuns
    .filter(a => a.average_speed > currentSpeed && runId(a) !== id)
    .reduce((next, a) => (!next || a.average_speed < next.average_speed ? a : next), null)

  const paceRank = rankingIndex.paceRank[id] || null
  const paceTotal = rankingIndex.paceScope[id] || similarRuns.length
  console.log('[RunRanking] run', id, `${(minDistance / 1000).toFixed(2)} km`,
    '· scope vitesse:', similarRuns.length, 'runs >= distance · rang', paceRank,
    '· record scope:', bestRun?.average_speed, '· prochain à battre:', nextFasterRun?.average_speed)

  return {
    total: rankingIndex.total,
    distance: {
      rank: rankingIndex.distRank[id] || null,
      total: rankingIndex.total,
      valueLabel: `${(activity.distance / 1000).toFixed(2)} km`,
      data: buildDistanceDistribution(valid, activity),
    },
    pace: {
      rank: paceRank,
      total: paceTotal,
      minDistanceLabel: `${(minDistance / 1000).toFixed(2)} km`,
      valueLabel: `${fmtPaceFromSpeed(activity.average_speed)}/km`,
      data: buildSpeedDistribution(similarRuns, activity),
      percentLabel: paceRank && paceTotal
        ? `top ${Math.max(1, Math.round((paceRank / paceTotal) * 100))} %`
        : '',
      medianLabel: medianSpeed ? `${fmtPaceFromSpeed(medianSpeed)}/km` : '',
      isRecord: paceRank === 1,
      best: runRef(bestRun),
      nextFaster: runRef(nextFasterRun),
    },
  }
}

function tempEmoji(tempC) {
  if (tempC == null) return '🌡️'
  if (tempC >= 28) return '☀️'
  if (tempC >= 20) return '🌤️'
  if (tempC >= 12) return '⛅'
  if (tempC >= 4) return '🌥️'
  return '❄️'
}

function tempLabel(tempC) {
  if (tempC == null) return 'Météo'
  if (tempC >= 28) return 'Chaud'
  if (tempC >= 20) return 'Doux'
  if (tempC >= 12) return 'Frais'
  if (tempC >= 4) return 'Froid'
  return 'Très froid'
}

export function buildRunWeatherSummary(activity) {
  if (!activity) return null
  const temps = [
    activity.average_temp,
    activity.min_temperature,
    activity.max_temperature,
  ].filter(v => v != null && Number.isFinite(Number(v))).map(Number)

  if (!temps.length) return null
  const temp = activity.average_temp != null
    ? Number(activity.average_temp)
    : temps.reduce((sum, value) => sum + value, 0) / temps.length
  const min = activity.min_temperature != null ? Number(activity.min_temperature) : null
  const max = activity.max_temperature != null ? Number(activity.max_temperature) : null
  const roundedTemp = Math.round(temp)
  const rangeLabel = min != null && max != null && Math.round(min) !== Math.round(max)
    ? `${Math.round(min)}-${Math.round(max)} °C`
    : ''

  return {
    emoji: tempEmoji(temp),
    label: tempLabel(temp),
    temperature: temp,
    temperatureLabel: `${roundedTemp} °C`,
    rangeLabel,
    source: 'Activité',
  }
}

/**
 * Stats secondaires d'un run — colonnes déjà renvoyées par
 * /api/data/activities mais historiquement jamais affichées (FC max,
 * allure max, cadence, calories, altitude min-max, température).
 * Ne retourne que les stats réellement renseignées pour ce run.
 * Partagé entre RunModal et ActivityDetail.
 */
export function buildRunExtraStats(a, { maxSpeed } = {}) {
  if (!a) return []
  const stats = []
  if (a.max_heartrate > 0) stats.push({ label: 'FC max', value: Math.round(a.max_heartrate), unit: 'bpm' })
  // Allure max : le max_speed du résumé Garmin est un pic instantané (~1 s),
  // souvent aberrant (ex: 3:03/km sur un footing, glitch GPS). Quand les
  // streams sont chargés, l'appelant passe le pic de la courbe de vitesse
  // lissée (maxSpeed) — cohérent avec le graphe affiché.
  const bestMaxSpeed = maxSpeed > 0 ? maxSpeed : a.max_speed
  if (bestMaxSpeed > 0) stats.push({ label: 'Allure max', value: fmtPaceFromSpeed(bestMaxSpeed), unit: '/km' })
  // La base stocke la cadence par jambe (convention Strava) ; on affiche la
  // cadence totale en pas/min, la métrique usuelle en course à pied.
  if (a.average_cadence > 0) stats.push({ label: 'Cadence', value: Math.round(a.average_cadence * 2), unit: 'pas/min' })
  if (a.calories > 0) stats.push({ label: 'Calories', value: Math.round(a.calories), unit: 'kcal' })
  if (a.elev_high || a.elev_low) stats.push({ label: 'Altitude', value: `${Math.round(a.elev_low || 0)}–${Math.round(a.elev_high || 0)}`, unit: 'm' })
  if (a.average_temp != null) stats.push({ label: 'Temp. moy.', value: Math.round(a.average_temp), unit: '°C' })
  return stats
}

// Sort records by time (best first) and annotate isBest / pctOffBest.
function rankRecords(records) {
  records.sort((a, b) => a.time - b.time)
  if (records.length) {
    const bestTime = records[0].time
    records.forEach(m => {
      m.isBest = m.time === bestTime
      m.pctOffBest = bestTime > 0 ? Math.round(((m.time - bestTime) / bestTime) * 1000) / 10 : 0
    })
  }
  return records
}

function bestRecord(prs, distType) {
  return prs?.[distType]?.length ? prs[distType][0] : null
}

// Fastest record within the recency window. prs lists are ranked best-first
// (rankRecords), so the first record passing the date filter is the fastest
// recent one.
function bestRecentRecord(prs, distType, cutoff) {
  const records = prs?.[distType] || []
  return records.find(r => r?.time > 0 && r?.date && parseDate(r.date) >= cutoff) || null
}

function preferredRiegelSource(prs) {
  const cutoff = new Date(getNow() - RIEGEL_RECENT_WINDOW_DAYS * 86400000)
  for (const source of RIEGEL_SOURCE_ORDER) {
    const record = bestRecentRecord(prs, source, cutoff)
    if (record && TARGET_DIST[source]) return { source, record, recent: true }
  }
  // Aucun effort dans la fenêtre (coupure, blessure…) : retomber sur le
  // meilleur all-time plutôt que de faire disparaître les projections.
  for (const source of RIEGEL_SOURCE_ORDER) {
    const record = bestRecord(prs, source)
    if (record?.time > 0 && TARGET_DIST[source]) return { source, record, recent: false }
  }
  return null
}

// Riegel projections for the race distances that matter for marathon planning.
function buildRiegelProjections(prs, { withSourceDate = false } = {}) {
  const projections = {}
  const picked = preferredRiegelSource(prs)
  if (!picked) return projections
  const { source, record: bestRec, recent } = picked
  console.log(`[Riegel] source=${source} time=${bestRec.time}s date=${bestRec.date} recent=${recent} (fenêtre ${RIEGEL_RECENT_WINDOW_DAYS}j)`)
  RIEGEL_TARGETS.forEach(target => {
    const seconds = Math.round(riegel(bestRec.time, TARGET_DIST[source], TARGET_DIST[target]))
    const actual = bestRecord(prs, target)
    projections[target] = {
      target_distance: target,
      target_label: DISTANCE_LABELS[target],
      seconds,
      formatted: fmtTime(seconds),
      pace: paceForDist(seconds, target),
      source_time: fmtTime(bestRec.time),
      source_distance: source,
      source_label: DISTANCE_LABELS[source],
      source_activity_id: bestRec.activity_id,
      ...(withSourceDate ? { source_date: bestRec.date?.slice(0, 10) || '' } : {}),
      ...(actual ? {
        actual_seconds: actual.time,
        actual_formatted: fmtTime(actual.time),
        delta_seconds: seconds - actual.time,
      } : {}),
    }
  })
  return projections
}

function buildRiegelProjectionTimeline(prs) {
  const events = []
  Object.entries(prs || {}).forEach(([distType, records]) => {
    if (!TARGET_DIST[distType]) return
    const recordList = records || []
    recordList.forEach(record => {
      if (record?.date && record?.time > 0) events.push({ distType, date: record.date, time: record.time })
    })
  })
  events.sort((a, b) => String(a.date).localeCompare(String(b.date)))

  const runningBest = {}
  const timeline = {}
  events.forEach(event => {
    if (!runningBest[event.distType] || event.time < runningBest[event.distType].time) {
      runningBest[event.distType] = event
    }
    const date = event.date.slice(0, 10)
    if (!timeline[date]) timeline[date] = {}

    const source = RIEGEL_SOURCE_ORDER.find(sourceKey => runningBest[sourceKey]?.time > 0)
    if (!source) return
    RIEGEL_TARGETS.forEach(target => {
      timeline[date][target] = Math.round(riegel(
        runningBest[source].time,
        TARGET_DIST[source],
        TARGET_DIST[target]
      ))
    })
  })

  return Object.entries(timeline)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, values]) => ({ date, ...values }))
}

// --- Cockpit ---
export function computeCockpit(activities, externalPRs = null) {
  const now = new Date(getNow())
  const weekStart = getMonday(now)

  const d90 = new Date(now - 90 * 86400000)
  const d28 = new Date(now - 28 * 86400000)
  const d180 = new Date(now - 180 * 86400000)

  const d7 = new Date(now - 7 * 86400000)
  const d365 = new Date(now - 365 * 86400000)

  let weekVol = 0, vol7 = 0, vol90 = 0, vol365 = 0, vol28 = 0, prev90 = 0
  activities.forEach(a => {
    const dt = parseDate(a.start_date_local)
    const dist = a.distance || 0
    if (dt >= weekStart) weekVol += dist
    if (dt >= d7) vol7 += dist
    if (dt >= d90) vol90 += dist
    if (dt >= d365) vol365 += dist
    if (dt >= d28) vol28 += dist
    if (dt >= d180 && dt < d90) prev90 += dist
  })

  const avg4w = vol28 / 4
  const alerts = []
  if (avg4w > 0 && weekVol > avg4w * 1.2) {
    alerts.push({ type: 'warning', message: `Volume semaine +${Math.round((weekVol / avg4w - 1) * 100)}% vs moyenne 4 sem.` })
  }
  if (prev90 > 0 && vol90 < prev90 * 0.85) {
    alerts.push({ type: 'danger', message: `Volume 90j en baisse de ${Math.round((1 - vol90 / prev90) * 100)}%` })
  }

  // Use externally computed PRs (from Neon/splits) if available, otherwise fallback to estimate
  const prs = externalPRs && Object.keys(externalPRs).length > 0
    ? _convertExternalPRs(externalPRs)
    : computePRs(activities)
  const pr90d = Object.values(prs).reduce((sum, dist) => {
    return sum + dist.filter(p => p.isBest && parseDate(p.date) >= d90).length
  }, 0)

  const projections = buildRiegelProjections(prs, { withSourceDate: true })

  // Recent runs for mini-map on cockpit
  const recentRuns = activities
    .filter(a => a.summary_polyline)
    .slice(0, 10)
    .map(a => ({
      id: a.id,
      name: a.name,
      date: a.start_date_local,
      distance: a.distance,
      polyline: a.summary_polyline,
      start_latlng: a.start_latlng,
    }))

  return {
    week_volume: Math.round(weekVol / 10) / 100,
    volume_7d: Math.round(vol7 / 10) / 100,
    volume_90d: Math.round(vol90 / 10) / 100,
    volume_365d: Math.round(vol365 / 10) / 100,
    avg_4_weeks: Math.round(avg4w / 10) / 100,
    pr_90d: pr90d,
    projections,
    alerts,
    total_activities: activities.length,
    recent_runs: recentRuns,
  }
}

// Convert external PRs (from Neon getComputedBests) to the same format as computePRs output
function _convertExternalPRs(externalPRs) {
  const prs = {}
  for (const [distType, bests] of Object.entries(externalPRs)) {
    if (!bests?.length) { prs[distType] = []; continue }
    const records = bests.map(b => ({
      date: b.startDate,
      time: Math.round(b.timeSeconds),
      actual_time: b.movingTime,
      activity_id: b.activityId,
      distance: b.distance,
      formatted: fmtTime(Math.round(b.timeSeconds)),
      pace: paceForDist(b.timeSeconds, distType),
      polyline: b.polyline,
      source: b.source || 'splits',
    }))
    prs[distType] = rankRecords(records)
  }
  return prs
}

// --- PRs ---
export function computePRs(activities, bestEffortsMap = null) {
  const prs = {}
  const types = ['5k', '10k', 'semi', 'marathon']

  types.forEach(distType => {
    const matching = activities
      .filter(a => isEligible(a.distance || 0, distType))
      .map(a => {
        // Use stored best_efforts if available, fall back to pace estimation
        const beTime = getBestEffortTime(a.id, distType, bestEffortsMap)
        const estTime = beTime || estimateTime(a.moving_time, a.distance, distType)
        return {
          date: a.start_date_local,
          time: estTime,
          actual_time: a.moving_time,
          activity_id: a.id,
          distance: a.distance,
          formatted: fmtTime(estTime),
          pace: paceForDist(estTime, distType),
          polyline: a.summary_polyline,
          source: beTime ? 'best_efforts' : 'estimate',
        }
      })
    rankRecords(matching)
    const effortCount = matching.filter(m => m.source === 'best_efforts').length
    console.log(`[PR] ${distType}: ${matching.length} eligible (${effortCount} with best_efforts, ${matching.length - effortCount} estimated)`)
    prs[distType] = matching
  })

  return prs
}

// --- Volume ---
export function computeMonthly(activities) {
  const buckets = {}
  activities.forEach(a => {
    const dt = parseDate(a.start_date_local)
    const key = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}`
    if (!buckets[key]) buckets[key] = { year: String(dt.getFullYear()), month: String(dt.getMonth() + 1).padStart(2, '0'), km: 0, runs: 0, time_s: 0 }
    buckets[key].km += a.distance / 1000
    buckets[key].runs++
    buckets[key].time_s += a.moving_time || 0
  })
  return Object.values(buckets).sort((a, b) => `${a.year}-${a.month}`.localeCompare(`${b.year}-${b.month}`)).map(r => ({ ...r, km: Math.round(r.km * 100) / 100 }))
}

export function computeYearly(activities) {
  const buckets = {}
  activities.forEach(a => {
    const dt = parseDate(a.start_date_local)
    const yr = String(dt.getFullYear())
    if (!buckets[yr]) buckets[yr] = { year: yr, km: 0, runs: 0, time_s: 0, elev: 0 }
    buckets[yr].km += a.distance / 1000
    buckets[yr].runs++
    buckets[yr].time_s += a.moving_time || 0
    buckets[yr].elev += a.total_elevation_gain || 0
  })
  return Object.values(buckets).sort((a, b) => a.year.localeCompare(b.year)).map(r => ({ ...r, km: Math.round(r.km * 100) / 100, elev: Math.round(r.elev * 10) / 10 }))
}

export function computeRolling(activities, days = 90) {
  const now = new Date(getNow())
  const todayKey = localDateStr(now)
  const start = new Date(now)
  start.setDate(start.getDate() - days * 2)
  start.setHours(0, 0, 0, 0)
  // Populate daily buckets with ALL activities — activities older than `start`
  // are needed so that rolling windows at the left edge of the chart include
  // their real lookback, otherwise the first `days` points would be 0.
  const daily = {}
  activities.forEach(a => {
    const dt = parseDate(a.start_date_local)
    const d = localDateStr(dt)
    daily[d] = (daily[d] || 0) + a.distance / 1000
  })
  // Prefix sums over the sorted run-days: each window total is then two
  // binary searches instead of a re-scan of every run-day per output point.
  const keys = Object.keys(daily).sort()
  const prefix = new Array(keys.length + 1)
  prefix[0] = 0
  keys.forEach((k, i) => { prefix[i + 1] = prefix[i] + daily[k] })
  const lowerBound = target => { // first index with keys[i] >= target
    let lo = 0, hi = keys.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (keys[mid] < target) lo = mid + 1
      else hi = mid
    }
    return lo
  }
  const upperBound = target => { // first index with keys[i] > target
    let lo = 0, hi = keys.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (keys[mid] <= target) lo = mid + 1
      else hi = mid
    }
    return lo
  }
  const result = []
  const d = new Date(start)
  while (localDateStr(d) <= todayKey) {
    const ds = localDateStr(d)
    const windowStart = new Date(d)
    windowStart.setDate(windowStart.getDate() - days)
    const ws = localDateStr(windowStart)
    const total = prefix[upperBound(ds)] - prefix[lowerBound(ws)]
    result.push({ date: ds, km: Math.round(total * 100) / 100 })
    d.setDate(d.getDate() + 1)
  }
  console.log('[ROLLING] days=', days, 'first=', result[0]?.date, '→', result[0]?.km, 'last=', result[result.length - 1]?.date, '→', result[result.length - 1]?.km, 'len=', result.length)
  return result
}

// --- Performance ---
export function computeBestByYear(prs) {
  const result = {}
  Object.entries(prs).forEach(([distType, records]) => {
    const byYear = {}
    records.forEach(r => {
      const yr = r.date.slice(0, 4)
      if (!byYear[yr] || r.time < byYear[yr].time) byYear[yr] = r
    })
    result[distType] = Object.entries(byYear)
      .map(([yr, v]) => ({ year: yr, time: v.time, formatted: v.formatted, pace: v.pace }))
      .sort((a, b) => a.year.localeCompare(b.year))
  })
  return result
}

export function computeProjections(prs, activities) {
  const projections = buildRiegelProjections(prs, { withSourceDate: true })
  const timeline = buildRiegelProjectionTimeline(prs)

  const now = new Date(getNow())
  const d90 = new Date(now - 90 * 86400000)
  const vol90 = activities.reduce((s, a) => parseDate(a.start_date_local) >= d90 ? s + a.distance : s, 0) / 1000

  return {
    current: projections,
    timeline,
    confidence: vol90 > 300 ? 'high' : vol90 > 150 ? 'medium' : 'low',
    volume_90d_km: Math.round(vol90 * 10) / 10,
  }
}

// --- Analysis ---
export function computePaceStability(activities) {
  return activities
    .filter(a => (a.distance || 0) > 3000)
    .sort((a, b) => a.start_date_local.localeCompare(b.start_date_local))
    .slice(-100)
    .map(a => {
      const pace = a.moving_time / (a.distance / 1000)
      return {
        id: a.id,
        date: a.start_date_local,
        name: a.name || '',
        distance_km: Math.round(a.distance / 10) / 100,
        pace_s_km: Math.round(pace * 10) / 10,
        pace_formatted: fmtPace(pace),
        heartrate: a.average_heartrate,
      }
    })
}

export function computeCardiacDecoupling(activities) {
  return activities
    .filter(a => a.average_heartrate && (a.distance || 0) > 5000)
    .sort((a, b) => a.start_date_local.localeCompare(b.start_date_local))
    .slice(-200)
    .map(a => {
      const pace = a.moving_time / (a.distance / 1000)
      const speedKmh = (a.average_speed || 0) * 3.6
      const eff = a.average_heartrate ? speedKmh / a.average_heartrate : null
      return {
        id: a.id,
        date: a.start_date_local,
        name: a.name || '',
        distance_km: Math.round(a.distance / 10) / 100,
        pace_s_km: Math.round(pace * 10) / 10,
        avg_hr: a.average_heartrate,
        max_hr: a.max_heartrate,
        efficiency: eff ? Math.round(eff * 10000) / 10000 : null,
      }
    })
}

export function computeVolumeVsPerformance(activities) {
  const runs10k = activities
    .filter(a => isEligible(a.distance || 0, '10k'))
    .sort((a, b) => a.start_date_local.localeCompare(b.start_date_local))

  return runs10k.map(r => {
    const d = r.start_date_local.slice(0, 10)
    const dt = new Date(d)
    const d30 = localDateStr(new Date(dt - 30 * 86400000))
    const vol = activities.reduce((s, a) => {
      const ad = a.start_date_local.slice(0, 10)
      return ad >= d30 && ad <= d ? s + a.distance : s
    }, 0) / 1000
    const estTime = estimateTime(r.moving_time, r.distance, '10k')
    return {
      id: r.id,
      date: d,
      name: r.name || '',
      time_10k: estTime,
      formatted: fmtTime(estTime),
      volume_30d_km: Math.round(vol * 10) / 10,
    }
  })
}

// --- Gear Usage ---
const RETIREMENT_KM = 1000

/**
 * Normalize a gear ID for consistent matching.
 * IDs may or may not include a prefix letter ('g' for shoes, 'b' for bikes).
 * We strip the prefix to get a canonical key.
 */
export function normalizeGearId(id) {
  if (!id) return ''
  const s = String(id)
  return s.startsWith('g') || s.startsWith('b') ? s.slice(1) : s
}

export function computeGearUsage(activities, shoes = [], gearDetails = []) {
  // Build shoe map keyed by normalized ID for reliable matching
  const shoeMap = {}
  // First load gear details from DB (lower priority)
  gearDetails.forEach(g => {
    if (!g.id) return
    shoeMap[normalizeGearId(g.id)] = g
    shoeMap[String(g.id)] = g
  })
  // Then overlay shoes from athlete profile (higher priority)
  shoes.forEach(s => {
    if (!s.id) return
    shoeMap[normalizeGearId(s.id)] = s
    shoeMap[String(s.id)] = s // also keep raw ID
  })

  // Group by gear_id
  const byGear = {}
  activities.forEach(a => {
    const gid = a.gear_id || '__none__'
    if (!byGear[gid]) byGear[gid] = []
    byGear[gid].push(a)
  })

  // Compute per-shoe stats
  const shoeStats = Object.entries(byGear)
    .filter(([gid]) => gid !== '__none__')
    .map(([gid, acts]) => {
      const normId = normalizeGearId(gid)
      const info = shoeMap[normId] || shoeMap[gid] || {}
      const gearNameFromActs = acts.find(a => a.gear_name)?.gear_name
      const totalKm = acts.reduce((s, a) => s + (a.distance || 0) / 1000, 0)
      const totalTime = acts.reduce((s, a) => s + (a.moving_time || 0), 0)
      const avgPace = totalKm > 0 ? totalTime / totalKm : 0
      const sorted = [...acts].sort((a, b) => b.start_date_local.localeCompare(a.start_date_local))
      // Use shoe name from shoes array, or gear_name from ANY activity in the group, or raw ID as last resort
      const resolvedName = info.name || gearNameFromActs || gid
      return {
        id: gid,
        name: resolvedName,
        nickname: info.nickname || '',
        model_name: info.model_name || '',
        brand_name: info.brand_name || '',
        primary: info.primary || false,
        retired: info.retired || false,
        total_km: Math.round(totalKm * 100) / 100,
        total_runs: acts.length,
        total_time_s: totalTime,
        avg_pace_s: Math.round(avgPace * 10) / 10,
        last_used: sorted[0]?.start_date_local || '',
        pct: Math.min(totalKm / RETIREMENT_KM, 1),
      }
    })
    .sort((a, b) => b.last_used.localeCompare(a.last_used))

  // Build a resolved name map from shoeStats (already resolved with gear_name fallback)
  const resolvedNameMap = {}
  shoeStats.forEach(s => { resolvedNameMap[s.id] = s.name })

  // Active shoe IDs (non-retired, with activities)
  const activeIds = shoeStats.filter(s => !s.retired).map(s => s.id)

  // Cumulative usage over time
  const cumTotals = {}
  activeIds.forEach(id => { cumTotals[id] = 0 })

  const allSorted = activities
    .filter(a => a.gear_id && activeIds.includes(a.gear_id))
    .sort((a, b) => a.start_date_local.localeCompare(b.start_date_local))

  const cumByDate = {}
  allSorted.forEach(a => {
    const d = a.start_date_local.slice(0, 10)
    cumTotals[a.gear_id] = (cumTotals[a.gear_id] || 0) + (a.distance || 0) / 1000
    cumByDate[d] = { date: d }
    activeIds.forEach(id => {
      cumByDate[d][resolvedNameMap[id] || id] = Math.round((cumTotals[id] || 0) * 10) / 10
    })
  })
  const cumulative = Object.values(cumByDate)

  // Monthly breakdown
  const monthBuckets = {}
  activities.filter(a => a.gear_id && activeIds.includes(a.gear_id)).forEach(a => {
    const dt = parseDate(a.start_date_local)
    const m = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}`
    if (!monthBuckets[m]) monthBuckets[m] = { month: m }
    const sName = resolvedNameMap[a.gear_id] || a.gear_id
    monthBuckets[m][sName] = Math.round(((monthBuckets[m][sName] || 0) + (a.distance || 0) / 1000) * 10) / 10
  })
  const monthly = Object.values(monthBuckets).sort((a, b) => a.month.localeCompare(b.month))

  const shoeNamesList = activeIds.map(id => resolvedNameMap[id] || id)

  return { shoes: shoeStats, cumulative, monthly, shoeNames: shoeNamesList, retirementKm: RETIREMENT_KM }
}


export function shoeDisplayName(shoe) {
  const clean = value => String(value || '').trim()
  const nickname = clean(shoe.nickname)
  const name = clean(shoe.name)
  const rawModelName = clean(shoe.model_name)
  const rawBrandName = clean(shoe.brand_name)
  const modelName = /^unknown shoes?$/i.test(rawModelName) ? '' : rawModelName
  const brandName = /^(other|unknown)$/i.test(rawBrandName) ? '' : rawBrandName
  const fullModel = [brandName, modelName].filter(Boolean).join(' ')

  if (nickname && fullModel && nickname !== fullModel) return { primary: nickname, secondary: fullModel }
  if (nickname) return { primary: nickname, secondary: '' }
  if (name && fullModel && name !== fullModel) return { primary: name, secondary: fullModel }
  if (name) return { primary: name, secondary: '' }
  if (fullModel) return { primary: fullModel, secondary: '' }
  return { primary: 'Chaussure sans nom', secondary: '' }
}
