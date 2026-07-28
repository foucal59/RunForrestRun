/**
 * Best efforts and breakaway analysis from activity streams.
 * Computes best times for standard distances and time intervals.
 */

import { getNow } from './clock'

// Standard distance-based best efforts
const DISTANCE_EFFORTS = [
  { key: '400m', distance: 400, label: '400m' },
  { key: '1km', distance: 1000, label: '1 km' },
  { key: '1mile', distance: 1609.34, label: '1 mile' },
  { key: '5km', distance: 5000, label: '5 km' },
  { key: '10km', distance: 10000, label: '10 km' },
  { key: 'semi', distance: 21097.5, label: 'Semi-marathon' },
  { key: 'marathon', distance: 42195, label: 'Marathon' },
]

// Time-based intervals for breakaway analysis
const TIME_INTERVALS = [
  { key: '30s', seconds: 30, label: '30 sec' },
  { key: '1min', seconds: 60, label: '1 min' },
  { key: '5min', seconds: 300, label: '5 min' },
  { key: '10min', seconds: 600, label: '10 min' },
  { key: '20min', seconds: 1200, label: '20 min' },
  { key: '30min', seconds: 1800, label: '30 min' },
  { key: '60min', seconds: 3600, label: '60 min' },
]

/**
 * Format seconds to mm:ss or h:mm:ss
 */
export function fmtEffortTime(seconds) {
  if (!seconds || seconds <= 0) return '-'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h > 0) return `${h}h${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

/**
 * Format pace in min/km from speed (m/s)
 */
function fmtPaceFromSpeed(speedMs) {
  if (!speedMs || speedMs <= 0) return '-'
  const paceS = Math.round(1000 / speedMs)
  const m = Math.floor(paceS / 60)
  const s = paceS % 60
  return `${m}:${String(s).padStart(2, '0')}/km`
}

/**
 * Find the best (fastest) time for a given distance using stream data.
 * Uses a sliding window on the distance stream.
 */
function bestTimeForDistance(distStream, timeStream, targetDist) {
  if (!distStream?.length || !timeStream?.length) return null
  if (distStream[distStream.length - 1] < targetDist) return null

  let bestTime = Infinity
  let j = 0

  for (let i = 0; i < distStream.length; i++) {
    while (j < distStream.length && (distStream[j] - distStream[i]) < targetDist) {
      j++
    }
    if (j >= distStream.length) break

    // Interpolate exact distance
    const overshoot = distStream[j] - distStream[i] - targetDist
    const segmentDist = j > 0 ? distStream[j] - distStream[j - 1] : 1
    const segmentTime = j > 0 ? timeStream[j] - timeStream[j - 1] : 0
    const timeAdjust = segmentDist > 0 ? (overshoot / segmentDist) * segmentTime : 0

    const time = timeStream[j] - timeStream[i] - timeAdjust
    if (time < bestTime) bestTime = time
  }

  return bestTime < Infinity ? bestTime : null
}

/**
 * Find the best (highest) average speed for a given time interval.
 * Uses a sliding window on the time stream.
 */
function bestSpeedForDuration(distStream, timeStream, targetDuration) {
  if (!distStream?.length || !timeStream?.length) return null
  const totalTime = timeStream[timeStream.length - 1]
  if (totalTime < targetDuration) return null

  let bestDist = 0
  let j = 0

  for (let i = 0; i < timeStream.length; i++) {
    while (j < timeStream.length && (timeStream[j] - timeStream[i]) < targetDuration) {
      j++
    }
    if (j >= timeStream.length) break

    // Interpolate for exact duration
    const overshootTime = timeStream[j] - timeStream[i] - targetDuration
    const segmentTime = j > 0 ? timeStream[j] - timeStream[j - 1] : 1
    const segmentDist = j > 0 ? distStream[j] - distStream[j - 1] : 0
    const distAdjust = segmentTime > 0 ? (overshootTime / segmentTime) * segmentDist : 0

    const dist = distStream[j] - distStream[i] - distAdjust
    if (dist > bestDist) bestDist = dist
  }

  return bestDist > 0 ? bestDist / targetDuration : null // m/s
}

/**
 * Compute best efforts for a single activity from its streams.
 * @param {Object} streams - { distance: {data:[]}, time: {data:[]}, ... }
 * @param {string} activityDate - ISO date string
 * @param {number} activityId
 * @returns {{ distances: Array, intervals: Array }}
 */
export function computeActivityBestEfforts(streams, activityDate, activityId) {
  const distData = streams?.distance?.data
  const timeData = streams?.time?.data

  if (!distData || !timeData) return { distances: [], intervals: [] }

  const distances = DISTANCE_EFFORTS.map(d => {
    const time = bestTimeForDistance(distData, timeData, d.distance)
    return {
      key: d.key,
      label: d.label,
      distance: d.distance,
      time,
      formatted: fmtEffortTime(time),
      pace: time ? fmtPaceFromSpeed(d.distance / time) : '-',
      date: activityDate,
      activityId,
    }
  }).filter(d => d.time !== null)

  const intervals = TIME_INTERVALS.map(t => {
    const speed = bestSpeedForDuration(distData, timeData, t.seconds)
    return {
      key: t.key,
      label: t.label,
      seconds: t.seconds,
      speed,
      distance: speed ? speed * t.seconds : null,
      pace: speed ? fmtPaceFromSpeed(speed) : '-',
      date: activityDate,
      activityId,
    }
  }).filter(t => t.speed !== null)

  return { distances, intervals }
}

/**
 * Merge best efforts across multiple activities, keeping only the all-time bests.
 * @param {Array} allEfforts - Array of { distances, intervals } from each activity
 * @returns {{ distances: Array, intervals: Array }}
 */
export function mergeAllTimeBests(allEfforts) {
  const bestDist = {}
  const bestInt = {}

  allEfforts.forEach(({ distances, intervals }) => {
    distances.forEach(d => {
      if (!bestDist[d.key] || d.time < bestDist[d.key].time) {
        bestDist[d.key] = { ...d }
      }
    })
    intervals.forEach(t => {
      if (!bestInt[t.key] || t.speed > bestInt[t.key].speed) {
        bestInt[t.key] = { ...t }
      }
    })
  })

  return {
    distances: DISTANCE_EFFORTS
      .map(d => bestDist[d.key])
      .filter(Boolean),
    intervals: TIME_INTERVALS
      .map(t => bestInt[t.key])
      .filter(Boolean),
  }
}

/**
 * Compute breakaway comparison: all-time best vs last 8 weeks best.
 * @param {Array} allEfforts - Array of { distances, intervals, date }
 * @returns {Array} comparison data for chart
 */
export function computeBreakawayComparison(allEfforts) {
  const eightWeeksAgo = getNow() - 56 * 86400000

  const allTime = allEfforts
  const recent = allEfforts.filter(e =>
    e.distances?.[0]?.date && new Date(e.distances[0].date).getTime() >= eightWeeksAgo
  )

  const allTimeBests = mergeAllTimeBests(allTime)
  const recentBests = mergeAllTimeBests(recent)

  // For time-based intervals: compare speeds
  const comparison = TIME_INTERVALS.map(t => {
    const allBest = allTimeBests.intervals.find(i => i.key === t.key)
    const recBest = recentBests.intervals.find(i => i.key === t.key)
    return {
      label: t.label,
      key: t.key,
      allTimePace: allBest?.pace || '-',
      allTimeSpeed: allBest?.speed || 0,
      recentPace: recBest?.pace || '-',
      recentSpeed: recBest?.speed || 0,
      pctDiff: allBest?.speed && recBest?.speed
        ? Math.round(((recBest.speed - allBest.speed) / allBest.speed) * 100)
        : null,
    }
  }).filter(c => c.allTimeSpeed > 0)

  return comparison
}

export { DISTANCE_EFFORTS, TIME_INTERVALS }
