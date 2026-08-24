import React, { useMemo } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ComposedChart, Line,
  BarChart, Bar, ReferenceLine, PieChart, Pie, Cell
} from 'recharts'
import { useActivities } from '../contexts/ActivityContext'
import { parseLocalDate, fmtPace, localDateStr, getMonday } from '../lib/compute'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle, Tip, COLORS } from '../lib/chartTheme'
import {
  computeTrainingLoad, STATUS_COLORS, STATUS_LABELS,
  TSB_ZONES, getTSBZone, rangeSubtitle,
} from '../lib/training'
import {
  buildZones, ZONE_COLORS, ZONE_LABELS,
  computeZoneDistribution, computeWeeklyZones, computeLoadEvolution,
  getCurrentMaxHr
} from '../lib/heartRateZones'
import { useNow } from '../lib/clock'
import ChartCard from '../components/ChartCard'
import StatCard from '../components/StatCard'
import Loader from '../components/Loader'
import ProgressInsights from '../components/ProgressInsights'
import { PointGraphControls, pointDot, usePointGraph } from '../components/PointGraphTools'

const DAY = 86400000

// Libellé lisible d'une durée exprimée en jours (pour les titres/labels).
function fmtDuration(days) {
  if (days >= 340) {
    const y = Math.round(days / 365)
    return y <= 1 ? '12 mois' : `${y} ans`
  }
  if (days >= 55) return `${Math.round(days / 30)} mois`
  return `${days} j`
}

function windowRuns(all, from, to) {
  return all.filter(a => {
    const t = parseLocalDate(a.start_date_local).getTime()
    return t >= from && t < to
  })
}

// Agrège un ensemble de runs sur une fenêtre de `days` jours.
function aggregate(runs, days) {
  const km = runs.reduce((s, a) => s + a.distance, 0) / 1000
  const weeks = Math.max(1, days) / 7
  const paceRuns = runs.filter(a => a.distance > 3000 && a.average_speed > 0)
  const pace = paceRuns.length
    ? paceRuns.reduce((s, a) => s + 1000 / a.average_speed, 0) / paceRuns.length : 0
  const long = runs.length ? Math.max(...runs.map(a => a.distance)) / 1000 : 0
  return {
    km: Math.round(km),
    kmWeek: Math.round((km / weeks) * 10) / 10,
    runs: runs.length,
    runsWeek: Math.round((runs.length / weeks) * 10) / 10,
    pace,
    paceStr: pace > 0 ? fmtPace(pace) : '-',
    long: Math.round(long * 10) / 10,
  }
}

// --- Existing progress helpers ---

function computePaceEvolution(activities) {
  // Rolling average pace over 10 runs, for runs > 3km
  const runs = activities
    .filter(a => a.distance > 3000 && a.average_speed > 0)
    .sort((a, b) => a.start_date_local.localeCompare(b.start_date_local))
    .map(a => ({
      date: a.start_date_local.slice(0, 10),
      id: a.id,
      name: a.name,
      pace: Math.round((1000 / a.average_speed) * 10) / 10,
      distance_km: Math.round(a.distance / 100) / 10,
    }))

  // Compute rolling average (10-run window)
  return runs.map((r, i) => {
    const window = runs.slice(Math.max(0, i - 9), i + 1)
    const avgPace = window.reduce((s, w) => s + w.pace, 0) / window.length
    return { ...r, avgPace: Math.round(avgPace * 10) / 10 }
  })
}

function computeKmEvolution(activities) {
  // Rolling average distance over 10 runs
  const runs = activities
    .filter(a => a.distance > 0)
    .sort((a, b) => a.start_date_local.localeCompare(b.start_date_local))
    .map(a => ({
      date: a.start_date_local.slice(0, 10),
      id: a.id,
      name: a.name,
      km: Math.round(a.distance / 100) / 10,
    }))

  return runs.map((r, i) => {
    const window = runs.slice(Math.max(0, i - 9), i + 1)
    const avgKm = window.reduce((s, w) => s + w.km, 0) / window.length
    return { ...r, avgKm: Math.round(avgKm * 10) / 10 }
  })
}

export default function Progress() {
  const { activities, allActivities, loading, effectiveDateRange } = useActivities()
  const now = useNow()

  console.log('[Progress] render, activities:', activities.length)

  // Fenêtre active = filtre de dates global (7j/30j/90j/6m/1a/Tout). Toute la
  // page s'aligne dessus au lieu des anciens 30/90 jours figés. La période
  // précédente (même durée, juste avant) sert aux comparaisons.
  const windows = useMemo(() => {
    let from, to
    if (effectiveDateRange) {
      from = effectiveDateRange.from
      to = effectiveDateRange.to
    } else {
      to = now
      from = allActivities.length
        ? Math.min(...allActivities.map(a => parseLocalDate(a.start_date_local).getTime()))
        : now - 90 * DAY
    }
    const len = Math.max(DAY, to - from)
    const days = Math.max(1, Math.round(len / DAY))
    return {
      from, to, len, days,
      prevFrom: from - len,
      prevTo: from,
      daysFromNow: Math.max(1, Math.ceil((now - from) / DAY)),
      label: fmtDuration(days),
      isAll: !effectiveDateRange,
    }
  }, [effectiveDateRange, allActivities, now])

  const stats = useMemo(() => {
    if (!activities.length) return null
    const cur = windowRuns(allActivities, windows.from, windows.to)
    const prev = windowRuns(allActivities, windows.prevFrom, windows.prevTo)
    const c = aggregate(cur, windows.days)
    const p = aggregate(prev, windows.days)
    return {
      c, p,
      label: windows.label,
      hasPrev: prev.length > 0,
      kmTrend: p.kmWeek > 0 ? Math.round(((c.kmWeek - p.kmWeek) / p.kmWeek) * 100) : 0,
      paceTrend: (p.pace > 0 && c.pace > 0) ? Math.round(((p.pace - c.pace) / p.pace) * 100) : 0,
    }
  }, [activities, allActivities, windows])

  const paceEvolution = useMemo(() => computePaceEvolution(activities), [activities])
  const kmEvolution = useMemo(() => computeKmEvolution(activities), [activities])
  const pacePointGraph = usePointGraph(paceEvolution, { yKey: 'pace', better: 'lower', minVisiblePoints: 12 })
  const kmPointGraph = usePointGraph(kmEvolution, { yKey: 'km', better: 'higher', minVisiblePoints: 12 })

  // Format d'axe temporel adaptatif : "MM-DD" sur une période courte, "AA-MM"
  // dès que la fenêtre couvre plusieurs années (sinon l'année disparaît).
  const dateTick = useMemo(() => {
    const multiYear = windows.days > 400
    return d => (multiYear ? d.slice(2, 7) : d.slice(5))
  }, [windows.days])

  // Training charge — compute from full history so CTL/ATL/TSB stay accurate
  // even when the date filter restricts visible activities.
  const seriesDays = useMemo(() => {
    if (!effectiveDateRange) return null
    const daysFromStart = Math.ceil((now - effectiveDateRange.from) / 86400000)
    return Math.max(180, daysFromStart)
  }, [effectiveDateRange, now])

  const trainingData = useMemo(
    () => computeTrainingLoad(allActivities, { daysBack: seriesDays }),
    [allActivities, seriesDays, now]
  )
  // Heart rate zones
  const maxHr = useMemo(() => getCurrentMaxHr(activities), [activities, now])
  const zones = useMemo(() => buildZones(null, maxHr), [maxHr])
  const distribution = useMemo(
    () => computeZoneDistribution(activities, zones, windows.daysFromNow),
    [activities, zones, windows.daysFromNow, now]
  )
  const weeklyZones = useMemo(() => computeWeeklyZones(activities, zones), [activities, zones])
  const loadEvolution = useMemo(() => computeLoadEvolution(activities, zones), [activities, zones, now])


  if (loading) return <Loader />
  if (!stats) return <div className="text-txt-muted progress_aucune_donnee_disponible_meta" data-name="progress_aucune_donnee_disponible_meta">Aucune donnee disponible.</div>

  const statusColor = STATUS_COLORS[trainingData.status] || '#6b7280'
  const statusLabel = STATUS_LABELS[trainingData.status] || trainingData.status
  const tsbZone = getTSBZone(trainingData.currentTSB)
  const chartData = (() => {
    if (!effectiveDateRange) return trainingData.daily
    const fromKey = localDateStr(new Date(effectiveDateRange.from))
    const toKey = localDateStr(new Date(effectiveDateRange.to))
    return trainingData.daily.filter(d => d.date >= fromKey && d.date <= toKey)
  })()
  const fitnessSubtitle = rangeSubtitle(effectiveDateRange)
  const hrActivities = activities.filter(a => a.average_heartrate)
  console.log('[Progress] fitness chart — preset:', effectiveDateRange?.presetDays, 'seriesDays:', seriesDays, 'points:', chartData.length)

  return (
    <div data-name="page_progress">
      <h2 className="text-xl font-semibold mb-6 text-txt progress_header" data-name="progress_header">Progression</h2>

      {/* Overview Stats */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 sm:gap-4 mb-6 sm:mb-8 progress_overview_stats_grid" data-name="progress_overview_stats_grid">
        <StatCard label={`km/sem. (${stats.label})`} value={stats.c.kmWeek} unit="km" trend={stats.hasPrev ? stats.kmTrend : undefined} trendLabel="vs préc." name="progress_stat_km_week" />
        <StatCard label={`Allure moy. (${stats.label})`} value={stats.c.paceStr} unit="/km" trend={stats.hasPrev ? stats.paceTrend : undefined} trendLabel="vs préc." name="progress_stat_pace" />
        <StatCard label="Sorties/semaine" value={stats.c.runsWeek} name="progress_stat_runs_week" />
        <StatCard label={`Total (${stats.label})`} value={stats.c.km} unit="km" name="progress_stat_total" />
        <StatCard label={`Sorties (${stats.label})`} value={stats.c.runs} name="progress_stat_total_runs" />
        <StatCard label={`Plus longue (${stats.label})`} value={stats.c.long} unit="km" name="progress_stat_long" />
      </div>

      {/* Comparaison période courante vs période précédente de même durée */}
      {stats.hasPrev && (
      <div className="card mb-6 sm:mb-8 progress_comparison_card" data-name="progress_comparison_card">
        <h3 className="text-sm font-medium text-txt-secondary mb-4 progress_comparison_title" data-name="progress_comparison_title">Comparaison {stats.label} vs période précédente</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 progress_comparison_grid" data-name="progress_comparison_grid">
          <div className="progress_comparison_volume" data-name="progress_comparison_volume">
            <div className="text-xs text-txt-muted progress_comparison_volume_volume_meta" data-name="progress_comparison_volume_volume_meta">Volume</div>
            <div className="text-lg font-mono font-semibold text-txt progress_comparison_volume_recent_km_value" data-name="progress_comparison_volume_recent_km_value">{stats.c.km} <span className="text-sm text-txt-secondary progress_comparison_volume_recent_km_value_km_text" data-name="progress_comparison_volume_recent_km_value_km_text">km</span></div>
            <div className="text-xs text-txt-muted progress_comparison_volume_previous_period_meta" data-name="progress_comparison_volume_previous_period_meta">vs {stats.p.km} km</div>
            {stats.kmTrend !== 0 && (
              <div className={`text-xs font-medium mt-0.5 ${stats.kmTrend > 0 ? 'text-emerald-600' : 'text-red-500'} progress_comparison_volume_trend_label`} data-name="progress_comparison_volume_trend_label">
                {stats.kmTrend > 0 ? '+' : ''}{stats.kmTrend}%
              </div>
            )}
          </div>
          <div className="progress_comparison_runs" data-name="progress_comparison_runs">
            <div className="text-xs text-txt-muted progress_comparison_runs_sorties_meta" data-name="progress_comparison_runs_sorties_meta">Sorties</div>
            <div className="text-lg font-mono font-semibold text-txt progress_comparison_runs_recent_runs_value" data-name="progress_comparison_runs_recent_runs_value">{stats.c.runs}</div>
            <div className="text-xs text-txt-muted progress_comparison_runs_vs_prev_runs_meta" data-name="progress_comparison_runs_vs_prev_runs_meta">vs {stats.p.runs}</div>
          </div>
          <div className="progress_comparison_pace" data-name="progress_comparison_pace">
            <div className="text-xs text-txt-muted progress_comparison_pace_allure_moy_meta" data-name="progress_comparison_pace_allure_moy_meta">Allure moy.</div>
            <div className="text-lg font-mono font-semibold text-txt progress_comparison_pace_recent_pace_value" data-name="progress_comparison_pace_recent_pace_value">{stats.c.paceStr} <span className="text-sm text-txt-secondary progress_comparison_pace_recent_pace_value_km_text" data-name="progress_comparison_pace_recent_pace_value_km_text">/km</span></div>
            <div className="text-xs text-txt-muted progress_comparison_pace_vs_prev_pace_meta" data-name="progress_comparison_pace_vs_prev_pace_meta">vs {stats.p.paceStr}</div>
            {stats.paceTrend !== 0 && (
              <div className={`text-xs font-medium mt-0.5 ${stats.paceTrend > 0 ? 'text-emerald-600' : 'text-red-500'} progress_comparison_pace_trend_label`} data-name="progress_comparison_pace_trend_label">
                {stats.paceTrend > 0 ? '+' : ''}{stats.paceTrend}% plus rapide
              </div>
            )}
          </div>
          <div className="progress_comparison_long" data-name="progress_comparison_long">
            <div className="text-xs text-txt-muted progress_comparison_long_plus_longue_sortie_meta" data-name="progress_comparison_long_plus_longue_sortie_meta">Plus longue sortie</div>
            <div className="text-lg font-mono font-semibold text-txt progress_comparison_long_recent_long_value" data-name="progress_comparison_long_recent_long_value">{stats.c.long} <span className="text-sm text-txt-secondary progress_comparison_long_recent_long_value_km_text" data-name="progress_comparison_long_recent_long_value_km_text">km</span></div>
            <div className="text-xs text-txt-muted progress_comparison_long_vs_prev_long_km_meta" data-name="progress_comparison_long_vs_prev_long_km_meta">vs {stats.p.long} km</div>
          </div>
        </div>
      </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:gap-6 mb-6 page_progress_grid" data-name="page_progress_grid">
        {/* Pace Evolution */}
        {paceEvolution.length > 0 && (
          <ChartCard title="Evolution de l'allure" subtitle="Allure moy. par sortie + moyenne mobile 10 runs" name="progress_pace_evolution_chart">
            <PointGraphControls graph={pacePointGraph} className="mb-2" />
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={pacePointGraph.visibleData}>
                <defs>
                  <linearGradient id="gradPaceEvo" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={COLORS.brand} stopOpacity={0.15} />
                    <stop offset="100%" stopColor={COLORS.brand} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={dateTick} />
                <YAxis tick={axisStyle} tickFormatter={v => fmtPace(v)} reversed domain={['dataMin - 10', 'dataMax + 10']} />
                <Tooltip content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const d = payload[0]?.payload
                  return (
                    <div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg progress_pace_evolution_chart_tooltip" data-name="progress_pace_evolution_chart_tooltip">
                      <div className="text-xs text-txt-secondary font-medium progress_pace_evolution_chart_tooltip_date_label" data-name="progress_pace_evolution_chart_tooltip_date_label">{d?.date}</div>
                      <div className="text-sm text-txt font-medium progress_pace_evolution_chart_tooltip_name_label" data-name="progress_pace_evolution_chart_tooltip_name_label">{d?.name}</div>
                      <div className="text-sm font-mono progress_pace_evolution_chart_tooltip_pace_km_value" data-name="progress_pace_evolution_chart_tooltip_pace_km_value" style={{ color: COLORS.brand }}>{fmtPace(d?.pace)}/km</div>
                      <div className="text-xs text-txt-muted progress_pace_evolution_chart_tooltip_moy_10_runs_avg_pace_km_meta" data-name="progress_pace_evolution_chart_tooltip_moy_10_runs_avg_pace_km_meta">Moy. 10 runs: {fmtPace(d?.avgPace)}/km | {d?.distance_km} km</div>
                    </div>
                  )
                }} />
                {pacePointGraph.selectedVisible && (
                  <>
                    <ReferenceLine x={pacePointGraph.selected.date} stroke="#f59e0b" strokeDasharray="5 4" />
                    <ReferenceLine y={pacePointGraph.selected.pace} stroke="#f59e0b" strokeDasharray="5 4" />
                  </>
                )}
                <Area
                  type="monotone"
                  dataKey="pace"
                  stroke={COLORS.brand}
                  strokeWidth={1}
                  fill="url(#gradPaceEvo)"
                  dot={pointDot({
                    selectedId: pacePointGraph.selectedId,
                    betterIds: pacePointGraph.betterIds,
                    onSelect: pacePointGraph.selectPoint,
                    color: COLORS.brand,
                    radius: 2,
                  })}
                  activeDot={false}
                  name="Allure"
                  connectNulls
                />
                <Line type="monotone" dataKey="avgPace" stroke="#2563EB" strokeWidth={2.5} dot={false} name="Moy. mobile" connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </ChartCard>
        )}

        {/* Km Evolution */}
        {kmEvolution.length > 0 && (
          <ChartCard title="Evolution du km moyen" subtitle="Distance par sortie + moyenne mobile 10 runs" name="progress_km_evolution_chart">
            <PointGraphControls graph={kmPointGraph} className="mb-2" />
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={kmPointGraph.visibleData}>
                <defs>
                  <linearGradient id="gradKmEvo" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity={0.15} />
                    <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={dateTick} />
                <YAxis tick={axisStyle} domain={['dataMin - 1', 'dataMax + 1']} />
                <Tooltip content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const d = payload[0]?.payload
                  return (
                    <div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg progress_km_evolution_chart_tooltip" data-name="progress_km_evolution_chart_tooltip">
                      <div className="text-xs text-txt-secondary font-medium progress_km_evolution_chart_tooltip_date_label" data-name="progress_km_evolution_chart_tooltip_date_label">{d?.date}</div>
                      <div className="text-sm text-txt font-medium progress_km_evolution_chart_tooltip_name_label" data-name="progress_km_evolution_chart_tooltip_name_label">{d?.name}</div>
                      <div className="text-sm font-mono progress_km_evolution_chart_tooltip_distance_value" data-name="progress_km_evolution_chart_tooltip_distance_value" style={{ color: '#10b981' }}>{d?.km} km</div>
                      <div className="text-xs text-txt-muted progress_km_evolution_chart_tooltip_average_distance_meta" data-name="progress_km_evolution_chart_tooltip_average_distance_meta">Moy. 10 runs: {d?.avgKm} km</div>
                    </div>
                  )
                }} />
                {kmPointGraph.selectedVisible && (
                  <>
                    <ReferenceLine x={kmPointGraph.selected.date} stroke="#f59e0b" strokeDasharray="5 4" />
                    <ReferenceLine y={kmPointGraph.selected.km} stroke="#f59e0b" strokeDasharray="5 4" />
                  </>
                )}
                <Area
                  type="monotone"
                  dataKey="km"
                  stroke="#10b981"
                  strokeWidth={1}
                  fill="url(#gradKmEvo)"
                  dot={pointDot({
                    selectedId: kmPointGraph.selectedId,
                    betterIds: kmPointGraph.betterIds,
                    onSelect: kmPointGraph.selectPoint,
                    color: '#10b981',
                    radius: 2,
                  })}
                  activeDot={false}
                  name="Distance"
                  connectNulls
                />
                <Line type="monotone" dataKey="avgKm" stroke="#7c3aed" strokeWidth={2.5} dot={false} name="Moy. mobile" connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </ChartCard>
        )}
      </div>

      {/* ---- Charge d'entraînement ---- */}
      <div className="mt-8 mb-6 progress_training_section_header" data-name="progress_training_section_header">
        <hr className="border-surface-border mb-6 progress_training_section_header_divider" data-name="progress_training_section_header_divider" />
        <h3 className="text-lg font-semibold text-txt mb-6 progress_training_section_header_charge_d_entrainement_title" data-name="progress_training_section_header_charge_d_entrainement_title">Charge d'entrainement</h3>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mb-8 progress_training_stats_grid" data-name="progress_training_stats_grid">
        <StatCard label="Fitness (CTL)" value={trainingData.currentCTL} name="progress_training_stat_ctl" />
        <StatCard label="Fatigue (ATL)" value={trainingData.currentATL} name="progress_training_stat_atl" />
        <StatCard label="Forme (TSB)" value={trainingData.currentTSB} name="progress_training_stat_tsb" />
        <div className="card flex flex-col justify-center progress_training_stat_status" data-name="progress_training_stat_status">
          <div className="stat-label progress_training_stat_status_statut_text" data-name="progress_training_stat_status_statut_text">Statut</div>
          <div className="mt-1 text-lg font-semibold progress_training_stat_status_label" data-name="progress_training_stat_status_label" style={{ color: statusColor }}>
            {statusLabel}
          </div>
          <div className="text-xs text-txt-muted mt-0.5 progress_training_stat_status_desc_meta" data-name="progress_training_stat_status_desc_meta">{tsbZone.desc}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 progress_training_charts_grid" data-name="progress_training_charts_grid">
        <ChartCard title="Fitness / Fatigue / Forme" subtitle={fitnessSubtitle} className="lg:col-span-2 progress_fitness_chart_chart_card" data-name="progress_fitness_chart_chart_card" name="progress_fitness_chart">
          <ResponsiveContainer width="100%" height={350}>
            <ComposedChart data={chartData}>
              <defs>
                <linearGradient id="gradCTL2" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.2} />
                  <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="gradATL2" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#ef4444" stopOpacity={0.15} />
                  <stop offset="100%" stopColor="#ef4444" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid {...gridStyle} />
              <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 10 }} tickFormatter={dateTick} />
              <YAxis tick={axisStyle} />
              <Tooltip content={({ active, payload }) => {
                if (!active || !payload?.length) return null
                const d = payload[0]?.payload
                const zone = getTSBZone(d?.tsb || 0)
                return (
                  <div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg progress_fitness_chart_chart_card_tooltip" data-name="progress_fitness_chart_chart_card_tooltip">
                    <div className="text-xs text-txt-secondary font-medium progress_fitness_chart_chart_card_tooltip_date_label" data-name="progress_fitness_chart_chart_card_tooltip_date_label">{d?.date}</div>
                    <div className="text-sm text-blue-600 font-mono progress_fitness_chart_chart_card_tooltip_ctl_fitness_ctl_value" data-name="progress_fitness_chart_chart_card_tooltip_ctl_fitness_ctl_value">CTL (Fitness): {d?.ctl}</div>
                    <div className="text-sm text-red-500 font-mono progress_fitness_chart_chart_card_tooltip_atl_fatigue_atl_value" data-name="progress_fitness_chart_chart_card_tooltip_atl_fatigue_atl_value">ATL (Fatigue): {d?.atl}</div>
                    <div className="text-sm text-emerald-600 font-mono progress_fitness_chart_chart_card_tooltip_tsb_forme_tsb_value" data-name="progress_fitness_chart_chart_card_tooltip_tsb_forme_tsb_value">TSB (Forme): {d?.tsb}</div>
                    <div className="mt-1.5 px-2 py-0.5 rounded text-xs font-medium progress_fitness_chart_chart_card_tooltip_zone_label" data-name="progress_fitness_chart_chart_card_tooltip_zone_label" style={{ backgroundColor: zone.color }}>
                      {zone.label}
                    </div>
                    <div className="text-xs text-txt-muted mt-0.5 progress_fitness_chart_chart_card_tooltip_desc_meta" data-name="progress_fitness_chart_chart_card_tooltip_desc_meta">{zone.desc}</div>
                  </div>
                )
              }} />
              <ReferenceLine y={0} stroke="#cbd5e1" strokeDasharray="3 3" />
              <Area type="monotone" dataKey="ctl" stroke="#3b82f6" strokeWidth={2.5} fill="url(#gradCTL2)" name="Fitness (CTL)" connectNulls animationDuration={1200} />
              <Area type="monotone" dataKey="atl" stroke="#ef4444" strokeWidth={2} fill="url(#gradATL2)" name="Fatigue (ATL)" connectNulls animationDuration={1200} />
              <Line type="monotone" dataKey="tsb" stroke="#10b981" strokeWidth={2} dot={false} name="Forme (TSB)" connectNulls animationDuration={1200} />
            </ComposedChart>
          </ResponsiveContainer>
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs progress_tsb_legend" data-name="progress_tsb_legend">
            {TSB_ZONES.map(z => (
              <div key={z.label} className="flex items-center gap-2 px-2 py-1.5 rounded-lg progress_tsb_legend_item" data-name="progress_tsb_legend_item" style={{ backgroundColor: z.color }}>
                <div>
                  <div className="font-medium text-txt progress_tsb_legend_item_label" data-name="progress_tsb_legend_item_label">{z.label}</div>
                  <div className="text-txt-muted progress_tsb_legend_item_tsb_min_a_meta" data-name="progress_tsb_legend_item_tsb_min_a_meta">TSB {z.min > -100 ? z.min : '< -30'} a {z.max < 100 ? z.max : '> 25'}</div>
                </div>
              </div>
            ))}
          </div>
        </ChartCard>

        <div className="card lg:col-span-2 progress_guide_card" data-name="progress_guide_card">
          <h3 className="text-sm font-medium text-txt-secondary mb-3 progress_guide_title" data-name="progress_guide_title">Comment lire ce graphique ?</h3>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs text-txt-secondary progress_guide_card_grid" data-name="progress_guide_card_grid">
            <div className="progress_guide_ctl" data-name="progress_guide_ctl">
              <div className="flex items-center gap-2 mb-1 progress_guide_ctl_ctl_fitness_value" data-name="progress_guide_ctl_ctl_fitness_value">
                <div className="w-3 h-0.5 bg-blue-500 rounded progress_guide_ctl_ctl_fitness_value_section" data-name="progress_guide_ctl_ctl_fitness_value_section" />
                <span className="font-medium text-txt progress_guide_ctl_ctl_fitness_value_ctl_fitness_label" data-name="progress_guide_ctl_ctl_fitness_value_ctl_fitness_label">CTL (Fitness)</span>
              </div>
              <p>Charge d'entrainement cumulee sur 42 jours. Plus elle monte, plus vous etes en forme. Augmentation ideale: 5-10% par semaine.</p>
            </div>
            <div className="progress_guide_atl" data-name="progress_guide_atl">
              <div className="flex items-center gap-2 mb-1 progress_guide_atl_atl_fatigue_value" data-name="progress_guide_atl_atl_fatigue_value">
                <div className="w-3 h-0.5 bg-red-500 rounded progress_guide_atl_atl_fatigue_value_section" data-name="progress_guide_atl_atl_fatigue_value_section" />
                <span className="font-medium text-txt progress_guide_atl_atl_fatigue_value_atl_fatigue_label" data-name="progress_guide_atl_atl_fatigue_value_atl_fatigue_label">ATL (Fatigue)</span>
              </div>
              <p>Charge recente sur 7 jours. Quand ATL depasse CTL, vous accumulez de la fatigue. Normal en phase de charge.</p>
            </div>
            <div className="progress_guide_tsb" data-name="progress_guide_tsb">
              <div className="flex items-center gap-2 mb-1 progress_guide_tsb_tsb_forme_value" data-name="progress_guide_tsb_tsb_forme_value">
                <div className="w-3 h-0.5 bg-emerald-500 rounded progress_guide_tsb_tsb_forme_value_section" data-name="progress_guide_tsb_tsb_forme_value_section" />
                <span className="font-medium text-txt progress_guide_tsb_tsb_forme_value_tsb_forme_label" data-name="progress_guide_tsb_tsb_forme_value_tsb_forme_label">TSB (Forme)</span>
              </div>
              <p>Forme = Fitness - Fatigue. Negatif = en charge (bien). Positif = repose. Viser -10 a +5 pour un pic de forme.</p>
            </div>
          </div>
        </div>

        <ChartCard title="Charge hebdomadaire" name="progress_weekly_load_chart">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={trainingData.weeklyLoad}>
              <defs>
                <linearGradient id="gradWeekLoad2" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={COLORS.brand} stopOpacity={0.9} />
                  <stop offset="100%" stopColor={COLORS.brand} stopOpacity={0.4} />
                </linearGradient>
              </defs>
              <CartesianGrid {...gridStyle} />
              <XAxis dataKey="week" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={dateTick} />
              <YAxis tick={axisStyle} />
              <Tooltip content={<Tip />} />
              <Bar dataKey="load" fill="url(#gradWeekLoad2)" name="Charge (TRIMP)" radius={[4, 4, 0, 0]} animationDuration={800} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard title="Charge quotidienne" subtitle={fitnessSubtitle} name="progress_daily_load_chart">
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData}>
              <defs>
                <linearGradient id="gradDailyLoad2" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.9} />
                  <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.3} />
                </linearGradient>
              </defs>
              <CartesianGrid {...gridStyle} />
              <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={dateTick} />
              <YAxis tick={axisStyle} />
              <Tooltip content={<Tip />} />
              <Bar dataKey="load" fill="url(#gradDailyLoad2)" name="Load" radius={[3, 3, 0, 0]} animationDuration={800} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>


      {/* ---- Zones de fréquence cardiaque ---- */}
      <div className="mt-8 mb-6 progress_zones_section_header" data-name="progress_zones_section_header">
        <hr className="border-surface-border mb-6 progress_zones_section_header_divider" data-name="progress_zones_section_header_divider" />
        <h3 className="text-lg font-semibold text-txt mb-6 progress_zones_section_header_zones_de_frequence_cardiaque_title" data-name="progress_zones_section_header_zones_de_frequence_cardiaque_title">Zones de frequence cardiaque</h3>
      </div>

      {hrActivities.length === 0 ? (
        <div className="card text-center py-12 mb-6 progress_zones_empty_card" data-name="progress_zones_empty_card">
          <p className="text-txt-muted progress_zones_empty_card_aucune_activite_avec_frequence_description" data-name="progress_zones_empty_card_aucune_activite_avec_frequence_description">Aucune activite avec frequence cardiaque detectee.</p>
          <p className="text-sm text-txt-muted mt-2 progress_zones_empty_card_connectez_un_capteur_fc_description" data-name="progress_zones_empty_card_connectez_un_capteur_fc_description">Connectez un capteur FC pour debloquer cette analyse.</p>
        </div>
      ) : (
        <div data-name="progress_zones_block">
          <div className="flex items-center justify-between mb-6 progress_zones_header_row" data-name="progress_zones_header_row">
            <div className="text-sm font-medium text-txt-secondary progress_zones_header_row_zones_d_entrainement_label" data-name="progress_zones_header_row_zones_d_entrainement_label">Zones d'entrainement</div>
            <div className="text-xs text-txt-muted progress_zones_period_label" data-name="progress_zones_period_label">{fitnessSubtitle}</div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 sm:gap-4 mb-6 sm:mb-8 progress_zones_stats_grid" data-name="progress_zones_stats_grid">
            <StatCard label="FC max utilisée" value={maxHr} unit="bpm" name="progress_zones_stat_max_hr" />
            <StatCard label="Aerobic" value={distribution.aerobic} unit="%" name="progress_zones_stat_aerobic" />
            <StatCard label="Anaerobic" value={distribution.anaerobic} unit="%" name="progress_zones_stat_anaerobic" />
            <StatCard label="Temps total" value={Math.round(distribution.totalMinutes / 60)} unit="h" name="progress_zones_stat_total_time" />
            <StatCard label="Activites FC" value={hrActivities.length} name="progress_zones_stat_hr_activities" />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 mb-6 progress_zones_block_grid" data-name="progress_zones_block_grid">
            <ChartCard title={`Repartition (${stats.label})`} name="progress_zones_distribution_chart">
              <div className="flex items-center gap-6 progress_zones_distribution_layout" data-name="progress_zones_distribution_layout">
                <ResponsiveContainer width="50%" height={220}>
                  <PieChart>
                    <Pie
                      data={distribution.zoneDistribution.filter(z => z.minutes > 0)}
                      dataKey="minutes"
                      nameKey="label"
                      cx="50%" cy="50%"
                      innerRadius={55} outerRadius={90}
                      paddingAngle={2}
                    >
                      {distribution.zoneDistribution.filter(z => z.minutes > 0).map(z => (
                        <Cell key={z.zone} fill={z.color} />
                      ))}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
                <div className="flex-1 space-y-2 progress_zones_legend" data-name="progress_zones_legend">
                  {distribution.zoneDistribution.map((z, idx) => (
                    <div key={z.zone} className={`flex items-center gap-3 progress_zones_zone_${idx + 1} progress_zones_zone`} data-name={`progress_zones_zone_${idx + 1}`}>
                      <div className="w-3 h-3 rounded-full progress_zones_zone_section" data-name="progress_zones_zone_section" style={{ backgroundColor: z.color }} />
                      <div className="flex-1 progress_zones_zone_min_section" data-name="progress_zones_zone_min_section">
                        <div className="text-xs font-medium text-txt progress_zones_zone_min_label" data-name="progress_zones_zone_min_label">{z.label}</div>
                        <div className="text-xs text-txt-muted progress_zones_zone_min_section_minutes_min_meta" data-name="progress_zones_zone_min_section_minutes_min_meta">{z.minutes} min</div>
                      </div>
                      <div className="text-sm font-mono font-medium text-txt progress_zones_zone_pct_value" data-name="progress_zones_zone_pct_value">{z.pct}%</div>
                    </div>
                  ))}
                </div>
              </div>
            </ChartCard>

            {weeklyZones.length > 0 && (
              <ChartCard title="Zones hebdomadaires" subtitle="Min par zone par semaine" name="progress_zones_weekly_chart">
                <ResponsiveContainer width="100%" height={250}>
                  <BarChart data={weeklyZones.slice(-26)}>
                    <CartesianGrid {...gridStyle} />
                    <XAxis dataKey="week" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={dateTick} />
                    <YAxis tick={axisStyle} />
                    <Tooltip content={<Tip unit="min" />} />
                    {ZONE_LABELS.map((z, i) => (
                      <Bar key={z} dataKey={`z${i + 1}`} stackId="zones" fill={ZONE_COLORS[i]} name={z} radius={i === 4 ? [3, 3, 0, 0] : [0, 0, 0, 0]} />
                    ))}
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            )}
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 mb-6 progress_zones_secondary_charts_grid" data-name="progress_zones_secondary_charts_grid">
            {loadEvolution.length > 0 && (
              <ChartCard title="Evolution charge aerobie/anaerobie" subtitle="Fenetre glissante 30j" name="progress_zones_load_evolution_chart">
                <ResponsiveContainer width="100%" height={250}>
                  <AreaChart data={loadEvolution}>
                    <defs>
                      <linearGradient id="gradLowAero2" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.3} />
                        <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.02} />
                      </linearGradient>
                      <linearGradient id="gradHighAero2" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#10b981" stopOpacity={0.3} />
                        <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                      </linearGradient>
                      <linearGradient id="gradAnae2" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#ef4444" stopOpacity={0.3} />
                        <stop offset="100%" stopColor="#ef4444" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid {...gridStyle} />
                    <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={dateTick} />
                    <YAxis tick={axisStyle} />
                    <Tooltip content={<Tip unit="min" />} />
                    <Area type="monotone" dataKey="lowAerobic" stroke="#3b82f6" strokeWidth={2} fill="url(#gradLowAero2)" name="Aerobie leger" connectNulls />
                    <Area type="monotone" dataKey="highAerobic" stroke="#10b981" strokeWidth={2} fill="url(#gradHighAero2)" name="Aerobie intense" connectNulls />
                    <Area type="monotone" dataKey="anaerobic" stroke="#ef4444" strokeWidth={2} fill="url(#gradAnae2)" name="Anaerobie" connectNulls />
                  </AreaChart>
                </ResponsiveContainer>
              </ChartCard>
            )}

          </div>
        </div>
      )}

      <ProgressInsights />
    </div>
  )
}
