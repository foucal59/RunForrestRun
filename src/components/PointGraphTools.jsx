import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { RotateCcw, ZoomIn, ZoomOut } from 'lucide-react'

export const POINT_ZOOM_LEVELS = [1, 1.5, 2, 3, 4]

export function pointId(point, index = 0) {
  if (!point) return ''
  if (point.id != null) return String(point.id)
  if (point.activity_id != null) return String(point.activity_id)
  if (point.date != null) return String(point.date)
  if (point.month != null) return String(point.month)
  return String(index)
}

function isFiniteNumber(value) {
  return Number.isFinite(Number(value))
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max)
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

function valueIsBetter(value, selectedValue, better) {
  if (!isFiniteNumber(value) || !isFiniteNumber(selectedValue)) return false
  if (better === 'higher') return Number(value) >= Number(selectedValue)
  return Number(value) <= Number(selectedValue)
}

export function usePointGraph(data, {
  getId = pointId,
  yKey,
  better = 'lower',
  minVisiblePoints = 10,
} = {}) {
  const [selectedId, setSelectedId] = useState(null)
  const [zoomIndex, setZoomIndex] = useState(0)
  const zoomLevel = POINT_ZOOM_LEVELS[zoomIndex]

  const selected = useMemo(() => {
    if (selectedId == null) return null
    return data.find((point, index) => getId(point, index) === selectedId) || null
  }, [data, getId, selectedId])

  useEffect(() => {
    if (selectedId == null || selected) return
    setSelectedId(null)
  }, [selected, selectedId])

  const selectPoint = useCallback((point, index = 0) => {
    const id = getId(point, index)
    if (!id) return
    setSelectedId(current => current === id ? null : id)
  }, [getId])

  const visibleData = useMemo(() => {
    if (zoomIndex === 0 || data.length <= minVisiblePoints) return data
    const visibleCount = Math.max(minVisiblePoints, Math.ceil(data.length / zoomLevel))
    const selectedIndex = selected
      ? data.findIndex((point, index) => getId(point, index) === selectedId)
      : -1
    const centerIndex = selectedIndex >= 0 ? selectedIndex : data.length - 1
    const half = Math.floor(visibleCount / 2)
    const start = clamp(centerIndex - half, 0, Math.max(0, data.length - visibleCount))
    return data.slice(start, start + visibleCount)
  }, [data, getId, minVisiblePoints, selected, selectedId, zoomIndex, zoomLevel])

  const selectedVisible = useMemo(() => {
    if (!selected) return false
    return visibleData.some((point, index) => getId(point, index) === selectedId)
  }, [getId, selected, selectedId, visibleData])

  const betterIds = useMemo(() => {
    if (!selected || !yKey) return new Set()
    const selectedValue = selected[yKey]
    return new Set(data
      .filter((point, index) =>
        getId(point, index) !== selectedId &&
        valueIsBetter(point?.[yKey], selectedValue, better))
      .map((point, index) => getId(point, index)))
  }, [better, data, getId, selected, selectedId, yKey])

  const zoomIn = useCallback(() => {
    setZoomIndex(index => Math.min(index + 1, POINT_ZOOM_LEVELS.length - 1))
  }, [])

  const zoomOut = useCallback(() => {
    setZoomIndex(index => Math.max(index - 1, 0))
  }, [])

  const resetZoom = useCallback(() => {
    setZoomIndex(0)
  }, [])

  return {
    selected,
    selectedId,
    selectedVisible,
    visibleData,
    betterIds,
    zoomIndex,
    zoomLevel,
    selectPoint,
    zoomIn,
    zoomOut,
    resetZoom,
  }
}

export function PointGraphControls({ graph, className = '' }) {
  const zoomLabel = `${Number.isInteger(graph.zoomLevel) ? graph.zoomLevel : graph.zoomLevel.toFixed(1)}x`
  return (
    <div className={`inline-flex w-max items-center rounded-md border border-surface-border bg-white p-0.5 point_graph_zoom ${className}`} data-name="point_graph_zoom">
      <button
        type="button"
        className="p-1.5 rounded text-txt-muted hover:text-txt hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-35 transition-colors point_graph_zoom_button"
        data-name="point_graph_zoom_out"
        aria-label="Dezoomer"
        title="Dezoomer"
        disabled={graph.zoomIndex === 0}
        onClick={graph.zoomOut}
      >
        <ZoomOut size={14} aria-hidden="true" />
      </button>
      <span className="min-w-[2.4rem] text-center text-[11px] font-mono text-txt-secondary point_graph_zoom_label">
        {zoomLabel}
      </span>
      <button
        type="button"
        className="p-1.5 rounded text-txt-muted hover:text-txt hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-35 transition-colors point_graph_zoom_button"
        data-name="point_graph_zoom_in"
        aria-label="Zoomer"
        title="Zoomer"
        disabled={graph.zoomIndex === POINT_ZOOM_LEVELS.length - 1}
        onClick={graph.zoomIn}
      >
        <ZoomIn size={14} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="ml-0.5 p-1.5 rounded text-txt-muted hover:text-txt hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-35 transition-colors point_graph_zoom_button"
        data-name="point_graph_zoom_reset"
        aria-label="Reinitialiser le zoom"
        title="Reinitialiser le zoom"
        disabled={graph.zoomIndex === 0}
        onClick={graph.resetZoom}
      >
        <RotateCcw size={14} aria-hidden="true" />
      </button>
    </div>
  )
}

function pointColor({ id, selectedId, betterIds, color }) {
  if (id && selectedId === id) return '#f59e0b'
  if (id && betterIds?.has(id)) return '#e11d48'
  return color
}

export function pointDot({
  selectedId,
  betterIds,
  onSelect,
  getId = pointId,
  color,
  radius = 2.5,
}) {
  return function PointDot(props) {
    const { cx, cy, payload, index, value, key } = props
    if (!isFiniteNumber(cx) || !isFiniteNumber(cy) || value == null) return null
    const id = getId(payload, index)
    const selected = id && selectedId === id
    const fill = pointColor({ id, selectedId, betterIds, color })

    return (
      <g
        key={key}
        className="point_graph_dot_group"
        data-name="point_graph_dot"
        onClick={() => onSelect(payload, index)}
        style={{ cursor: 'pointer' }}
      >
        {selected && <circle cx={cx} cy={cy} r={radius + 5} className="point_graph_selected_ring" />}
        <circle
          cx={cx}
          cy={cy}
          r={selected ? radius + 1.5 : radius}
          fill={fill}
          fillOpacity={selected ? 1 : betterIds?.has(id) ? 0.95 : 0.72}
          stroke="#ffffff"
          strokeWidth={selected || betterIds?.has(id) ? 1.6 : 0}
        />
      </g>
    )
  }
}

export function scatterPoint({
  selectedId,
  betterIds,
  onSelect,
  getId = pointId,
  color,
  radius = 4,
}) {
  return function ScatterPoint(props) {
    const { cx, cy, payload, index, key } = props
    if (!isFiniteNumber(cx) || !isFiniteNumber(cy)) return null
    const id = getId(payload, index)
    const selected = id && selectedId === id
    const fill = pointColor({ id, selectedId, betterIds, color })

    return (
      <g
        key={key}
        className="point_graph_dot_group"
        data-name="point_graph_scatter_dot"
        onClick={() => onSelect(payload, index)}
        style={{ cursor: 'pointer' }}
      >
        {selected && <circle cx={cx} cy={cy} r={radius + 6} className="point_graph_selected_ring" />}
        <circle
          cx={cx}
          cy={cy}
          r={selected ? radius + 2 : radius}
          fill={fill}
          fillOpacity={selected ? 1 : betterIds?.has(id) ? 0.95 : 0.58}
          stroke="#ffffff"
          strokeWidth={selected || betterIds?.has(id) ? 1.6 : 0}
        />
      </g>
    )
  }
}

export function numericZoomDomain(data, key, selected, zoomIndex, {
  padRatio = 0.08,
  minPad = 1,
  minValue = null,
} = {}) {
  const values = data.map(point => Number(point?.[key])).filter(Number.isFinite)
  if (!values.length) return ['auto', 'auto']

  const rawMin = Math.min(...values)
  const rawMax = Math.max(...values)
  const spread = Math.max(rawMax - rawMin, minPad)
  const pad = Math.max(spread * padRatio, minPad)
  const fullMin = minValue == null ? rawMin - pad : Math.max(minValue, rawMin - pad)
  const fullMax = rawMax + pad
  const selectedValue = Number(selected?.[key])
  const center = Number.isFinite(selectedValue)
    ? selectedValue
    : values.reduce((sum, value) => sum + value, 0) / values.length

  return centeredDomain(fullMin, fullMax, center, POINT_ZOOM_LEVELS[zoomIndex])
}

export function filterByNumericDomains(data, xKey, yKey, xDomain, yDomain) {
  if (!Array.isArray(xDomain) || !Array.isArray(yDomain)) return data
  if (!Number.isFinite(xDomain[0]) || !Number.isFinite(xDomain[1])) return data
  if (!Number.isFinite(yDomain[0]) || !Number.isFinite(yDomain[1])) return data

  return data.filter(point => {
    const x = Number(point?.[xKey])
    const y = Number(point?.[yKey])
    return Number.isFinite(x) && Number.isFinite(y) &&
      x >= xDomain[0] && x <= xDomain[1] &&
      y >= yDomain[0] && y <= yDomain[1]
  })
}
