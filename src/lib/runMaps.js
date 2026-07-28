import { fmtPace, parseLocalDate } from './compute'

export function sanitizeLatLngPoints(points) {
  if (!Array.isArray(points)) return []
  return points.filter(point =>
    Array.isArray(point) &&
    point.length === 2 &&
    Number.isFinite(point[0]) &&
    Number.isFinite(point[1]) &&
    point[0] >= -90 && point[0] <= 90 &&
    point[1] >= -180 && point[1] <= 180
  )
}

// A Google-encoded polyline is pure ASCII in the 0x3F–0x7E range. Some legacy
// Garmin syncs stored a raw GPS-sample JSON blob in summary_polyline; it carries
// `"`, `:`, spaces, dots and digits (all < 0x3F), and feeding that to
// decodePolyline yields garbage coords that break the map's fitBounds. Accept
// only strings whose every char sits in the valid polyline range.
export function isLikelyEncodedPolyline(str) {
  return typeof str === 'string' && str.length > 0 && /^[\x3f-\x7e]+$/.test(str)
}

export function findTraceFallbackActivity(activity, allActivities = []) {
  if (!activity?.start_date_local) return null
  const targetTime = parseLocalDate(activity.start_date_local).getTime()
  const targetDistance = activity.distance || 0
  const targetMovingTime = activity.moving_time || 0
  let best = null
  let bestScore = Number.POSITIVE_INFINITY

  for (const other of allActivities) {
    if (!other || other.id === activity.id || !isLikelyEncodedPolyline(other.summary_polyline) || !other.start_date_local) continue
    const timeDiff = Math.abs(parseLocalDate(other.start_date_local).getTime() - targetTime)
    if (timeDiff > 2 * 60 * 1000) continue
    const distDiff = Math.abs((other.distance || 0) - targetDistance)
    if (distDiff > 150) continue
    const movingTimeDiff = Math.abs((other.moving_time || 0) - targetMovingTime)
    if (targetMovingTime > 0 && movingTimeDiff > 180) continue
    const score = timeDiff + distDiff + movingTimeDiff
    if (score < bestScore) {
      best = other
      bestScore = score
    }
  }

  return best
}

export function resolveRunTrace(activity, allActivities = [], points = null, options = {}) {
  const { allowFallback = true } = options
  const sanitizedPoints = sanitizeLatLngPoints(points)
  if (sanitizedPoints.length > 1) return { points: sanitizedPoints, traceSource: 'streams' }
  if (isLikelyEncodedPolyline(activity?.summary_polyline)) {
    return { polyline: activity.summary_polyline, traceSource: 'activity_polyline' }
  }
  if (!allowFallback) return null
  const fallback = findTraceFallbackActivity(activity, allActivities)
  if (isLikelyEncodedPolyline(fallback?.summary_polyline)) {
    return { polyline: fallback.summary_polyline, traceSource: `fallback_polyline:${fallback.id}` }
  }
  return null
}

export function toMapRun(activity, allActivities = [], points = null, options = {}) {
  const trace = resolveRunTrace(activity, allActivities, points, options)
  if (!trace) return null
  return {
    id: activity.id,
    name: activity.name,
    date: activity.start_date_local,
    distanceKm: Math.round((activity.distance || 0) / 100) / 10,
    pace: activity.average_speed > 0 ? fmtPace(1000 / activity.average_speed) : '-',
    prCount: activity.pr_count || 0,
    ...trace,
  }
}
