/**
 * Heart Rate Zones computation module.
 * Always uses custom zone percentages based on FC max.
 * Provider-supplied zones are intentionally ignored.
 */

import { parseLocalDate as parseLocal, localDateStr, getMonday } from './compute'
import { getNow } from './clock'

const FCMAX_OVERRIDE_KEY = 'garmin_fcmax_override'
export const OBSERVED_MAX_HR_WINDOW_DAYS = 90
export const PERSONAL_MAX_HR_FALLBACK = 181
const MIN_PLAUSIBLE_OBSERVED_HR = 30
const MAX_PLAUSIBLE_OBSERVED_HR = 250

function observedHeartRate(value) {
  const hr = Number(value)
  return Number.isFinite(hr) && hr >= MIN_PLAUSIBLE_OBSERVED_HR && hr <= MAX_PLAUSIBLE_OBSERVED_HR
    ? hr
    : null
}

function localDayNumber(value) {
  const parsed = parseLocal(value)
  if (Number.isNaN(parsed.getTime())) return null
  return Math.floor(Date.UTC(parsed.getFullYear(), parsed.getMonth(), parsed.getDate()) / 86400000)
}

/**
 * Get the max HR observed from target calendar day - 90 through target day,
 * inclusive. Calendar-day semantics match the Python resolver and DB queries.
 * Used to compute contextual FC max for each individual run.
 */
export function getObservedMaxHrForDate(activities, targetDate, windowDays = OBSERVED_MAX_HR_WINDOW_DAYS) {
  const targetDay = localDayNumber(targetDate)
  if (targetDay == null) return PERSONAL_MAX_HR_FALLBACK
  const windowStartDay = targetDay - windowDays

  let maxHr = 0
  for (const a of activities) {
    const observed = observedHeartRate(a.max_heartrate)
    if (observed == null) continue
    const activityDay = localDayNumber(a.start_date_local)
    if (activityDay != null && activityDay >= windowStartDay && activityDay <= targetDay) {
      if (observed > maxHr) maxHr = observed
    }
  }
  return maxHr > 0 ? Math.round(maxHr) : PERSONAL_MAX_HR_FALLBACK
}

/**
 * Get the max HR observed in the same inclusive calendar-day window, with the
 * date it occurred.
 */
export function getObservedMaxHrWithDate(activities, targetDate, windowDays = OBSERVED_MAX_HR_WINDOW_DAYS) {
  const targetDay = localDayNumber(targetDate)
  if (targetDay == null) {
    return { hr: PERSONAL_MAX_HR_FALLBACK, date: null, source: 'personal_fallback', windowDays }
  }
  const windowStartDay = targetDay - windowDays

  let maxHr = 0
  let maxDate = null
  let maxDay = -Infinity
  for (const a of activities) {
    const observed = observedHeartRate(a.max_heartrate)
    if (observed == null) continue
    const activityDay = localDayNumber(a.start_date_local)
    if (activityDay != null && activityDay >= windowStartDay && activityDay <= targetDay) {
      if (observed > maxHr || (observed === maxHr && activityDay > maxDay)) {
        maxHr = observed
        maxDate = a.start_date_local
        maxDay = activityDay
      }
    }
  }
  return {
    hr: maxHr > 0 ? Math.round(maxHr) : PERSONAL_MAX_HR_FALLBACK,
    date: maxDate,
    source: maxHr > 0 ? 'observed_90d' : 'personal_fallback',
    windowDays,
  }
}

/**
 * Get the FC max reference currently used by the frontend, including its
 * source. The manual override is deliberately browser-local; otherwise the
 * maximum observed over 90 days is used, with the personal fallback as a last
 * resort when no usable activity exists.
 */
export function getCurrentMaxHrInfo(activities) {
  const override = getManualFcMax()
  if (override > 0) {
    return { hr: override, date: null, source: 'manual_local', windowDays: null }
  }
  return getObservedMaxHrWithDate(
    activities,
    new Date(getNow()),
    OBSERVED_MAX_HR_WINDOW_DAYS
  )
}

export function getCurrentMaxHr(activities) {
  return getCurrentMaxHrInfo(activities).hr
}

/**
 * Get/set manual FC max override stored in localStorage.
 */
export function getManualFcMax() {
  try {
    // one-shot migration: strava_ → garmin_
    const old = localStorage.getItem('strava_fcmax_override')
    if (old) { localStorage.setItem(FCMAX_OVERRIDE_KEY, old); localStorage.removeItem('strava_fcmax_override') }
    const raw = localStorage.getItem(FCMAX_OVERRIDE_KEY)
    if (!raw) return 0
    const val = JSON.parse(raw)
    return val?.value || 0
  } catch { return 0 }
}

export function setManualFcMax(value) {
  if (!value || value <= 0) {
    localStorage.removeItem(FCMAX_OVERRIDE_KEY)
  } else {
    localStorage.setItem(FCMAX_OVERRIDE_KEY, JSON.stringify({ value: Math.round(value), ts: Date.now() }))
  }
}

// Default zones based on percentage of HRmax
const DEFAULT_ZONE_PCTS = [
  { zone: 1, label: 'Z1 - Récupération', min: 0.00, max: 0.65 },
  { zone: 2, label: 'Z2 - Endurance', min: 0.65, max: 0.75 },
  { zone: 3, label: 'Z3 - Tempo', min: 0.75, max: 0.85 },
  { zone: 4, label: 'Z4 - Seuil', min: 0.85, max: 0.95 },
  { zone: 5, label: 'Z5 - VO2max', min: 0.95, max: 1.00 },
]

export const ZONE_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#f97316', '#ef4444']
export const ZONE_LABELS = ['Z1', 'Z2', 'Z3', 'Z4', 'Z5']

/**
 * Build zone boundaries from HRmax using our custom percentages.
 * Always uses our defined thresholds (Z1 <65%, Z2 65-75%, Z3 75-85%, Z4 85-95%, Z5 >95%).
 * @param {Array|null} _providerZones - Ignored (kept for API compat)
 * @param {number|null} maxHr - FC max reference selected by the caller
 * @returns {Array} zones with { zone, label, min, max }
 */
export function buildZones(_providerZones, maxHr) {
  const hrMax = maxHr || PERSONAL_MAX_HR_FALLBACK
  return DEFAULT_ZONE_PCTS.map(z => ({
    zone: z.zone,
    label: z.label,
    min: Math.round(hrMax * z.min),
    max: z.zone === 5 ? 999 : Math.round(hrMax * z.max),
  }))
}

/**
 * Determine which zone a heart rate value falls into.
 */
export function getZone(hr, zones) {
  for (const z of zones) {
    if (hr >= z.min && hr < z.max) return z.zone
  }
  return hr >= zones[zones.length - 1]?.min ? 5 : 1
}

/**
 * Estimate time distribution across zones for a single activity.
 * Uses average_heartrate and max_heartrate to model a uniform HR distribution,
 * then calculates how much time falls in each zone.
 *
 * Without HR streams, we estimate: HR varied uniformly between
 * hrLow = 2*avgHr - maxHr and hrHigh = maxHr during the activity.
 * This ensures that an activity with maxHr in Z5 actually shows Z5 time.
 *
 * @param {object} activity - Activity with average_heartrate, max_heartrate, moving_time
 * @param {Array} zones - Zone boundaries from buildZones()
 * @returns {number[]} Array of 5 values (minutes in Z1..Z5)
 */
function estimateZoneMinutes(activity, zones) {
  const avgHr = activity.average_heartrate
  const maxHr = activity.max_heartrate
  const movingMin = (activity.moving_time || 0) / 60

  if (!avgHr || !movingMin) return [0, 0, 0, 0, 0]

  // If no max HR or max equals avg, put all time in one zone
  if (!maxHr || maxHr <= avgHr) {
    const result = [0, 0, 0, 0, 0]
    const z = getZone(avgHr, zones) - 1
    result[z] = movingMin
    return result
  }

  // Estimate HR range: symmetric around avgHr, bounded by maxHr on top
  const hrHigh = maxHr
  const hrLow = Math.max(2 * avgHr - maxHr, zones[0].min || 0, 60)
  const range = hrHigh - hrLow

  if (range <= 0) {
    const result = [0, 0, 0, 0, 0]
    const z = getZone(avgHr, zones) - 1
    result[z] = movingMin
    return result
  }

  // Calculate overlap between HR distribution [hrLow, hrHigh] and each zone
  const result = [0, 0, 0, 0, 0]
  for (let i = 0; i < zones.length; i++) {
    const zMin = zones[i].min
    const zMax = zones[i].max === 999 ? 300 : zones[i].max
    const overlapMin = Math.max(hrLow, zMin)
    const overlapMax = Math.min(hrHigh, zMax)
    if (overlapMax > overlapMin) {
      result[i] = movingMin * (overlapMax - overlapMin) / range
    }
  }

  return result
}

/**
 * Compute time in each zone from HR stream data.
 * @param {Array} hrStream - Array of HR values (1 per second or sample)
 * @param {Array} timeStream - Array of time values in seconds
 * @param {Array} zones - Zone boundaries
 * @returns {Array} [{ zone, label, color, seconds, pct }]
 */
export function computeTimeInZones(hrStream, timeStream, zones) {
  if (!hrStream?.length || !timeStream?.length) return []

  const zoneSeconds = [0, 0, 0, 0, 0]

  for (let i = 1; i < hrStream.length; i++) {
    const hr = hrStream[i]
    const dt = timeStream[i] - timeStream[i - 1]
    if (dt > 0 && dt < 300 && hr > 0) {
      const z = getZone(hr, zones)
      zoneSeconds[z - 1] += dt
    }
  }

  const total = zoneSeconds.reduce((a, b) => a + b, 0)
  return zoneSeconds.map((s, i) => ({
    zone: i + 1,
    label: ZONE_LABELS[i],
    fullLabel: zones[i]?.label || `Zone ${i + 1}`,
    color: ZONE_COLORS[i],
    seconds: s,
    pct: total > 0 ? Math.round((s / total) * 100) : 0,
    range: zones[i] ? `${zones[i].min}-${zones[i].max === 999 ? '∞' : zones[i].max}` : '',
  }))
}

/**
 * Compute relative effort for a single activity.
 * effort = sum(zone_weight * time_in_zone_minutes)
 * Weights: Z1=1, Z2=2, Z3=3, Z4=4, Z5=5
 */
export function computeRelativeEffort(hrStream, timeStream, zones) {
  if (!hrStream?.length || !timeStream?.length) return 0

  let effort = 0
  for (let i = 1; i < hrStream.length; i++) {
    const hr = hrStream[i]
    const dt = (timeStream[i] - timeStream[i - 1]) / 60 // minutes
    if (dt > 0 && dt < 5 && hr > 0) {
      const z = getZone(hr, zones)
      effort += z * dt
    }
  }
  return Math.round(effort)
}

/**
 * Estimate relative effort from activity summary (no streams needed).
 * Uses zone distribution estimate for more accurate weighting.
 */
export function estimateRelativeEffort(avgHr, movingTimeSec, zones, maxHr) {
  if (!avgHr || !movingTimeSec) return 0
  const fakeActivity = { average_heartrate: avgHr, max_heartrate: maxHr, moving_time: movingTimeSec }
  const zoneMins = estimateZoneMinutes(fakeActivity, zones)
  let effort = 0
  zoneMins.forEach((m, i) => { effort += (i + 1) * m })
  return Math.round(effort)
}

/**
 * Compute zone distribution for a collection of activities over a period.
 * Uses estimated zone distribution per activity (not just average HR).
 * @param {Array} activities - Activities with average_heartrate, max_heartrate, moving_time
 * @param {Array} zones - Zone boundaries
 * @param {number} days - Number of days to look back
 * @returns {{ aerobic: number, anaerobic: number, zoneDistribution: Array }}
 */
export function computeZoneDistribution(activities, zones, days = 90) {
  const cutoff = getNow() - days * 86400000
  const filtered = activities.filter(a => {
    const t = parseLocal(a.start_date_local).getTime()
    return t >= cutoff && a.average_heartrate
  })

  const zoneTotals = [0, 0, 0, 0, 0]
  filtered.forEach(a => {
    const mins = estimateZoneMinutes(a, zones)
    for (let i = 0; i < 5; i++) zoneTotals[i] += mins[i]
  })

  const total = zoneTotals.reduce((a, b) => a + b, 0)
  const lowAerobic = zoneTotals[0] + zoneTotals[1]     // Z1-2
  const highAerobic = zoneTotals[2] + zoneTotals[3]     // Z3-4
  const anaerobic = zoneTotals[4]                        // Z5

  return {
    aerobic: total > 0 ? Math.round(((lowAerobic + highAerobic) / total) * 100) : 0,
    anaerobic: total > 0 ? Math.round((anaerobic / total) * 100) : 0,
    lowAerobic: Math.round(lowAerobic),
    highAerobic: Math.round(highAerobic),
    anaerobicMin: Math.round(anaerobic),
    totalMinutes: Math.round(total),
    zoneDistribution: zoneTotals.map((m, i) => ({
      zone: i + 1,
      label: ZONE_LABELS[i],
      color: ZONE_COLORS[i],
      minutes: Math.round(m),
      pct: total > 0 ? Math.round((m / total) * 100) : 0,
    })),
  }
}

/**
 * Compute weekly zone distribution for stacked chart.
 * Uses estimated zone distribution per activity (not just average HR).
 */
export function computeWeeklyZones(activities, zones) {
  const byWeek = {}
  activities.forEach(a => {
    if (!a.average_heartrate) return
    const d = parseLocal(a.start_date_local)
    const key = localDateStr(getMonday(d))

    if (!byWeek[key]) byWeek[key] = { week: key, z1: 0, z2: 0, z3: 0, z4: 0, z5: 0 }

    // Distribute time across zones using estimated distribution
    const mins = estimateZoneMinutes(a, zones)
    byWeek[key].z1 += mins[0]
    byWeek[key].z2 += mins[1]
    byWeek[key].z3 += mins[2]
    byWeek[key].z4 += mins[3]
    byWeek[key].z5 += mins[4]
  })

  return Object.values(byWeek)
    .sort((a, b) => a.week.localeCompare(b.week))
    .map(w => ({
      ...w,
      z1: Math.round(w.z1),
      z2: Math.round(w.z2),
      z3: Math.round(w.z3),
      z4: Math.round(w.z4),
      z5: Math.round(w.z5),
    }))
}

/**
 * Compute aerobic/anaerobic load evolution over 30-day sliding windows.
 * Uses estimated zone distribution per activity.
 */
export function computeLoadEvolution(activities, zones) {
  const sorted = [...activities]
    .filter(a => a.average_heartrate)
    .sort((a, b) => parseLocal(a.start_date_local) - parseLocal(b.start_date_local))

  if (sorted.length < 10) return []

  const result = []
  const windowDays = 30

  // Generate data points every 7 days
  const startDate = parseLocal(sorted[0].start_date_local).getTime()
  const endDate = getNow()

  for (let t = startDate + windowDays * 86400000; t <= endDate; t += 7 * 86400000) {
    const windowStart = t - windowDays * 86400000
    const windowActs = sorted.filter(a => {
      const at = parseLocal(a.start_date_local).getTime()
      return at >= windowStart && at <= t
    })

    let lowAerobic = 0, highAerobic = 0, anaerobic = 0
    windowActs.forEach(a => {
      const mins = estimateZoneMinutes(a, zones)
      lowAerobic += mins[0] + mins[1]     // Z1-Z2
      highAerobic += mins[2] + mins[3]     // Z3-Z4
      anaerobic += mins[4]                  // Z5
    })

    result.push({
      date: localDateStr(new Date(t)),
      lowAerobic: Math.round(lowAerobic),
      highAerobic: Math.round(highAerobic),
      anaerobic: Math.round(anaerobic),
    })
  }

  return result
}
