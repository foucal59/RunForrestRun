import React, { useMemo, useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react'
import { RotateCcw, ZoomIn, ZoomOut } from 'lucide-react'
import { useActivities } from '../contexts/ActivityContext'
import { fmtPace } from '../lib/compute'

// Mesure la largeur du conteneur (le SVG se dessine en pixels réels pour que
// les points restent parfaitement ronds à toute taille d'écran).
function useContainerWidth() {
  const ref = useRef(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    if (!ref.current) return
    const el = ref.current
    const ro = new ResizeObserver(entries => {
      for (const e of entries) setW(e.contentRect.width)
    })
    ro.observe(el)
    setW(el.clientWidth)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

// Choisit un pas "rond" (en secondes) pour l'axe allure, ~5 graduations.
function paceStep(range) {
  const steps = [15, 30, 60, 120, 300]
  const target = range / 5
  return steps.find(s => s >= target) || 600
}

// Pas "rond" pour l'axe distance (km), ~6 graduations.
function kmStep(max) {
  const steps = [1, 2, 5, 10, 20]
  const target = max / 6
  return steps.find(s => s >= target) || 50
}

function clamp(n, min, max) {
  return Math.min(Math.max(n, min), max)
}

function centeredDomain(fullMin, fullMax, center, zoom) {
  const fullRange = fullMax - fullMin
  if (zoom <= 1 || fullRange <= 0) return [fullMin, fullMax]

  const range = fullRange / zoom
  const clampedCenter = clamp(center, fullMin, fullMax)
  let min = clampedCenter - range / 2
  let max = clampedCenter + range / 2

  if (min < fullMin) {
    max += fullMin - min
    min = fullMin
  }
  if (max > fullMax) {
    min -= max - fullMax
    max = fullMax
  }

  return [Math.max(fullMin, min), Math.min(fullMax, max)]
}

const H = 320
const M = { top: 16, right: 16, bottom: 30, left: 46 }
const COMPACT_H = 270
const COMPACT_M = { top: 12, right: 10, bottom: 28, left: 40 }
const DOT_R = 4
const STAGGER_MS = 420 // durée totale du bloom radial d'entrée
const ZOOM_LEVELS = [1, 1.5, 2, 3, 4]

export default function PaceDistanceScatter() {
  const { activities } = useActivities()
  const [wrapRef, width] = useContainerWidth()
  const [mounted, setMounted] = useState(false)
  const [hover, setHover] = useState(null) // { run, cx, cy }
  const [selectedRunId, setSelectedRunId] = useState(null)
  const [zoomIndex, setZoomIndex] = useState(0)
  const compact = width > 0 && width < 480
  const chartH = compact ? COMPACT_H : H
  const margin = compact ? COMPACT_M : M
  const dotR = compact ? 3.5 : DOT_R
  const zoomLevel = ZOOM_LEVELS[zoomIndex]

  const model = useMemo(() => {
    console.log('[PaceDistanceScatter] computing from', activities.length, 'runs')
    const valid = activities
      .filter(a => a.distance > 500 && a.average_speed > 0)
      .map(a => ({
        id: a.id,
        km: a.distance / 1000,
        pace: 1000 / a.average_speed, // secondes / km
        name: a.name,
        date: a.start_date_local?.slice(0, 10),
        t: a.start_date_local || '',
      }))
    if (!valid.length) return null

    const sorted = [...valid].sort((a, b) => a.t.localeCompare(b.t))
    const lastRun = sorted[sorted.length - 1]
    const latestFirst = [...sorted].reverse()
    const avg = {
      km: valid.reduce((s, r) => s + r.km, 0) / valid.length,
      pace: valid.reduce((s, r) => s + r.pace, 0) / valid.length,
    }
    const maxKm = Math.max(...valid.map(r => r.km))
    const minPace = Math.min(...valid.map(r => r.pace))
    const maxPace = Math.max(...valid.map(r => r.pace))

    // Délai d'entrée radial : le cœur du nuage éclot en premier, les extrêmes
    // ensuite → floraison du centre vers les bords.
    let maxD = 0
    valid.forEach(r => {
      const d = Math.hypot((r.km - avg.km), (r.pace - avg.pace) / 15)
      r._d = d
      if (d > maxD) maxD = d
    })
    valid.forEach(r => { r.delay = maxD ? (r._d / maxD) * STAGGER_MS : 0 })

    return { valid, sorted, latestFirst, lastRun, avg, maxKm, minPace, maxPace }
  }, [activities])

  // Floraison d'entrée : déclenchée une seule fois quand les données arrivent.
  useEffect(() => {
    if (!model || mounted) return
    const id = requestAnimationFrame(() => requestAnimationFrame(() => setMounted(true)))
    return () => cancelAnimationFrame(id)
  }, [model, mounted])

  const selectedRun = useMemo(() => {
    if (!model || selectedRunId == null) return null
    return model.valid.find(r => String(r.id) === String(selectedRunId)) || null
  }, [model, selectedRunId])

  useEffect(() => {
    if (!model || selectedRunId == null || selectedRun) return
    setSelectedRunId(null)
  }, [model, selectedRunId, selectedRun])

  const scales = useMemo(() => {
    if (!model || width <= 0) return null
    const plotW = width - margin.left - margin.right
    const plotH = chartH - margin.top - margin.bottom
    const fullXMin = 0
    const fullXMax = model.maxKm * 1.05
    const pad = Math.max(15, (model.maxPace - model.minPace) * 0.12)
    const fullYMin = Math.max(0, model.minPace - pad)
    const fullYMax = model.maxPace + pad
    const center = selectedRun || model.avg
    const [xMin, xMax] = centeredDomain(fullXMin, fullXMax, center.km, zoomLevel)
    const [yMin, yMax] = centeredDomain(fullYMin, fullYMax, center.pace, zoomLevel)
    const sx = km => margin.left + ((km - xMin) / (xMax - xMin)) * plotW
    const sy = pace => margin.top + ((pace - yMin) / (yMax - yMin)) * plotH
    return { plotW, plotH, xMin, xMax, yMin, yMax, sx, sy }
  }, [model, width, chartH, margin, selectedRun, zoomLevel])

  const ticks = useMemo(() => {
    if (!scales) return { x: [], y: [] }
    const xs = []
    const kStep = kmStep(scales.xMax - scales.xMin)
    const xStart = Math.ceil(scales.xMin / kStep) * kStep
    for (let k = xStart; k <= scales.xMax; k += kStep) xs.push(k)
    const ys = []
    const pStep = paceStep(scales.yMax - scales.yMin)
    const start = Math.ceil(scales.yMin / pStep) * pStep
    for (let p = start; p <= scales.yMax; p += pStep) ys.push(p)
    return { x: xs, y: ys }
  }, [scales])

  const comparisonRunIds = useMemo(() => {
    if (!model || !selectedRun) return new Set()
    return new Set(model.valid
      .filter(r =>
        String(r.id) !== String(selectedRun.id) &&
        r.km >= selectedRun.km &&
        r.pace <= selectedRun.pace
      )
      .map(r => String(r.id)))
  }, [model, selectedRun])

  const selectRun = useCallback(run => {
    setSelectedRunId(run?.id ?? null)
  }, [])

  const zoomIn = useCallback(() => {
    setZoomIndex(index => Math.min(index + 1, ZOOM_LEVELS.length - 1))
  }, [])

  const zoomOut = useCallback(() => {
    setZoomIndex(index => Math.max(index - 1, 0))
  }, [])

  const resetZoom = useCallback(() => {
    setZoomIndex(0)
  }, [])

  if (!model) return null

  const ready = scales != null
  const bottom = chartH - margin.bottom
  const axisFontSize = compact ? 9 : 10
  const selectedPoint = ready && selectedRun
    ? { run: selectedRun, cx: scales.sx(selectedRun.km), cy: scales.sy(selectedRun.pace) }
    : null
  const activePoint = hover || selectedPoint
  const selectedRunValue = selectedRun ? String(selectedRun.id) : ''
  const zoomLabel = `${Number.isInteger(zoomLevel) ? zoomLevel : zoomLevel.toFixed(1)}x`
  const runIsInView = run =>
    ready &&
    run.km >= scales.xMin &&
    run.km <= scales.xMax &&
    run.pace >= scales.yMin &&
    run.pace <= scales.yMax

  return (
    <div className="card pace_distance_scatter" data-name="pace_distance_scatter">
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between pace_distance_scatter_header" data-name="pace_distance_scatter_header">
        <div className="min-w-0">
          <div className="text-sm font-medium text-txt-secondary mb-1 pace_distance_scatter_title" data-name="pace_distance_scatter_title">
            Allure vs Distance
          </div>
          <div className="text-xs text-txt-muted flex flex-wrap items-center gap-x-3 gap-y-1 pace_distance_scatter_subtitle" data-name="pace_distance_scatter_subtitle">
            <span>Chaque course</span>
            <span className="inline-flex items-center gap-1 pace_distance_scatter_legend_last">
              <span className="inline-block w-2.5 h-2.5 rounded-full bg-white border border-slate-400" /> dernière
            </span>
            <span className="inline-flex items-center gap-1 pace_distance_scatter_legend_avg">
              <span className="inline-block w-2.5 h-2.5 rounded-full border-2 border-primary" /> moyenne
            </span>
            {selectedRun && (
              <span className="inline-flex items-center gap-1 pace_distance_scatter_legend_better">
                <span className="inline-block w-2.5 h-2.5 rounded-full bg-rose-500 border border-white" />
                {comparisonRunIds.size} mieux placée{comparisonRunIds.size === 1 ? '' : 's'}
              </span>
            )}
          </div>
        </div>
        <div className="flex w-full flex-col gap-2 sm:w-auto sm:items-end pace_distance_scatter_controls" data-name="pace_distance_scatter_controls">
          <select
            className="w-full sm:w-64 rounded-md border border-surface-border bg-white px-2 py-1.5 text-xs text-txt-secondary focus:outline-none focus:border-primary pace_distance_scatter_select"
            data-name="pace_distance_scatter_select"
            aria-label="Sélectionner une sortie à comparer"
            value={selectedRunValue}
            onChange={event => {
              const id = event.target.value
              setSelectedRunId(id || null)
            }}
          >
            <option value="">Sélectionner une sortie</option>
            {model.latestFirst.map(r => (
              <option key={r.id} value={String(r.id)}>
                {r.date} · {r.name || 'Sortie'} · {r.km.toFixed(1)} km · {fmtPace(r.pace)}/km
              </option>
            ))}
          </select>
          <div className="inline-flex w-max items-center rounded-md border border-surface-border bg-white p-0.5 pace_distance_scatter_zoom" data-name="pace_distance_scatter_zoom">
            <button
              type="button"
              className="p-1.5 rounded text-txt-muted hover:text-txt hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-35 transition-colors pace_distance_scatter_zoom_button"
              data-name="pace_distance_scatter_zoom_out"
              aria-label="Dézoomer"
              title="Dézoomer"
              disabled={zoomIndex === 0}
              onClick={zoomOut}
            >
              <ZoomOut size={14} aria-hidden="true" />
            </button>
            <span className="min-w-[2.4rem] text-center text-[11px] font-mono text-txt-secondary pace_distance_scatter_zoom_label">
              {zoomLabel}
            </span>
            <button
              type="button"
              className="p-1.5 rounded text-txt-muted hover:text-txt hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-35 transition-colors pace_distance_scatter_zoom_button"
              data-name="pace_distance_scatter_zoom_in"
              aria-label="Zoomer"
              title="Zoomer"
              disabled={zoomIndex === ZOOM_LEVELS.length - 1}
              onClick={zoomIn}
            >
              <ZoomIn size={14} aria-hidden="true" />
            </button>
            <button
              type="button"
              className="ml-0.5 p-1.5 rounded text-txt-muted hover:text-txt hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-35 transition-colors pace_distance_scatter_zoom_button"
              data-name="pace_distance_scatter_zoom_reset"
              aria-label="Réinitialiser le zoom"
              title="Réinitialiser le zoom"
              disabled={zoomIndex === 0}
              onClick={resetZoom}
            >
              <RotateCcw size={14} aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      <div ref={wrapRef} className="relative pace_distance_scatter_plot" data-name="pace_distance_scatter_plot" style={{ height: chartH }}>
        {ready && (
          <svg width={width} height={chartH} className="block pace_distance_scatter_svg" onMouseLeave={() => setHover(null)}>
            {/* Grille + graduations */}
            {ticks.y.map(p => (
              <g key={`y${p}`}>
                <line
                  x1={margin.left} x2={width - margin.right}
                  y1={scales.sy(p)} y2={scales.sy(p)}
                  className="pace_scatter_grid" strokeDasharray="3 3"
                />
                <text
                  x={margin.left - 8} y={scales.sy(p)}
                  textAnchor="end" dominantBaseline="central"
                  className="pace_scatter_axis_text" fontSize={axisFontSize}
                >{fmtPace(p)}</text>
              </g>
            ))}
            {ticks.x.map(k => (
              <g key={`x${k}`}>
                <line
                  x1={scales.sx(k)} x2={scales.sx(k)}
                  y1={margin.top} y2={bottom}
                  className="pace_scatter_grid" strokeDasharray="3 3"
                />
                <text
                  x={scales.sx(k)} y={bottom + 16}
                  textAnchor="middle"
                  className="pace_scatter_axis_text" fontSize={axisFontSize}
                >{k}</text>
              </g>
            ))}
            <text
              x={width - margin.right} y={bottom + 16} textAnchor="end"
              className="pace_scatter_axis_text" fontSize={axisFontSize}
            >km</text>

            {/* Sélection + quadrant: plus à droite = au moins aussi long, plus haut = plus rapide. */}
            {selectedPoint && (
              <g className="pace_scatter_selection_guides" pointerEvents="none">
                <rect
                  x={selectedPoint.cx}
                  y={margin.top}
                  width={Math.max(0, width - margin.right - selectedPoint.cx)}
                  height={Math.max(0, selectedPoint.cy - margin.top)}
                  className="pace_scatter_quadrant"
                />
                <line
                  x1={margin.left} x2={width - margin.right}
                  y1={selectedPoint.cy} y2={selectedPoint.cy}
                  className="pace_scatter_reference"
                />
                <line
                  x1={selectedPoint.cx} x2={selectedPoint.cx}
                  y1={margin.top} y2={bottom}
                  className="pace_scatter_reference"
                />
              </g>
            )}

            {/* Nuage de points */}
            {model.valid.filter(runIsInView).map(r => {
              const isSelected = selectedRun && String(r.id) === String(selectedRun.id)
              const isComparisonRun = comparisonRunIds.has(String(r.id))
              return (
                <circle
                  key={r.id}
                  className={[
                    'pace-dot pace_scatter_dot',
                    isComparisonRun ? 'pace_scatter_dot_comparison' : '',
                    isSelected ? 'pace_scatter_dot_selected' : '',
                  ].filter(Boolean).join(' ')}
                  cx={scales.sx(r.km)}
                  cy={scales.sy(r.pace)}
                  r={mounted ? (isSelected ? dotR + 1.5 : dotR) : 0}
                  style={{ animationDelay: `${r.delay}ms` }}
                  onMouseEnter={() => setHover({ run: r, cx: scales.sx(r.km), cy: scales.sy(r.pace) })}
                  onClick={() => selectRun(r)}
                  onTouchStart={() => selectRun(r)}
                />
              )
            })}

            {/* Marqueur moyenne (cercle creux) */}
            {runIsInView(model.avg) && (
              <circle
                className="pace-dot pace_scatter_avg"
                cx={scales.sx(model.avg.km)}
                cy={scales.sy(model.avg.pace)}
                r={mounted ? 8 : 0}
                style={{ animationDelay: `${STAGGER_MS + 40}ms` }}
                fill="none"
              />
            )}
            {/* Marqueur dernière course (point clair) */}
            {runIsInView(model.lastRun) && (
              <circle
                className={[
                  'pace-dot pace_scatter_last',
                  comparisonRunIds.has(String(model.lastRun.id)) ? 'pace_scatter_dot_comparison' : '',
                  selectedRun && String(model.lastRun.id) === String(selectedRun.id) ? 'pace_scatter_dot_selected' : '',
                ].filter(Boolean).join(' ')}
                cx={scales.sx(model.lastRun.km)}
                cy={scales.sy(model.lastRun.pace)}
                r={mounted ? (selectedRun && String(model.lastRun.id) === String(selectedRun.id) ? dotR + 1.5 : 5) : 0}
                style={{ animationDelay: `${STAGGER_MS + 120}ms` }}
                onMouseEnter={() => setHover({ run: model.lastRun, cx: scales.sx(model.lastRun.km), cy: scales.sy(model.lastRun.pace) })}
                onClick={() => selectRun(model.lastRun)}
                onTouchStart={() => selectRun(model.lastRun)}
              />
            )}

            {/* Anneau de sélection persistant */}
            {selectedPoint && (
              <circle
                cx={selectedPoint.cx} cy={selectedPoint.cy} r={dotR + 6}
                className="pace_scatter_selected_ring" pointerEvents="none"
              />
            )}

            {/* Point survolé mis en avant */}
            {hover && (
              <circle
                cx={hover.cx} cy={hover.cy} r={dotR + 3}
                className="pace_scatter_dot_hover" pointerEvents="none"
              />
            )}
          </svg>
        )}

        {/* Tooltip */}
        {activePoint && (
          <div
            className="absolute z-10 pointer-events-none bg-white border border-surface-border rounded-xl px-3 py-2 shadow-lg text-xs pace_distance_scatter_tooltip"
            data-name="pace_distance_scatter_tooltip"
            style={{
              left: Math.min(Math.max(activePoint.cx, 70), width - 70),
              top: Math.max(activePoint.cy - 12, 8),
              transform: 'translate(-50%, -100%)',
            }}
          >
            <div className="font-medium text-txt truncate max-w-[180px]">{activePoint.run.name}</div>
            <div className="text-txt-secondary">{activePoint.run.date}</div>
            <div className="font-mono text-primary">{fmtPace(activePoint.run.pace)}/km · {activePoint.run.km.toFixed(1)} km</div>
          </div>
        )}
      </div>
    </div>
  )
}
