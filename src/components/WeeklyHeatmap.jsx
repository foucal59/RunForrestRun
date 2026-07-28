import React, { useMemo } from 'react'
import { parseLocalDate } from '../lib/compute'
import { useActivities } from '../contexts/ActivityContext'

const BUCKET_COUNT = 26
const WEEKS_PER_BUCKET = 2
const DAYS_PER_BUCKET = WEEKS_PER_BUCKET * 7
const DAY_MS = 86400000
const HEATMAP_PALETTE = [
  '#E7ECFE',
  '#BBCAFC',
  '#8FA8FA',
  '#6386F8',
  '#3764F6',
  '#0936C8',
  '#072A9C',
  '#051E70',
  '#031244',
  '#010718',
]
const HEATMAP_MAX_WEEKLY_KM = 80
const HEATMAP_LEGEND_VALUES = [10, 20, 30, 40, 50, 60, 70, 80]

// Returns first day of ISO week `week` of `year`
function isoWeekStart(year, week) {
  const jan4 = new Date(year, 0, 4)
  const dayOfWeek = (jan4.getDay() + 6) % 7
  const monday = new Date(jan4)
  monday.setDate(jan4.getDate() - dayOfWeek + (week - 1) * 7)
  return monday
}

function startOfDay(d) {
  const date = new Date(d)
  date.setHours(0, 0, 0, 0)
  return date
}

// Returns biweekly bucket index 0-25, aligned with the clicked date range.
function getBucket(d, year = d.getFullYear()) {
  const yearStart = isoWeekStart(year, 1)
  const dayOffset = Math.floor((startOfDay(d) - yearStart) / DAY_MS)
  return Math.max(0, Math.min(BUCKET_COUNT - 1, Math.floor(dayOffset / DAYS_PER_BUCKET)))
}

function getBucketRange(year, bucket) {
  const startWeek = bucket * WEEKS_PER_BUCKET + 1
  const start = isoWeekStart(year, startWeek)
  const end = new Date(start)
  end.setDate(start.getDate() + DAYS_PER_BUCKET - 1)

  const yearStart = new Date(year, 0, 1)
  const yearEnd = new Date(year, 11, 31)
  return {
    start: start < yearStart ? yearStart : start,
    end: end > yearEnd || bucket === BUCKET_COUNT - 1 ? yearEnd : end,
  }
}

const MONTH_LABELS = ['Janv','Fév','Mars','Avr','Mai','Juin','Juil','Août','Sept','Oct','Nov','Déc']

function dominantMonth({ start, end }) {
  const daysByMonth = Array(12).fill(0)
  const d = startOfDay(start)
  const last = startOfDay(end)

  while (d <= last) {
    daysByMonth[d.getMonth()] += 1
    d.setDate(d.getDate() + 1)
  }

  return daysByMonth.reduce(
    (bestMonth, days, month) => days > daysByMonth[bestMonth] ? month : bestMonth,
    0
  )
}

function getMonthLabels(year) {
  const labels = Array(BUCKET_COUNT).fill('')
  const seen = new Set()

  for (let bucket = 0; bucket < BUCKET_COUNT; bucket += 1) {
    const month = dominantMonth(getBucketRange(year, bucket))
    if (!seen.has(month)) {
      labels[bucket] = MONTH_LABELS[month]
      seen.add(month)
    }
  }

  return labels
}

function formatRange(start, end) {
  const opts = { day: 'numeric', month: 'short' }
  return `${start.toLocaleDateString('fr-FR', opts)} - ${end.toLocaleDateString('fr-FR', opts)}`
}

function formatKm(value) {
  return Math.round(value * 10) / 10
}

function hexToRgb(hex) {
  const value = hex.replace('#', '')
  return {
    r: parseInt(value.slice(0, 2), 16),
    g: parseInt(value.slice(2, 4), 16),
    b: parseInt(value.slice(4, 6), 16),
  }
}

function interpolateRgb(fromHex, toHex, t) {
  const from = hexToRgb(fromHex)
  const to = hexToRgb(toHex)
  const r = Math.round(from.r + (to.r - from.r) * t)
  const g = Math.round(from.g + (to.g - from.g) * t)
  const b = Math.round(from.b + (to.b - from.b) * t)
  return `rgb(${r},${g},${b})`
}

function getVolumeColor(weeklyKm) {
  if (!weeklyKm || weeklyKm <= 0) return HEATMAP_PALETTE[0]
  const progress = Math.min(weeklyKm, HEATMAP_MAX_WEEKLY_KM) / HEATMAP_MAX_WEEKLY_KM
  const scaled = progress * (HEATMAP_PALETTE.length - 1)
  const fromIndex = Math.floor(scaled)
  const toIndex = Math.min(fromIndex + 1, HEATMAP_PALETTE.length - 1)
  if (fromIndex === toIndex) return HEATMAP_PALETTE[fromIndex]
  return interpolateRgb(HEATMAP_PALETTE[fromIndex], HEATMAP_PALETTE[toIndex], scaled - fromIndex)
}

export default function WeeklyHeatmap() {
  const { allActivities, setDateRange } = useActivities()

  const { years, data } = useMemo(() => {
    console.log('[WeeklyHeatmap] computing from', allActivities.length, 'activities')
    const byYearBucket = {}
    allActivities.forEach(a => {
      if (!a.start_date_local || !a.distance) return
      const d = parseLocalDate(a.start_date_local)
      const year = d.getFullYear()
      const bucket = getBucket(d, year)
      if (!byYearBucket[year]) byYearBucket[year] = Array(BUCKET_COUNT).fill(0)
      byYearBucket[year][bucket] += a.distance / 1000
    })
    const years = Object.keys(byYearBucket).map(Number).sort()
    return { years, data: byYearBucket }
  }, [allActivities])
  const labelYear = years[years.length - 1] || new Date().getFullYear()
  const monthLabels = useMemo(() => getMonthLabels(labelYear), [labelYear])

  function getCellColor(km) {
    return getVolumeColor(km / WEEKS_PER_BUCKET)
  }

  function handleCellClick(year, bucket) {
    console.log('[WeeklyHeatmap] click year', year, 'bucket', bucket)
    const { start, end } = getBucketRange(year, bucket)
    end.setHours(23, 59, 59, 999)
    setDateRange({ from: start.getTime(), to: end.getTime() })
  }

  if (!years.length) return null

  return (
    <div className="card overflow-x-auto weekly_heatmap" data-name="weekly_heatmap">
      <div className="flex items-center justify-between mb-3 weekly_heatmap_header" data-name="weekly_heatmap_header">
        <div className="text-sm font-medium text-txt-secondary weekly_heatmap_title" data-name="weekly_heatmap_title">Stats hebdo. pour chaque année</div>
        <button
          onClick={() => setDateRange(null)}
          className="text-xs text-txt-muted hover:text-txt px-2 py-0.5 rounded hover:bg-surface-muted transition-colors weekly_heatmap_reset_button"
          data-name="weekly_heatmap_reset_button"
        >
          Tout afficher
        </button>
      </div>
      <div style={{ minWidth: 560 }} className="weekly_heatmap_grid" data-name="weekly_heatmap_grid">
        {/* Month labels */}
        <div className="flex items-center mb-1 pl-9 weekly_heatmap_month_labels" data-name="weekly_heatmap_month_labels">
          {monthLabels.map((label, i) => (
            <div key={i} className="text-[9px] text-txt-muted text-center leading-tight weekly_heatmap_month_label" data-name="weekly_heatmap_month_label" style={{ flex: 1, minWidth: 0 }}>
              {label}
            </div>
          ))}
        </div>
        {/* Rows: one per year, newest at top */}
        {[...years].reverse().map(year => (
          <div key={year} className="flex items-center mb-0.5 weekly_heatmap_row" data-name="weekly_heatmap_row">
            <div className="text-[9px] text-txt-muted text-right pr-1 flex-shrink-0 weekly_heatmap_row_year" data-name="weekly_heatmap_row_year" style={{ width: 34 }}>{year}</div>
            {(data[year] || Array(BUCKET_COUNT).fill(0)).map((km, bucket) => {
              const { start, end } = getBucketRange(year, bucket)
              const weeklyKm = km / WEEKS_PER_BUCKET
              return (
                <div
                  key={bucket}
                  title={`${year} ${formatRange(start, end)}: ${formatKm(km)} km · ${formatKm(weeklyKm)} km/sem.`}
                  onClick={() => km > 0 && handleCellClick(year, bucket)}
                  className={`rounded-sm transition-all weekly_heatmap_cell ${km > 0 ? 'cursor-pointer hover:ring-1 hover:ring-primary/60 hover:scale-110' : ''}`}
                  data-name="weekly_heatmap_cell"
                  style={{
                    flex: 1,
                    height: 13,
                    marginRight: 1.5,
                    backgroundColor: getCellColor(km),
                    boxShadow: km > 0
                      ? 'inset 0 0 0 1px rgba(1, 7, 24, 0.14)'
                      : 'inset 0 0 0 1px rgba(187, 202, 252, 0.55)',
                  }}
                />
              )
            })}
          </div>
        ))}
        {/* Legend */}
        <div className="flex items-center gap-1.5 mt-3 pl-9 weekly_heatmap_legend" data-name="weekly_heatmap_legend">
          <span className="text-[9px] text-txt-muted weekly_heatmap_legend_0_meta" data-name="weekly_heatmap_legend_0_meta">0</span>
          {HEATMAP_LEGEND_VALUES.map(km => (
            <div key={km} className="rounded-sm weekly_heatmap_legend_swatch" data-name="weekly_heatmap_legend_swatch" style={{ width: 14, height: 11, backgroundColor: getVolumeColor(km) }} />
          ))}
          <span className="text-[9px] text-txt-muted weekly_heatmap_legend_max_distance_meta" data-name="weekly_heatmap_legend_max_distance_meta">80+ km/sem.</span>
          <span className="text-[9px] text-txt-muted ml-2 italic weekly_heatmap_legend_cliquer_pour_filtrer_meta" data-name="weekly_heatmap_legend_cliquer_pour_filtrer_meta">Cliquer pour filtrer</span>
        </div>
      </div>
    </div>
  )
}
