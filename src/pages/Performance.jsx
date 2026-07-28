import React, { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar, Cell, ReferenceLine } from 'recharts'
import { useActivities } from '../contexts/ActivityContext'
import { computeBestByYear, computeProjections, parseLocalDate, fmtTime as ft, paceForDist as paceStr } from '../lib/compute'
import ChartCard from '../components/ChartCard'
import Loader from '../components/Loader'
import { useNow } from '../lib/clock'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle, COLORS } from '../lib/chartTheme'
import { PointGraphControls, pointDot, usePointGraph } from '../components/PointGraphTools'

const DC = { '5k': COLORS.brand, '10k': '#3b82f6', 'semi': '#10b981', 'marathon': '#8b5cf6' }
const DL = { '5k': '5 km', '10k': '10 km', 'semi': 'Semi-marathon', 'marathon': 'Marathon' }
const RIEGEL_TARGETS = ['10k', 'semi', 'marathon']

function deltaLabel(seconds) {
  if (seconds == null) return ''
  if (seconds === 0) return 'aligné record'
  const sign = seconds > 0 ? '+' : '-'
  return `${sign}${ft(Math.abs(seconds))} vs record`
}

function PTip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  const dateLabel = d?.runDate?.slice(0, 10) || d?.date?.slice(0, 10)
  return (
    <div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg performance_tooltip" data-name="performance_tooltip">
      <div className="text-xs text-txt-secondary font-medium performance_tooltip_slice_label" data-name="performance_tooltip_slice_label">{dateLabel}</div>
      {d?.name && <div className="text-sm text-txt font-medium mt-1 performance_tooltip_name" data-name="performance_tooltip_name">{d.name}</div>}
      <div className="text-sm text-txt font-mono mt-1 performance_tooltip_formatted_value" data-name="performance_tooltip_formatted_value">{d?.formatted || ft(d?.time)}</div>
      {d?.pace && <div className="text-xs text-txt-muted mt-0.5 performance_tooltip_pace_meta" data-name="performance_tooltip_pace_meta">{d.pace}</div>}
    </div>
  )
}

function ProgressBar({ progress }) {
  if (!progress) return null
  const pct = progress.total > 0 ? Math.round((progress.fetched / progress.total) * 100) : 0
  return (
    <div className="card mb-6 performance_progress_bar" data-name="performance_progress_bar">
      <div className="flex items-center justify-between mb-2 performance_progress_bar_header" data-name="performance_progress_bar_header">
        <span className="text-sm text-txt-secondary font-medium performance_progress_bar_header_chargement_des_courses_label" data-name="performance_progress_bar_header_chargement_des_courses_label">
          Chargement des courses...
        </span>
        <span className="text-xs text-txt-muted font-mono performance_progress_bar_header_fetched_total_computed_value" data-name="performance_progress_bar_header_fetched_total_computed_value">
          {progress.fetched}/{progress.total} ({progress.computed || 0} records calculés)
        </span>
      </div>
      <div className="w-full h-2 bg-surface-border rounded-full overflow-hidden performance_progress_bar_section" data-name="performance_progress_bar_section">
        <div
          className="h-full bg-gradient-to-r from-primary to-primary-light rounded-full transition-all duration-500 performance_progress_bar_fill" data-name="performance_progress_bar_fill"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function PerformanceMonthlyPointChart({ dt, data }) {
  const graph = usePointGraph(data, { yKey: 'time', better: 'lower', minVisiblePoints: 10 })
  const color = DC[dt]

  return (
    <ChartCard title="Evolution mensuelle" name={`performance_monthly_evolution_${dt}`}>
      <PointGraphControls graph={graph} className="mb-2" />
      <ResponsiveContainer width="100%" height={250}>
        <AreaChart data={graph.visibleData}>
          <defs>
            <linearGradient id={`gradPerf_${dt}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.25} />
              <stop offset="100%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid {...gridStyle} />
          <XAxis dataKey="date" tick={axisStyle} tickFormatter={d => d?.slice(0, 10)} />
          <YAxis tick={axisStyle} tickFormatter={ft} domain={['dataMin - 60', 'dataMax + 60']} reversed />
          <Tooltip content={<PTip />} />
          {graph.selectedVisible && graph.selected?.time != null && (
            <>
              <ReferenceLine x={graph.selected.date} stroke="#f59e0b" strokeDasharray="5 4" />
              <ReferenceLine y={graph.selected.time} stroke="#f59e0b" strokeDasharray="5 4" />
            </>
          )}
          <Area
            type="monotone"
            dataKey="time"
            stroke={color}
            strokeWidth={2}
            fill={`url(#gradPerf_${dt})`}
            dot={pointDot({
              selectedId: graph.selectedId,
              betterIds: graph.betterIds,
              onSelect: graph.selectPoint,
              color,
              radius: 3,
            })}
            activeDot={false}
            connectNulls
            animationDuration={1000}
          />
        </AreaChart>
      </ResponsiveContainer>
    </ChartCard>
  )
}

export default function Performance() {
  const { activities, loading, computedPRs, backfillStatus, effectiveDateRange } = useActivities()
  const [, setSearchParams] = useSearchParams()
  const now = useNow()
  const backfillActivityCount = Number(backfillStatus?.activityCount) || 0
  const backfillDetailsCount = Number(backfillStatus?.detailsCount) || 0
  const detailsFetchedForAllRuns = backfillActivityCount > 0 && backfillDetailsCount >= backfillActivityCount
  const detailsBackfillComplete = backfillStatus?.detailsComplete !== false || detailsFetchedForAllRuns

  // Build records from computed PRs, filtered by the resolved date range.
  // The context resolves presets against the (possibly simulated) current
  // date so that "90j" remains a sliding window when rewinding.
  const records = useMemo(() => {
    console.log('[Performance] building records, range=', effectiveDateRange, 'prs=', Object.values(computedPRs).reduce((s, a) => s + a.length, 0))
    const result = {}
    for (const [distType, bests] of Object.entries(computedPRs)) {
      const filtered = bests.filter(b => {
        if (!effectiveDateRange) return true
        const t = parseLocalDate(b.startDate).getTime()
        return t >= effectiveDateRange.from && t <= effectiveDateRange.to
      })
      const sorted = [...filtered].sort((a, b) => a.timeSeconds - b.timeSeconds)
      result[distType] = sorted.map((b, i) => ({
        date: b.startDate,
        time: Math.round(b.timeSeconds),
        activity_id: b.activityId,
        name: b.name,
        distance: b.distance,
        formatted: ft(Math.round(b.timeSeconds)),
        pace: paceStr(b.timeSeconds, distType),
        polyline: b.polyline,
        isBest: i === 0,
        pctOffBest: i === 0 ? 0 : Math.round(((b.timeSeconds - sorted[0].timeSeconds) / sorted[0].timeSeconds) * 1000) / 10,
      }))
    }
    return result
  }, [computedPRs, effectiveDateRange])

  // Compute 90-day records (best time per distance in last 90 days)
  const records90d = useMemo(() => {
    const cutoff = now - 90 * 86400000
    const result = {}
    for (const [distType, bests] of Object.entries(computedPRs)) {
      const filtered = bests.filter(b => {
        const t = parseLocalDate(b.startDate).getTime()
        return t >= cutoff && t <= now
      })
      const sorted = [...filtered].sort((a, b) => a.timeSeconds - b.timeSeconds)
      if (sorted.length > 0) {
        const b = sorted[0]
        result[distType] = {
          date: b.startDate,
          time: Math.round(b.timeSeconds),
          activity_id: b.activityId,
          formatted: ft(Math.round(b.timeSeconds)),
          pace: paceStr(b.timeSeconds, distType),
        }
      }
    }
    return result
  }, [computedPRs, now])

  // All-time records (not filtered by date range)
  const allTimeRecords = useMemo(() => {
    const result = {}
    for (const [distType, bests] of Object.entries(computedPRs)) {
      const sorted = [...bests].sort((a, b) => a.timeSeconds - b.timeSeconds)
      if (sorted.length > 0) {
        const b = sorted[0]
        result[distType] = {
          date: b.startDate,
          time: Math.round(b.timeSeconds),
          activity_id: b.activityId,
          formatted: ft(Math.round(b.timeSeconds)),
          pace: paceStr(b.timeSeconds, distType),
        }
      }
    }
    return result
  }, [computedPRs])

  const bestByYear = useMemo(() => computeBestByYear(records), [records])
  const projData = useMemo(() => computeProjections(records, activities), [records, activities])

  const recordsChrono = useMemo(() => {
    const result = {}
    Object.entries(records).forEach(([dt, recs]) => {
      result[dt] = [...recs].sort((a, b) => a.date.localeCompare(b.date))
    })
    return result
  }, [records])

  const monthlyEvolution = useMemo(() => {
    const result = {}
    Object.entries(recordsChrono).forEach(([distType, recs]) => {
      const monthMap = {}
      recs.forEach(r => {
        const d = parseLocalDate(r.date)
        const monthKey = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
        if (!monthMap[monthKey] || (r.time || Infinity) < monthMap[monthKey].time) {
          monthMap[monthKey] = r
        }
      })

      const allMonths = Object.entries(monthMap)
        .map(([month, r]) => ({
          month,
          date: month,
          runDate: r.date,
          time: r.time,
          activity_id: r.activity_id,
          name: r.name,
          formatted: r.formatted,
          pace: r.pace,
        }))
        .sort((a, b) => a.month.localeCompare(b.month))

      if (allMonths.length > 0) {
        const [firstYear, firstMonth] = allMonths[0].month.split('-').map(Number)
        const [lastYear, lastMonth] = allMonths[allMonths.length - 1].month.split('-').map(Number)
        const density = {}
        allMonths.forEach(m => { density[m.month] = m })

        const filled = []
        let cur = new Date(firstYear, firstMonth - 1, 1)
        const end = new Date(lastYear, lastMonth - 1, 1)
        while (cur <= end) {
          const k = `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, '0')}`
          filled.push(density[k] ? { ...density[k], date: k } : { date: k, time: null })
          cur.setMonth(cur.getMonth() + 1)
        }

        result[distType] = filled
      } else {
        result[distType] = []
      }
    })
    return result
  }, [recordsChrono])

  if (loading) return <Loader />

  return (
    <div data-name="page_performance">
      <h2 className="text-xl font-semibold mb-4 sm:mb-6 performance_header" data-name="performance_header">Performance & Records</h2>

      {/* Progress bar for details backfill */}
      {backfillStatus && !detailsBackfillComplete && backfillActivityCount > 0 && (
        <ProgressBar progress={{
          fetched: backfillDetailsCount,
          total: backfillActivityCount,
          computed: Object.values(computedPRs).reduce((s, a) => s + a.length, 0),
        }} />
      )}

      {/* Best times cards: all-time + 90-day */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mb-6 sm:mb-8 performance_records_grid" data-name="performance_records_grid">
        {Object.entries(DL).map(([k, l]) => {
          const best = allTimeRecords[k]
          const best90 = records90d[k]
          return (
            <div key={k} className={`card group performance_record_${k} performance_record`} data-name={`performance_record_${k}`}>
              <div className="text-xs text-txt-muted uppercase tracking-wider performance_record_l_label" data-name="performance_record_l_label">{l}</div>
              {/* All-time record */}
              <div
                onClick={() => best?.activity_id && setSearchParams({ run: best.activity_id })}
                className={`${best?.activity_id ? 'cursor-pointer' : ''} performance_record_${k}_alltime performance_record_alltime`} data-name={`performance_record_${k}_alltime`}
              >
                <div className="text-2xl font-mono font-semibold text-txt mt-1 performance_record_alltime_formatted_value" data-name="performance_record_alltime_formatted_value">{best ? best.formatted : '-'}</div>
                {best && <div className="text-xs text-txt-muted mt-0.5 performance_record_alltime_pace_slice_meta" data-name="performance_record_alltime_pace_slice_meta">{best.pace} | {best.date?.slice(0,10)}</div>}
              </div>
              {/* 90-day record */}
              <div className="mt-2 pt-2 border-t border-surface-border performance_record_90d_block" data-name="performance_record_90d_block">
                <div className="text-[10px] text-txt-muted uppercase performance_record_90d_block_90_jours_label" data-name="performance_record_90d_block_90_jours_label">90 jours</div>
                <div
                  onClick={() => best90?.activity_id && setSearchParams({ run: best90.activity_id })}
                  className={`${best90?.activity_id ? 'cursor-pointer' : ''} performance_record_${k}_90d performance_record_90d`} data-name={`performance_record_${k}_90d`}
                >
                  <div className="text-lg font-mono font-semibold text-txt-secondary performance_record_90d_formatted_value" data-name="performance_record_90d_formatted_value">{best90 ? best90.formatted : '-'}</div>
                  {best90 && <div className="text-[10px] text-txt-muted performance_record_90d_pace_slice_meta" data-name="performance_record_90d_pace_slice_meta">{best90.pace} | {best90.date?.slice(0,10)}</div>}
                </div>
              </div>
              <div className="h-0.5 mt-3 rounded-full transition-all duration-300 group-hover:w-full w-0 performance_record_section" data-name="performance_record_section" style={{ backgroundColor: DC[k] }} />
            </div>
          )
        })}
      </div>

      {/* Projections Riegel */}
      {projData?.current && Object.keys(projData.current).length > 0 && (
        <div className="card mb-6 sm:mb-8 performance_projections_card" data-name="performance_projections_card">
          <div className="flex items-start justify-between gap-3 mb-4 performance_projections_header" data-name="performance_projections_header">
            <div>
              <h3 className="text-sm font-medium text-txt-secondary performance_projections_title" data-name="performance_projections_title">Projections Riegel</h3>
              <p className="text-xs text-txt-muted mt-1 performance_projections_subtitle" data-name="performance_projections_subtitle">10 km, semi et marathon depuis le meilleur effort récent (90 j) — semi &gt; 10 km &gt; marathon &gt; 5 km.</p>
            </div>
            <div className={`text-[10px] px-2 py-1 rounded-lg font-medium performance_projection_confidence performance_projection_confidence_${projData.confidence}`} data-name="performance_projection_confidence">
              {projData.volume_90d_km} km / 90j
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 performance_projections_grid" data-name="performance_projections_grid">
            {RIEGEL_TARGETS.map(key => [key, projData.current[key]]).filter(([, val]) => val).map(([key, val]) => (
              <div key={key} className="bg-surface-muted rounded-lg p-3 performance_projection_item" data-name={`performance_projection_item_${key}`}>
                <div className="flex items-center justify-between gap-2 performance_projection_item_header" data-name="performance_projection_item_header">
                  <div className="text-xs text-txt-muted uppercase tracking-wider performance_projection_item_target_label" data-name="performance_projection_item_target_label">
                    {DL[key]}
                  </div>
                  <span className="w-2 h-2 rounded-full performance_projection_item_target_dot" data-name="performance_projection_item_target_dot" style={{ backgroundColor: DC[key] }} />
                </div>
                <div className="text-xl font-mono font-semibold text-txt mt-1 performance_projection_item_formatted_value" data-name="performance_projection_item_formatted_value">{val.formatted}</div>
                <div className="text-xs text-txt-secondary font-mono mt-0.5 performance_projection_item_pace" data-name="performance_projection_item_pace">{val.pace}</div>
                <div className="text-xs text-txt-muted mt-2 performance_projection_item_base_meta" data-name="performance_projection_item_base_meta">
                  Base: {val.source_label || DL[val.source_distance] || val.source_distance} en {val.source_time}
                  {val.source_date ? ` (${val.source_date})` : ''}
                </div>
                {val.actual_formatted && (
                  <div className={`text-[10px] mt-1 performance_projection_item_delta ${val.delta_seconds <= 0 ? 'text-emerald-600' : 'text-txt-muted'}`} data-name="performance_projection_item_delta">
                    Record: {val.actual_formatted} · {deltaLabel(val.delta_seconds)}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Evolution + Meilleur temps on SAME ROW per distance */}
      {Object.entries(DL).map(([dt, label]) => {
        const chrono = recordsChrono[dt]
        const years = bestByYear[dt]
        if (!chrono?.length && !years?.length) return null
        return (
          <div key={dt} className={`mb-6 performance_distance_block performance_distance_block_${dt}`} data-name={`performance_distance_block_${dt}`}>
            <h3 className="text-sm font-semibold text-txt-secondary mb-3 performance_distance_title" data-name="performance_distance_title">{label}</h3>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 performance_distance_block_grid" data-name="performance_distance_block_grid">
              {monthlyEvolution?.[dt]?.length > 0 && (
                <PerformanceMonthlyPointChart dt={dt} data={monthlyEvolution[dt]} />
              )}
              {years?.length > 0 && (
                <ChartCard title={`Meilleur temps annuel`} name={`performance_yearly_best_${dt}`}>
                  <ResponsiveContainer width="100%" height={250}>
                    <BarChart data={years}>
                      <defs>
                        <linearGradient id={`gradBar_${dt}`} x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor={DC[dt]} stopOpacity={0.9} />
                          <stop offset="100%" stopColor={DC[dt]} stopOpacity={0.4} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid {...gridStyle} />
                      <XAxis dataKey="year" tick={{ ...axisStyle, fontSize: 11 }} />
                      <YAxis tick={axisStyle} tickFormatter={ft} reversed />
                      <Tooltip content={<PTip />} />
                      <Bar dataKey="time" radius={[6, 6, 0, 0]} animationDuration={800}>
                        {years.map((_, i) => <Cell key={i} fill={`url(#gradBar_${dt})`} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </ChartCard>
              )}
            </div>
          </div>
        )
      })}

    </div>
  )
}
