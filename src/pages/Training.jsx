import React, { useMemo, useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { loadTrainingStatus } from '../api'
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ComposedChart, Line, ReferenceLine, ReferenceArea
} from 'recharts'
import { useActivities } from '../contexts/ActivityContext'
import {
  computeTrainingLoad, STATUS_COLORS, STATUS_LABELS,
  TSB_ZONES, getTSBZone, fmtDuration, rangeSubtitle,
} from '../lib/training'
import { parseLocalDate, localDateStr } from '../lib/compute'
import ChartCard from '../components/ChartCard'
import StatCard from '../components/StatCard'
import Loader from '../components/Loader'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle, Tip, COLORS } from '../lib/chartTheme'

export default function Training() {
  const { activities, allActivities, loading, effectiveDateRange, now } = useActivities()
  const [, setSearchParams] = useSearchParams()

  // Training status now comes from Garmin (replaces the in-app TSB heuristic).
  const [garminStatus, setGarminStatus] = useState(null)
  useEffect(() => {
    let cancelled = false
    loadTrainingStatus().then(s => { if (!cancelled) setGarminStatus(s) })
    return () => { cancelled = true }
  }, [])

  // Compute training load on the FULL history so CTL/ATL/TSB stay accurate
  // regardless of the date filter, then slice the chart window from the result.
  const seriesDays = useMemo(() => {
    if (!effectiveDateRange) return null // "Tout" → full history
    const daysFromStart = Math.ceil((now - effectiveDateRange.from) / 86400000)
    return Math.max(180, daysFromStart)
  }, [effectiveDateRange, now])

  const data = useMemo(
    () => computeTrainingLoad(allActivities, { daysBack: seriesDays }),
    [allActivities, seriesDays, now]
  )

  const recentRuns = useMemo(() => activities.slice(0, 10), [activities])

  // Chart window: filter daily series by the effective date range. When no
  // range is active ("Tout") show the full series.
  const chartData = useMemo(() => {
    if (!effectiveDateRange) return data.daily
    const fromKey = localDateStr(new Date(effectiveDateRange.from))
    const toKey = localDateStr(new Date(effectiveDateRange.to))
    return data.daily.filter(d => d.date >= fromKey && d.date <= toKey)
  }, [data.daily, effectiveDateRange])

  const weeklyChartData = useMemo(() => {
    if (!effectiveDateRange) return data.weeklyLoad
    const fromKey = localDateStr(new Date(effectiveDateRange.from))
    const toKey = localDateStr(new Date(effectiveDateRange.to))
    return data.weeklyLoad.filter(w => w.week >= fromKey && w.week <= toKey)
  }, [data.weeklyLoad, effectiveDateRange])

  const subtitle = rangeSubtitle(effectiveDateRange)

  console.log('[Training] render — preset:', effectiveDateRange?.presetDays, 'seriesDays:', seriesDays, 'chartPoints:', chartData.length)

  if (loading) return <Loader />

  const statusColor = STATUS_COLORS[data.status] || '#6b7280'
  const statusLabel = STATUS_LABELS[data.status] || data.status
  const tsbZone = getTSBZone(data.currentTSB)

  // TSB range for reference areas
  const tsbMin = Math.min(...chartData.map(d => d.tsb || 0))
  const tsbMax = Math.max(...chartData.map(d => d.tsb || 0))

  return (
    <div data-name="page_training">
      <h2 className="page_heading mb-6 training_header" data-name="training_header">Charge d'entrainement</h2>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mb-8 training_stats_grid" data-name="training_stats_grid">
        <StatCard label="Fitness (CTL)" value={data.currentCTL} name="training_stat_ctl" />
        <StatCard label="Fatigue (ATL)" value={data.currentATL} name="training_stat_atl" />
        <StatCard label="Forme (TSB)" value={data.currentTSB} name="training_stat_tsb" />
        <div className="card flex flex-col justify-center training_status_stat_card" data-name="training_status_stat_card">
          <div className="stat-label training_status_stat_label" data-name="training_status_stat_label">Statut Garmin</div>
          {garminStatus?.status ? (
            <>
              <div className="mt-1 text-lg font-semibold text-brand training_status_stat_value" data-name="training_status_stat_value">
                {garminStatus.status}
              </div>
              {garminStatus.trainingLoad && (
                <div className="text-xs text-txt-muted mt-0.5 training_status_stat_description" data-name="training_status_stat_description">{garminStatus.trainingLoad}</div>
              )}
            </>
          ) : (
            <div className="mt-1 text-sm text-txt-muted training_status_stat_fallback" data-name="training_status_stat_fallback">
              {garminStatus ? 'Statut Garmin indisponible' : '…'}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 training_chart_grid" data-name="training_chart_grid">
        <ChartCard title="Fitness / Fatigue / Forme" subtitle={subtitle} className="lg:col-span-2 training_fitness_chart_span training_fitness_chart_chart_card" data-name="training_fitness_chart_chart_card" name="training_fitness_chart">
          <ResponsiveContainer width="100%" height={350}>
            <ComposedChart data={chartData}>
              <defs>
                <linearGradient id="gradCTL" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.2} />
                  <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="gradATL" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#ef4444" stopOpacity={0.15} />
                  <stop offset="100%" stopColor="#ef4444" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid {...gridStyle} />
              <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 10 }} tickFormatter={d => d.slice(5)} />
              <YAxis tick={axisStyle} />
              <Tooltip content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const d = payload[0]?.payload
                const zone = getTSBZone(d?.tsb || 0)
                return (
                  <div className="tooltip_surface_card training_status_tooltip" data-name="training_status_tooltip">
                    <div className="tooltip_date_label training_status_tooltip_date" data-name="training_status_tooltip_date">{d?.date}</div>
                    <div className="tooltip_value_blue training_status_tooltip_ctl" data-name="training_status_tooltip_ctl">CTL (Fitness): {d?.ctl}</div>
                    <div className="tooltip_value_red training_status_tooltip_atl" data-name="training_status_tooltip_atl">ATL (Fatigue): {d?.atl}</div>
                    <div className="tooltip_value_green training_status_tooltip_tsb" data-name="training_status_tooltip_tsb">TSB (Forme): {d?.tsb}</div>
                    <div className="tooltip_badge training_status_tooltip_badge" data-name="training_status_tooltip_badge" style={{ backgroundColor: zone.color }}>
                      {zone.label}
                    </div>
                    <div className="text-xs text-txt-muted mt-0.5 training_status_tooltip_description" data-name="training_status_tooltip_description">{zone.desc}</div>
                  </div>
                )
              }} />
              <ReferenceLine y={0} stroke="#cbd5e1" strokeDasharray="3 3" />
              <Area type="monotone" dataKey="ctl" stroke="#3b82f6" strokeWidth={2.5} fill="url(#gradCTL)" name="Fitness (CTL)" connectNulls animationDuration={1200} />
              <Area type="monotone" dataKey="atl" stroke="#ef4444" strokeWidth={2} fill="url(#gradATL)" name="Fatigue (ATL)" connectNulls animationDuration={1200} />
              <Line type="monotone" dataKey="tsb" stroke="#10b981" strokeWidth={2} dot={false} name="Forme (TSB)" connectNulls animationDuration={1200} />
            </ComposedChart>
          </ResponsiveContainer>
          {/* Legend with interpretation */}
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs training_tsb_legend" data-name="training_tsb_legend">
            {TSB_ZONES.map(z => (
              <div key={z.label} className="flex items-center gap-2 px-2 py-1.5 rounded-lg training_tsb_legend_item" data-name="training_tsb_legend_item" style={{ backgroundColor: z.color }}>
                <div>
                  <div className="font-medium text-txt training_tsb_legend_label" data-name="training_tsb_legend_label">{z.label}</div>
                  <div className="text-txt-muted training_tsb_legend_range" data-name="training_tsb_legend_range">TSB {z.min > -100 ? z.min : '< -30'} a {z.max < 100 ? z.max : '> 25'}</div>
                </div>
              </div>
            ))}
          </div>
        </ChartCard>

        <ChartCard title="Charge hebdomadaire" subtitle={subtitle} name="training_weekly_load_chart">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={weeklyChartData}>
              <defs>
                <linearGradient id="gradWeekLoad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={COLORS.brand} stopOpacity={0.9} />
                  <stop offset="100%" stopColor={COLORS.brand} stopOpacity={0.4} />
                </linearGradient>
              </defs>
              <CartesianGrid {...gridStyle} />
              <XAxis dataKey="week" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={d => d.slice(5)} />
              <YAxis tick={axisStyle} />
              <Tooltip content={<Tip />} />
              <Bar dataKey="load" fill="url(#gradWeekLoad)" name="Charge (TRIMP)" radius={[4, 4, 0, 0]} animationDuration={800} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Charge quotidienne" subtitle={subtitle} name="training_daily_load_chart">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData}>
              <defs>
                <linearGradient id="gradDailyLoad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.9} />
                  <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.3} />
                </linearGradient>
              </defs>
              <CartesianGrid {...gridStyle} />
              <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={d => d.slice(5)} />
              <YAxis tick={axisStyle} />
              <Tooltip content={<Tip />} />
              <Bar dataKey="load" fill="url(#gradDailyLoad)" name="Load" radius={[3, 3, 0, 0]} animationDuration={800} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      {/* Recent runs */}
      {recentRuns.length > 0 && (
        <div className="card mt-6 training_recent_runs_card" data-name="training_recent_runs_card">
          <h3 className="text-sm font-medium text-txt-secondary mb-4 training_recent_runs_title" data-name="training_recent_runs_title">Dernieres courses</h3>
          <div className="data_table_scroller training_recent_runs_scroller" data-name="training_recent_runs_scroller">
            <table className="w-full text-sm training_recent_runs_table" data-name="training_recent_runs_table">
              <thead>
                <tr className="data_table_header_row training_recent_runs_header" data-name="training_recent_runs_header">
                  <th className="data_table_header_cell_left training_recent_runs_header_cell_date" data-name="training_recent_runs_header_cell_date">Date</th>
                  <th className="data_table_header_cell_left training_recent_runs_header_cell_name" data-name="training_recent_runs_header_cell_name">Nom</th>
                  <th className="data_table_header_cell_right training_recent_runs_header_cell_distance" data-name="training_recent_runs_header_cell_distance">Distance</th>
                  <th className="data_table_header_cell_right training_recent_runs_header_cell_time" data-name="training_recent_runs_header_cell_time">Temps</th>
                  <th className="data_table_header_cell_right training_recent_runs_header_cell_pace" data-name="training_recent_runs_header_cell_pace">Allure</th>
                  <th className="data_table_header_cell_right_last training_recent_runs_header_cell_hr" data-name="training_recent_runs_header_cell_hr">FC moy</th>
                </tr>
              </thead>
              <tbody>
                {recentRuns.map(r => {
                  const d = parseLocalDate(r.start_date_local)
                  const km = (r.distance / 1000).toFixed(2)
                  const paceTotal = r.distance > 0 ? Math.round(r.moving_time / (r.distance / 1000)) : 0
                  const paceMin = Math.floor(paceTotal / 60)
                  const paceSec = paceTotal % 60
                  return (
                    <tr key={r.id}
                      onClick={() => setSearchParams({ run: r.id })}
                      className="data_table_body_row training_recent_runs_row" data-name="training_recent_runs_row">
                      <td className="data_table_body_cell_date training_recent_runs_cell_date" data-name="training_recent_runs_cell_date">
                        {d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' })}
                      </td>
                      <td className="data_table_body_cell_title training_recent_runs_cell_name" data-name="training_recent_runs_cell_name">{r.name}</td>
                      <td className="data_table_body_cell_metric training_recent_runs_cell_distance" data-name="training_recent_runs_cell_distance">{km} km</td>
                      <td className="data_table_body_cell_metric training_recent_runs_cell_time" data-name="training_recent_runs_cell_time">{fmtDuration(r.moving_time)}</td>
                      <td className="data_table_body_cell_metric training_recent_runs_cell_pace" data-name="training_recent_runs_cell_pace">{paceMin}:{String(paceSec).padStart(2, '0')}/km</td>
                      <td className="data_table_body_cell_metric_last training_recent_runs_cell_hr" data-name="training_recent_runs_cell_hr">{r.average_heartrate ? Math.round(r.average_heartrate) : '-'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Interpretation Guide */}
      <div className="card mt-6 training_guide_card" data-name="training_guide_card">
        <h3 className="text-sm font-medium text-txt-secondary mb-3 training_guide_title" data-name="training_guide_title">Comment lire ce graphique ?</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs text-txt-secondary training_guide_grid" data-name="training_guide_grid">
          <div className="training_guide_ctl" data-name="training_guide_ctl">
            <div className="guide_marker_row training_guide_ctl_header" data-name="training_guide_ctl_header">
              <div className="w-3 h-0.5 bg-blue-500 rounded training_guide_ctl_marker" data-name="training_guide_ctl_marker" />
              <span className="guide_marker_label training_guide_ctl_label" data-name="training_guide_ctl_label">CTL (Fitness)</span>
            </div>
            <p>Charge d'entrainement cumulee sur 42 jours. Plus elle monte, plus vous etes en forme. Augmentation ideale: 5-10% par semaine.</p>
          </div>
          <div className="training_guide_atl" data-name="training_guide_atl">
            <div className="guide_marker_row training_guide_atl_header" data-name="training_guide_atl_header">
              <div className="w-3 h-0.5 bg-red-500 rounded training_guide_atl_marker" data-name="training_guide_atl_marker" />
              <span className="guide_marker_label training_guide_atl_label" data-name="training_guide_atl_label">ATL (Fatigue)</span>
            </div>
            <p>Charge recente sur 7 jours. Quand ATL depasse CTL, vous accumulez de la fatigue. Normal en phase de charge.</p>
          </div>
          <div className="training_guide_tsb" data-name="training_guide_tsb">
            <div className="guide_marker_row training_guide_tsb_header" data-name="training_guide_tsb_header">
              <div className="w-3 h-0.5 bg-emerald-500 rounded training_guide_tsb_marker" data-name="training_guide_tsb_marker" />
              <span className="guide_marker_label training_guide_tsb_label" data-name="training_guide_tsb_label">TSB (Forme)</span>
            </div>
            <p>Forme = Fitness - Fatigue. Negatif = en charge (bien). Positif = repose. Viser -10 a +5 pour un pic de forme.</p>
          </div>
        </div>
      </div>
    </div>
  )
}
