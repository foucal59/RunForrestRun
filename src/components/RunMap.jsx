import React, { useEffect, useRef, useMemo } from 'react'

function decodePolyline(str) {
  const points = []
  let index = 0, lat = 0, lng = 0
  while (index < str.length) {
    let b, shift = 0, result = 0
    do { b = str.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5 } while (b >= 0x20)
    lat += (result & 1) ? ~(result >> 1) : (result >> 1)
    shift = 0; result = 0
    do { b = str.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5 } while (b >= 0x20)
    lng += (result & 1) ? ~(result >> 1) : (result >> 1)
    points.push([lat / 1e5, lng / 1e5])
  }
  return points
}

// Downsample a trace so a long GPS stream (tens of thousands of 1 Hz points)
// stays light on the map. Also avoids spreading a giant array as call args.
function thinWithIndex(points, max = 3000) {
  if (!Array.isArray(points)) return []
  const step = Math.ceil(points.length / max)
  const out = []
  for (let i = 0; i < points.length; i += step) out.push({ point: points[i], index: i })
  return out
}

function nearestIndexedPoint(latlng, indexedPoints) {
  if (!latlng || !indexedPoints?.length) return null
  let best = null
  let bestDistance = Number.POSITIVE_INFINITY
  const lngScale = Math.cos((latlng.lat * Math.PI) / 180)

  for (const item of indexedPoints) {
    const [lat, lng] = item.point
    const dLat = lat - latlng.lat
    const dLng = (lng - latlng.lng) * lngScale
    const distance = dLat * dLat + dLng * dLng
    if (distance < bestDistance) {
      bestDistance = distance
      best = item
    }
  }

  return best
}

function normalizeLatLng(point) {
  if (!Array.isArray(point) || point.length !== 2) return null
  if (!Number.isFinite(point[0]) || !Number.isFinite(point[1])) return null
  if (point[0] < -90 || point[0] > 90 || point[1] < -180 || point[1] > 180) return null
  return point
}

function highlightedWeightForZoom(zoom) {
  if (!Number.isFinite(zoom)) return 5
  if (zoom <= 10) return 7
  if (zoom <= 12) return 6
  if (zoom <= 14) return 5
  return 4
}

function runIdsKey(ids) {
  return Array.isArray(ids) && ids.length ? ids.map(id => String(id)).join('|') : ''
}

function safeRemoveLayer(layer) {
  if (!layer) return
  try {
    layer.remove()
  } catch (e) {
    console.warn('[RunMap] layer cleanup skipped:', e?.message || e)
  }
}

function safeRemoveMap(map, container) {
  if (!map) return
  try {
    map.off()
    map.remove()
  } catch (e) {
    console.warn('[RunMap] map cleanup skipped:', e?.message || e)
  } finally {
    if (container) {
      try { container._leaflet_id = null } catch {}
      try { container.innerHTML = '' } catch {}
    }
  }
}

function safeBringToFront(layer) {
  if (!layer?._path?.parentNode) return
  try {
    layer.bringToFront()
  } catch (e) {
    console.warn('[RunMap] bringToFront skipped:', e?.message || e)
  }
}

/**
 * RunMap — renders GPS traces on a Leaflet map.
 *
 * Props:
 *   runs        — array of { id, name, date, distanceKm, pace, prCount?,
 *                            polyline OR points }. `polyline` is an encoded
 *                            string; `points` is a ready [[lat,lng], …] array
 *                            (used for Garmin runs that have no stored polyline
 *                            but do have GPS stream coordinates).
 *   height      — map height in px (default 400)
 *   singleRun   — if true, renders a single trace in blue with no interactivity chrome
 *   className   — extra CSS classes
 *   onRunClick  — optional callback (runId: number) called when a trace is clicked
 *   activePoint — optional [lat,lng] marker shown on top of the trace
 *   fitRunIds   — optional run ids used for the initial viewport; all runs are
 *                 still rendered, but the map centers on this subset
 *   highlightRunIds — optional run ids emphasized with a thicker trace
 *   flush       — if true, removes the map's own border/radius so it fills a card
 */
export default function RunMap({ runs, height = 400, singleRun = false, className = '', onRunClick, activePoint = null, onTraceHover, onTraceLeave, fitRunIds = null, highlightRunIds = null, flush = false }) {
  const mapRef = useRef(null)
  const mapInstanceRef = useRef(null)
  const activeMarkerRef = useRef(null)
  const resizeObserverRef = useRef(null)
  // Store onRunClick in a ref so the Leaflet map never reinitializes just because the parent re-renders with a new callback reference
  const onRunClickRef = useRef(onRunClick)
  const onTraceHoverRef = useRef(onTraceHover)
  const onTraceLeaveRef = useRef(onTraceLeave)
  const activePointRef = useRef(activePoint)
  useEffect(() => { onRunClickRef.current = onRunClick }, [onRunClick])
  useEffect(() => { onTraceHoverRef.current = onTraceHover }, [onTraceHover])
  useEffect(() => { onTraceLeaveRef.current = onTraceLeave }, [onTraceLeave])

  const fitRunIdsKey = runIdsKey(fitRunIds)
  const highlightRunIdsKey = runIdsKey(highlightRunIds)
  const fitRunIdSet = useMemo(() => {
    if (!fitRunIdsKey) return null
    return new Set(fitRunIdsKey.split('|'))
  }, [fitRunIdsKey])
  const highlightRunIdSet = useMemo(() => {
    if (!highlightRunIdsKey) return null
    return new Set(highlightRunIdsKey.split('|'))
  }, [highlightRunIdsKey])

  function syncActiveMarker(point) {
    const map = mapInstanceRef.current
    const L = typeof window !== 'undefined' ? window.L : null
    const latlng = normalizeLatLng(point)

    if (!map || !L || !latlng) {
      if (activeMarkerRef.current) {
        safeRemoveLayer(activeMarkerRef.current)
        activeMarkerRef.current = null
      }
      return
    }

    if (!activeMarkerRef.current) {
      activeMarkerRef.current = L.circleMarker(latlng, {
        radius: 6,
        color: '#ffffff',
        weight: 2,
        fillColor: '#2563EB',
        fillOpacity: 1,
        opacity: 1,
        interactive: false,
      }).addTo(map)
    } else {
      activeMarkerRef.current.setLatLng(latlng)
    }
    activeMarkerRef.current.bringToFront?.()
  }

  useEffect(() => {
    activePointRef.current = activePoint
    syncActiveMarker(activePoint)
  }, [activePoint])

  const decodedRuns = useMemo(() =>
    (runs || []).map(r => {
      const raw = Array.isArray(r.points) && r.points.length
        ? r.points
        : (r.polyline ? decodePolyline(r.polyline) : [])
      // Drop NaN / out-of-range coords: one bad trace would otherwise make
      // L.latLngBounds invalid and fitBounds a no-op → gray, unzoomed map.
      const points = raw.filter(p =>
        Array.isArray(p) && p.length === 2 &&
        Number.isFinite(p[0]) && Number.isFinite(p[1]) &&
        p[0] >= -90 && p[0] <= 90 && p[1] >= -180 && p[1] <= 180
      )
      return { ...r, points }
    }).filter(r => r.points.length > 1),
    [runs]
  )

  useEffect(() => {
    if (!mapRef.current || decodedRuns.length === 0) return
    if (typeof window === 'undefined') return
    let cancelled = false
    const container = mapRef.current

    const initMap = async () => {
      if (!window.L) {
        if (!document.getElementById('leaflet-css')) {
          const link = document.createElement('link')
          link.id = 'leaflet-css'
          link.rel = 'stylesheet'
          link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css'
          document.head.appendChild(link)
        }
        await new Promise((resolve, reject) => {
          if (window.L) { resolve(); return }
          const script = document.createElement('script')
          script.src = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'
          script.onload = resolve
          script.onerror = reject
          document.head.appendChild(script)
        })
      }

      // A re-render or React StrictMode's double-invoke may have torn this
      // effect down (or started another init) while the script was loading.
      if (cancelled || !container) return

      const L = window.L

      // Drop any map we own, then bail if a racing init still owns the
      // container — otherwise L.map() throws "Map container is already initialized".
      if (mapInstanceRef.current) {
        safeRemoveMap(mapInstanceRef.current, container)
        mapInstanceRef.current = null
      }
      if (container._leaflet_id != null) {
        try { container._leaflet_id = null } catch {}
        try { container.innerHTML = '' } catch {}
      }

      const map = L.map(container, {
        zoomControl: !singleRun,
        attributionControl: false,
        scrollWheelZoom: !singleRun,
      })
      if (cancelled) { safeRemoveMap(map, container); return }

      L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19,
      }).addTo(map)

      const allBounds = []
      const fitBounds = []
      const highlightedLayers = []
      const total = decodedRuns.length

      decodedRuns.forEach((run, i) => {
        const ratio = total > 1 ? i / (total - 1) : 0
        const highlighted = !!highlightRunIdSet?.has(String(run.id))
        const hue = Math.round(ratio * 220)
        const sat = Math.round(90 - ratio * 30)
        const light = Math.round(50 + ratio * 20)
        const color = singleRun ? '#2563EB' : `hsl(${hue},${sat}%,${light}%)`
        const opacity = singleRun ? 0.9 : (highlighted ? 0.95 : 0.86 - ratio * 0.16)
        const weight = highlighted ? highlightedWeightForZoom(map.getZoom()) : (singleRun ? 3.5 : 2.6)

        const indexedPts = thinWithIndex(run.points)
        const pts = indexedPts.map(item => item.point)
        const halo = !singleRun ? L.polyline(pts, {
          color: highlighted ? '#111827' : '#ffffff',
          weight: weight + (highlighted ? 4 : 2.5),
          opacity: highlighted ? 0.18 : 0.7,
          smoothFactor: 1,
          interactive: false,
        }).addTo(map) : null
        const polyline = L.polyline(pts, {
          color,
          weight,
          opacity,
          smoothFactor: 1
        }).addTo(map)

        // Keep geographically isolated runs visible when the selected range
        // forces a wide viewport: the route may become tiny, but this marker
        // keeps a stable on-screen footprint at every zoom level.
        if (!singleRun) {
          L.circleMarker(pts[Math.floor(pts.length / 2)], {
            radius: highlighted ? 3.5 : 3,
            color: '#ffffff',
            weight: 1.5,
            fillColor: color,
            fillOpacity: 1,
            interactive: false,
          }).addTo(map)
        }

        if (highlighted) highlightedLayers.push({ halo, polyline })

        if (!singleRun) {
          const prBadge = run.prCount > 0
            ? `<div style="margin-top:4px;color:#6366F1;font-weight:600;font-size:11px;">🏆 ${run.prCount} PR</div>`
            : ''

          const hasClick = !!onRunClickRef.current
          const popupHtml = `
            <div style="font-family: Inter, sans-serif; color: #1e293b; cursor: ${hasClick ? 'pointer' : 'default'};">
              <div style="font-weight: 600; margin-bottom: 4px;">${run.name || 'Run'}</div>
              <div style="font-size: 12px; color: #64748b;">${run.date?.slice(0, 10)} | ${run.distanceKm} km | ${run.pace}/km</div>
              ${prBadge}
              ${hasClick ? '<div style="margin-top:6px;font-size:11px;color:#2563EB;">Cliquer pour ouvrir →</div>' : ''}
            </div>
          `

          polyline.bindPopup(popupHtml, { className: 'light-popup' })

          polyline.on('click', () => {
            console.log('[RunMap] run clicked:', run.id)
            onRunClickRef.current?.(run.id)
          })
        }

        if (onTraceHoverRef.current || onTraceLeaveRef.current) {
          polyline.on('mousemove', e => {
            const nearest = nearestIndexedPoint(e.latlng, indexedPts)
            if (!nearest) return
            onTraceHoverRef.current?.({
              runId: run.id,
              point: nearest.point,
              pointIndex: nearest.index,
            })
          })
          polyline.on('mouseout', () => onTraceLeaveRef.current?.(run.id))
        }

        // Accumulate bounds without spreading (`push(...huge)` overflows the stack).
        const useForInitialFit = !fitRunIdSet || fitRunIdSet.has(String(run.id))
        for (let k = 0; k < pts.length; k++) {
          allBounds.push(pts[k])
          if (useForInitialFit) fitBounds.push(pts[k])
        }
      })

      mapInstanceRef.current = map
      syncActiveMarker(activePointRef.current)

      function updateHighlightStyles() {
        if (!highlightedLayers.length) return
        const nextWeight = highlightedWeightForZoom(map.getZoom())
        highlightedLayers.forEach(({ halo, polyline }) => {
          halo?.setStyle({ weight: nextWeight + 4 })
          polyline.setStyle({ weight: nextWeight })
          safeBringToFront(halo)
          safeBringToFront(polyline)
        })
      }

      map.on('zoomend', updateHighlightStyles)

      // Leaflet measures the container at L.map() time. This card lives in a CSS
      // grid that lays out *after* the async Leaflet script loads, so the map is
      // often born 0×0 → only the zoom-0 world tile loads (gray map) and
      // fitBounds runs against a null size (traces off-screen). Recompute the
      // size on the next frame, fit, then keep it in sync if the container
      // resizes (responsive grid, sidebar toggle, dark-mode reflow…).
      requestAnimationFrame(() => {
        if (cancelled || mapInstanceRef.current !== map) return
        map.invalidateSize()
        const initialBounds = fitBounds.length > 0 ? fitBounds : allBounds
        if (initialBounds.length > 0) {
          const bounds = L.latLngBounds(initialBounds)
          if (bounds.isValid()) map.fitBounds(bounds, { padding: [18, 18] })
          updateHighlightStyles()
        }
      })

      if (typeof ResizeObserver !== 'undefined') {
        const ro = new ResizeObserver(() => {
          if (mapInstanceRef.current === map) map.invalidateSize()
        })
        ro.observe(container)
        resizeObserverRef.current = ro
      }
    }

    initMap()

    return () => {
      cancelled = true
      if (resizeObserverRef.current) {
        resizeObserverRef.current.disconnect()
        resizeObserverRef.current = null
      }
      if (activeMarkerRef.current) {
        safeRemoveLayer(activeMarkerRef.current)
        activeMarkerRef.current = null
      }
      if (mapInstanceRef.current) {
        safeRemoveMap(mapInstanceRef.current, container)
        mapInstanceRef.current = null
      }
    }
  }, [decodedRuns, singleRun, fitRunIdSet, highlightRunIdSet])

  if (!decodedRuns.length) {
    return (
      <div className={`flex items-center justify-center bg-surface-muted ${flush ? '' : 'rounded-xl'} run_map_empty ${className}`} data-name="run_map_empty" style={{ height }}>
        <span className="text-txt-muted text-sm run_map_empty_message" data-name="run_map_empty_message">Aucune trace GPS disponible</span>
      </div>
    )
  }

  const frameClass = flush
    ? 'overflow-hidden relative z-0 run_map'
    : 'rounded-xl overflow-hidden border border-surface-border relative z-0 run_map'

  return <div ref={mapRef} className={`${frameClass} ${className}`} data-name="run_map" style={{ height }} />
}
