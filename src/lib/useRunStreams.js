import { useState, useMemo, useCallback } from 'react'
import { buildZones, computeTimeInZones, computeRelativeEffort, getMaxHrForDate } from './heartRateZones'
import { computeActivityBestEfforts } from './bestEfforts'
import { fmtPaceFromSpeed } from './compute'
import { resolveRunTrace } from './runMaps'
import { findNearestStreamDatum, getActiveStreamPoint, readStreamIndexFromChartState } from './streamCursor'

/**
 * Pipeline de données streams partagé entre ActivityDetail et RunModal :
 * zones FC contextuelles, temps en zones, effort relatif, best efforts,
 * séries échantillonnées pour les graphes et curseur carte/graphes synchronisé.
 * `samples` contrôle la densité d'échantillonnage des graphes (500 page, 200 modal).
 */
export function useRunStreams({ activity, allActivities, streams, zoneData, activityId, samples }) {
  const [activeStreamIndex, setActiveStreamIndex] = useState(null)

  // Contextual FC max: use the 90-day window before THIS activity's date
  const maxHr = useMemo(() => {
    if (!activity || !allActivities.length) return 190
    return getMaxHrForDate(allActivities, activity.start_date_local, 90)
  }, [allActivities, activity])

  const zones = useMemo(() => buildZones(zoneData, maxHr), [zoneData, maxHr])

  const timeInZones = useMemo(() => {
    if (!streams?.heartrate?.data || !streams?.time?.data) return []
    return computeTimeInZones(streams.heartrate.data, streams.time.data, zones)
  }, [streams, zones])

  const relativeEffort = useMemo(() => {
    if (!streams?.heartrate?.data || !streams?.time?.data) return 0
    return computeRelativeEffort(streams.heartrate.data, streams.time.data, zones)
  }, [streams, zones])

  const bestEfforts = useMemo(() => {
    if (!streams) return { distances: [], intervals: [] }
    return computeActivityBestEfforts(streams, activity?.start_date_local, Number(activityId))
  }, [streams, activity, activityId])

  const speedData = useMemo(() => {
    if (!streams?.distance?.data || !streams?.velocity_smooth?.data) return []
    const dist = streams.distance.data
    const vel = streams.velocity_smooth.data
    const step = Math.max(1, Math.floor(dist.length / samples))
    // Moyenne glissante centrée (~3 pas d'échantillonnage) : la vitesse
    // seconde par seconde de la montre oscille en permanence — le simple
    // sous-échantillonnage gardait ces pics d'1 s et rendait la courbe
    // illisible.
    const half = Math.max(2, Math.floor((step * 3) / 2))
    const smoothAt = center => {
      let sum = 0
      let n = 0
      const from = Math.max(0, center - half)
      const to = Math.min(vel.length - 1, center + half)
      for (let i = from; i <= to; i++) {
        const v = vel[i]
        if (Number.isFinite(v)) { sum += v; n += 1 }
      }
      return n ? sum / n : 0
    }
    const points = dist.filter((_, i) => i % step === 0).map((d, idx) => {
      const streamIndex = idx * step
      const speed = smoothAt(streamIndex)
      return {
        streamIndex,
        distance: Math.round(d) / 1000,
        speed,
        pace: fmtPaceFromSpeed(speed),
      }
    })
    if (points.length) {
      const rawMax = Math.max(...vel.filter(Number.isFinite))
      const smoothMax = Math.max(...points.map(p => p.speed))
      console.log('[useRunStreams] vitesse lissée:', points.length, 'pts · fenêtre', half * 2 + 1,
        'échantillons · max brut', fmtPaceFromSpeed(rawMax), '/km · max lissé', fmtPaceFromSpeed(smoothMax), '/km')
    }
    return points
  }, [streams, samples])

  // Pic de la courbe lissée — remplace le max_speed Garmin (pic instantané
  // d'1 s, souvent aberrant) dans la carte « Allure max ».
  const speedMax = useMemo(
    () => (speedData.length ? Math.max(...speedData.map(p => p.speed)) : 0),
    [speedData]
  )

  // Domaine Y du graphe vitesse : plancher au 5e centile — une pause
  // (vitesse ≈ 0, allure 28:00+/km) écrasait toute la courbe utile. Les creux
  // sous le plancher sont coupés visuellement, le tooltip reste exact.
  const speedDomain = useMemo(() => {
    if (!speedData.length) return ['dataMin', 'dataMax']
    const sorted = speedData.map(p => p.speed).filter(v => v > 0).sort((a, b) => a - b)
    if (!sorted.length) return ['dataMin', 'dataMax']
    const lo = sorted[Math.floor(sorted.length * 0.05)]
    const hi = sorted[sorted.length - 1]
    console.log('[useRunStreams] domaine allure:', fmtPaceFromSpeed(lo), '→', fmtPaceFromSpeed(hi),
      '/km (min réel', fmtPaceFromSpeed(sorted[0]), '/km)')
    return [Math.max(0, lo * 0.95), hi * 1.02]
  }, [speedData])

  const hrData = useMemo(() => {
    if (!streams?.time?.data || !streams?.heartrate?.data) return []
    const time = streams.time.data
    const hr = streams.heartrate.data
    const step = Math.max(1, Math.floor(time.length / samples))
    return time.filter((_, i) => i % step === 0).map((t, idx) => {
      const streamIndex = idx * step
      return {
        streamIndex,
        time: Math.round((t / 60) * 10) / 10,
        hr: hr[streamIndex] || 0,
      }
    })
  }, [streams, samples])

  const activeStreamPoint = useMemo(() =>
    getActiveStreamPoint(streams, activeStreamIndex),
    [streams, activeStreamIndex]
  )
  const activeSpeedPoint = useMemo(() =>
    findNearestStreamDatum(speedData, activeStreamIndex),
    [speedData, activeStreamIndex]
  )
  const activeHrPoint = useMemo(() =>
    findNearestStreamDatum(hrData, activeStreamIndex),
    [hrData, activeStreamIndex]
  )

  const handleChartMouseMove = useCallback(state => {
    const streamIndex = readStreamIndexFromChartState(state)
    if (streamIndex != null) setActiveStreamIndex(streamIndex)
  }, [])

  const clearActiveStreamIndex = useCallback(() => {
    setActiveStreamIndex(null)
  }, [])

  const handleTraceHover = useCallback(({ pointIndex }) => {
    if (Number.isFinite(pointIndex)) setActiveStreamIndex(pointIndex)
  }, [])

  const mapRun = useMemo(() => {
    if (!activity) return []
    const trace = resolveRunTrace(activity, allActivities, streams?.latlng?.data)
    if (!trace) return []
    return [{
      id: activity.id,
      name: activity.name,
      date: activity.start_date_local,
      distance: activity.distance,
      ...trace,
    }]
  }, [activity, allActivities, streams])

  return {
    maxHr, zones, timeInZones, relativeEffort, bestEfforts,
    speedData, speedDomain, speedMax, hrData,
    activeStreamPoint, activeSpeedPoint, activeHrPoint,
    handleChartMouseMove, clearActiveStreamIndex, handleTraceHover,
    mapRun,
  }
}
