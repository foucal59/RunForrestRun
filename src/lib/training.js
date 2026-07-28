/**
 * Training load computations: TRIMP, ATL, CTL, TSB, training status.
 */

import { parseLocalDate as parseDate, localDateStr as dayKey, getMonday } from './compute'
import { getNow } from './clock'
import { getManualFcMax } from './heartRateZones'

const clamp01 = value => Math.max(0, Math.min(1, value))

function estimateTRIMP(activity) {
  const durationMin = (activity.moving_time || 0) / 60
  if (activity.average_heartrate) {
    return durationMin * (activity.average_heartrate / 180)
  }
  return durationMin * 0.75
}

function ewma(values, span) {
  const alpha = 2 / (span + 1)
  const result = []
  let prev = 0
  for (let i = 0; i < values.length; i++) {
    const v = values[i]
    prev = i === 0 ? v : alpha * v + (1 - alpha) * prev
    result.push(prev)
  }
  return result
}

// Aerobic efficiency proxy for a single activity: speed / (HR - resting_baseline).
// Higher value = same effort at higher speed = better aerobic fitness.
// Returns null for intervals, strolls, short runs, or missing HR.
function _aerobicEffScore(activity) {
  const hr = activity.average_heartrate
  const speed = (activity.distance > 0 && activity.moving_time > 0)
    ? activity.distance / activity.moving_time  // m/s
    : 0
  if (!hr || hr < 120 || hr > 170) return null  // not a steady aerobic run
  if (speed < 1.5 || speed > 5.5) return null    // outside 5.4–19.8 km/h
  if ((activity.moving_time || 0) < 1200) return null  // < 20 min
  return speed / (hr - 60)  // normalized by effort above resting baseline
}

export function computeTrainingLoad(activities, opts = {}) {
  // `daysBack` controls how far back the daily series starts from `now`.
  //   number → that many days
  //   null   → from the earliest activity (full history)
  //   omitted → 180 (legacy default)
  const { daysBack = 180 } = opts

  if (!activities.length) {
    console.log('[TRAINING] No activities, returning empty')
    return { daily: [], atl: [], ctl: [], tsb: [], weeklyLoad: [], status: 'Recovery', currentATL: 0, currentCTL: 0, currentTSB: 0, perfTrend: null }
  }

  // Build daily load map
  const dailyMap = {}
  let earliestTs = null
  activities.forEach(a => {
    const dt = parseDate(a.start_date_local)
    const key = dayKey(dt)
    const load = estimateTRIMP(a)
    dailyMap[key] = (dailyMap[key] || 0) + load
    const ts = dt.getTime()
    if (earliestTs == null || ts < earliestTs) earliestTs = ts
  })

  // Generate continuous daily series.
  // Use midnight + string comparison to avoid DST drift.
  // `now` is the simulated clock when the user is rewinding the dashboard.
  const now = new Date(getNow())
  const todayKey = dayKey(now)
  const d = new Date(now)
  if (daysBack == null) {
    d.setTime(earliestTs ?? now.getTime() - 180 * 86400000)
  } else {
    d.setDate(d.getDate() - daysBack)
  }
  d.setHours(0, 0, 0, 0)
  const days = []
  while (dayKey(d) <= todayKey) {
    const key = dayKey(d)
    days.push({ date: key, load: Math.round((dailyMap[key] || 0) * 10) / 10 })
    d.setDate(d.getDate() + 1)
  }

  console.log('[TRAINING]', activities.length, 'activities,', Object.keys(dailyMap).length, 'days with load, today:', todayKey, 'todayLoad:', dailyMap[todayKey] || 0)
  console.log('[TRAINING] Series range:', days[0]?.date, '→', days[days.length - 1]?.date, '(', days.length, 'days)')

  const loads = days.map(d => d.load)
  const atlValues = ewma(loads, 7)
  const ctlValues = ewma(loads, 42)

  const daily = days.map((d, i) => ({
    date: d.date,
    load: d.load,
    atl: Math.round(atlValues[i] * 10) / 10,
    ctl: Math.round(ctlValues[i] * 10) / 10,
    tsb: Math.round((ctlValues[i] - atlValues[i]) * 10) / 10,
  }))

  // Weekly aggregation
  const weekMap = {}
  days.forEach(d => {
    const key = dayKey(getMonday(new Date(d.date)))
    if (!weekMap[key]) weekMap[key] = { week: key, load: 0 }
    weekMap[key].load += d.load
  })
  const weeklyLoad = Object.values(weekMap)
    .sort((a, b) => a.week.localeCompare(b.week))
    .map(w => ({ ...w, load: Math.round(w.load * 10) / 10 }))

  const currentATL = daily.length ? daily[daily.length - 1].atl : 0
  const currentCTL = daily.length ? daily[daily.length - 1].ctl : 0
  const currentTSB = daily.length ? daily[daily.length - 1].tsb : 0

  // --- Performance trend (Garmin-style VO2max proxy) ---
  // Compare aerobic efficiency of last 28 days vs prior 28 days.
  // Requires ≥ 3 qualifying steady-aerobic runs in each window.
  const nowMs = now.getTime()
  const d28ms = nowMs - 28 * 86400000
  const d56ms = nowMs - 56 * 86400000
  const effInWindow = (from, to) =>
    activities
      .filter(a => { const t = parseDate(a.start_date_local).getTime(); return t >= from && t < to })
      .map(_aerobicEffScore)
      .filter(s => s !== null)
  const recentEff = effInWindow(d28ms, nowMs)
  const olderEff  = effInWindow(d56ms, d28ms)
  const mean = arr => arr.reduce((a, b) => a + b, 0) / arr.length
  const perfTrend = (recentEff.length >= 3 && olderEff.length >= 3)
    ? (mean(recentEff) - mean(olderEff)) / mean(olderEff)
    : null

  // Ratio: recent acute load vs chronic baseline. > 1 = above-baseline effort.
  const loadRatio = currentCTL > 1 ? currentATL / currentCTL : (currentATL > 0 ? 1.5 : 0)

  // CTL trend (14 days ago vs now) — kept as fallback signal
  const ctl14ago = daily.length > 14 ? daily[daily.length - 15].ctl : 0
  const ctlTrend = ctl14ago > 0 ? (currentCTL - ctl14ago) / ctl14ago : 0

  // --- Training status: Garmin-style (load × performance matrix) ---
  // Primary: loadRatio + perfTrend.  Fallback to TSB when insufficient perf data.
  let status = 'Recovery'
  if (loadRatio > 1.5) {
    // Acute load far exceeds chronic baseline regardless of performance
    status = 'Overreaching'
  } else if (perfTrend !== null) {
    // Garmin matrix: performance trend × load level
    if (perfTrend > 0.03 && loadRatio >= 0.6) {
      status = 'Productive'     // getting fitter AND training
    } else if (perfTrend > 0.03) {
      status = 'Peaking'        // getting fitter while load is low (taper/rest)
    } else if (perfTrend < -0.05 && loadRatio >= 0.5) {
      status = 'Unproductive'   // training but fitness declining → overtraining signal
    } else if (perfTrend < -0.05 && loadRatio < 0.2) {
      status = 'Detraining'     // not training AND fitness declining
    } else if (loadRatio >= 0.3) {
      status = 'Maintaining'    // load present, performance stable
    }
    // else loadRatio < 0.3 + no perf signal → Recovery (true rest)
  } else {
    // Fallback when < 3 qualifying runs in a window (no HR data, new athlete…)
    if (currentTSB < 0) {
      status = 'Productive'     // fatigue accumulated = active training block
    } else if (currentTSB > 15) {
      status = 'Peaking'
    } else if (currentTSB > 5 || Math.abs(ctlTrend) <= 0.02) {
      status = 'Maintaining'
    } else if (ctlTrend < -0.10) {
      status = 'Detraining'
    }
  }

  console.log(
    '[TRAINING] Status:', status,
    '| loadRatio:', loadRatio.toFixed(2),
    '| perfTrend:', perfTrend !== null ? (perfTrend * 100).toFixed(1) + '%' : 'n/a',
    '| TSB:', currentTSB, '| ATL:', currentATL, '| CTL:', currentCTL,
    '| recentEff runs:', recentEff.length, '| olderEff runs:', olderEff.length,
  )

  return { daily, weeklyLoad, status, currentATL, currentCTL, currentTSB, perfTrend }
}


/**
 * Compute training load distribution: low aerobic, aerobic, anaerobic.
 * Based on HR zones (Z1-Z2 = low aerobic, Z3-Z4 = aerobic, Z5 = anaerobic).
 * Uses estimated zone distribution per activity (not just average HR).
 */
export function computeLoadDistribution(activities, { fcMax: fcMaxOverride } = {}) {
  const now = new Date(getNow())

  let fcMax = fcMaxOverride
  if (!fcMax || fcMax <= 0) {
    const overrideHr = getManualFcMax()

    const d90ago = now.getTime() - 90 * 86400000
    let maxHrObs = 0
    for (const a of activities) {
      if (!a.max_heartrate) continue
      const t = parseDate(a.start_date_local).getTime()
      if (t >= d90ago) {
        if (a.max_heartrate > maxHrObs) maxHrObs = a.max_heartrate
      }
    }
    fcMax = overrideHr > 0 ? overrideHr : (maxHrObs > 0 ? Math.round(maxHrObs) : 190)
  }

  const zoneBounds = [
    { zone: 1, min: 0, max: Math.round(fcMax * 0.65) },
    { zone: 2, min: Math.round(fcMax * 0.65), max: Math.round(fcMax * 0.75) },
    { zone: 3, min: Math.round(fcMax * 0.75), max: Math.round(fcMax * 0.85) },
    { zone: 4, min: Math.round(fcMax * 0.85), max: Math.round(fcMax * 0.95) },
    { zone: 5, min: Math.round(fcMax * 0.95), max: 999 },
  ]

  const d28 = new Date(now - 28 * 86400000)
  const recent = activities.filter(a => {
    const dt = parseDate(a.start_date_local)
    return dt >= d28 && a.average_heartrate
  })

  if (!recent.length) return null

  let lowAerobic = 0, aerobic = 0, anaerobic = 0

  recent.forEach(a => {
    const avgHr = a.average_heartrate
    const maxHr = a.max_heartrate
    const movingMin = (a.moving_time || 0) / 60

    if (!avgHr || !movingMin) return

    let activityLowAerobic = 0
    let activityAerobic = 0
    let activityAnaerobic = 0

    if (!maxHr || maxHr <= avgHr) {
      if (avgHr < zoneBounds[1].max) activityLowAerobic += movingMin
      else if (avgHr < zoneBounds[3].max) activityAerobic += movingMin
      else activityAnaerobic += movingMin
    } else {
      // HR is sampled as integer bpm values. Treat the recorded max as the top
      // of a 1-bpm bucket so a run peaking exactly on the Z5 threshold is not
      // rounded down to zero anaerobic contribution.
      const hrHigh = maxHr + 1
      const hrLow = Math.max(2 * avgHr - maxHr, 60)
      const range = hrHigh - hrLow
      if (range <= 0) {
        if (avgHr < zoneBounds[1].max) activityLowAerobic += movingMin
        else if (avgHr < zoneBounds[3].max) activityAerobic += movingMin
        else activityAnaerobic += movingMin
      } else {
        for (let i = 0; i < zoneBounds.length; i++) {
          const zMin = zoneBounds[i].min
          const zMax = zoneBounds[i].max === 999 ? 300 : zoneBounds[i].max
          const overlapMin = Math.max(hrLow, zMin)
          const overlapMax = Math.min(hrHigh, zMax)
          if (overlapMax > overlapMin) {
            const mins = movingMin * (overlapMax - overlapMin) / range
            if (i <= 1) activityLowAerobic += mins
            else if (i <= 3) activityAerobic += mins
            else activityAnaerobic += mins
          }
        }
      }
    }

    const speedSurge = a.average_speed > 0 && a.max_speed > 0 ? a.max_speed / a.average_speed : 0
    const hrRatio = maxHr ? maxHr / fcMax : 0
    const intervalScore = clamp01((speedSurge - 1.35) / 0.45) * clamp01((hrRatio - 0.90) / 0.06)
    if (intervalScore > 0) {
      // Short intervals often produce anaerobic load before heart rate has time
      // to sit in Z5. Move a bounded part of the high-aerobic estimate into the
      // anaerobic bucket when the run combines speed surges with near-max HR.
      const anaerobicProxy = movingMin * (0.04 + 0.14 * intervalScore)
      const shift = Math.min(
        Math.max(0, anaerobicProxy - activityAnaerobic),
        activityAerobic * 0.45
      )
      activityAerobic -= shift
      activityAnaerobic += shift
    }

    lowAerobic += activityLowAerobic
    aerobic += activityAerobic
    anaerobic += activityAnaerobic
  })

  const total = lowAerobic + aerobic + anaerobic
  if (total === 0) return null

  const pct = v => v > 0 ? Math.max(1, Math.round(v / total * 100)) : 0

  return {
    lowAerobic: { minutes: Math.round(lowAerobic), pct: pct(lowAerobic) },
    aerobic: { minutes: Math.round(aerobic), pct: pct(aerobic) },
    anaerobic: { minutes: Math.round(anaerobic), pct: pct(anaerobic) },
    total: Math.round(total),
    period: '28j',
    fcMax,
  }
}

export const STATUS_COLORS = {
  Productive: '#10b981',
  Maintaining: '#3b82f6',
  Peaking: '#f59e0b',
  Overreaching: '#ef4444',
  Unproductive: '#f97316',
  Detraining: '#6b7280',
  Recovery: '#8b5cf6',
}

export const STATUS_LABELS = {
  Productive: 'Productif',
  Maintaining: 'Maintien',
  Peaking: 'Pic de forme',
  Overreaching: 'Surcharge',
  Unproductive: 'Improductif',
  Detraining: 'Désentraînement',
  Recovery: 'Récupération',
}

// Zones de forme (TSB) partagées par les pages Training et Progress.
export const TSB_ZONES = [
  { min: 25, max: 100, label: 'Tres repose', color: '#dbeafe', desc: 'Perte de forme potentielle si prolonge' },
  { min: 5, max: 25, label: 'Frais / Taper', color: '#dcfce7', desc: 'Ideal pour la competition' },
  { min: -10, max: 5, label: 'Optimal', color: '#f0fdf4', desc: 'Zone grise optimale pour progresser' },
  { min: -30, max: -10, label: 'Fatigue', color: '#fef9c3', desc: 'Surcharge fonctionnelle, progression en cours' },
  { min: -100, max: -30, label: 'Surentrainement', color: '#fee2e2', desc: 'Risque de blessure, reduire la charge' },
]

export function getTSBZone(tsb) {
  return TSB_ZONES.find(z => tsb >= z.min && tsb < z.max) || TSB_ZONES[2]
}

export function fmtDuration(sec) {
  if (!sec) return '-'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  return h > 0 ? `${h}h${String(m).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}

export function rangeSubtitle(effectiveDateRange) {
  if (!effectiveDateRange) return 'Toutes les données'
  if (effectiveDateRange.presetDays != null) return `${effectiveDateRange.presetDays} derniers jours`
  const span = Math.max(1, Math.ceil((effectiveDateRange.to - effectiveDateRange.from) / 86400000))
  return `${span} jours sélectionnés`
}
