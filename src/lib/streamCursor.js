function valueAt(data, index) {
  return Array.isArray(data) && index >= 0 && index < data.length ? data[index] : null
}

function isValidLatLng(point) {
  return Array.isArray(point) &&
    point.length === 2 &&
    Number.isFinite(point[0]) &&
    Number.isFinite(point[1]) &&
    point[0] >= -90 &&
    point[0] <= 90 &&
    point[1] >= -180 &&
    point[1] <= 180
}

export function readStreamIndexFromChartState(state) {
  const payload = state?.activePayload || []
  for (const item of payload) {
    const streamIndex = item?.payload?.streamIndex
    if (Number.isFinite(streamIndex)) return Math.round(streamIndex)
  }
  return null
}

export function getActiveStreamPoint(streams, index) {
  if (!streams || !Number.isFinite(index)) return null
  const streamIndex = Math.max(0, Math.round(index))
  const distanceM = valueAt(streams.distance?.data, streamIndex)
  const timeS = valueAt(streams.time?.data, streamIndex)
  const speed = valueAt(streams.velocity_smooth?.data, streamIndex)
  const hr = valueAt(streams.heartrate?.data, streamIndex)
  const latlng = valueAt(streams.latlng?.data, streamIndex)

  if (
    !Number.isFinite(distanceM) &&
    !Number.isFinite(timeS) &&
    !Number.isFinite(speed) &&
    !Number.isFinite(hr) &&
    !isValidLatLng(latlng)
  ) {
    return null
  }

  return {
    streamIndex,
    distance: Number.isFinite(distanceM) ? Math.round(distanceM) / 1000 : null,
    time: Number.isFinite(timeS) ? Math.round((timeS / 60) * 10) / 10 : null,
    speed: Number.isFinite(speed) ? speed : null,
    hr: Number.isFinite(hr) ? hr : null,
    latlng: isValidLatLng(latlng) ? latlng : null,
  }
}

export function findNearestStreamDatum(data, index) {
  if (!Array.isArray(data) || !data.length || !Number.isFinite(index)) return null
  let best = null
  let bestDistance = Number.POSITIVE_INFINITY

  for (const item of data) {
    if (!Number.isFinite(item?.streamIndex)) continue
    const distance = Math.abs(item.streamIndex - index)
    if (distance < bestDistance) {
      best = item
      bestDistance = distance
    }
  }

  return best
}

export function formatChartMinutes(minutes) {
  if (!Number.isFinite(minutes)) return '-'
  return Number.isInteger(minutes) ? `${minutes} min` : `${minutes.toFixed(1)} min`
}
