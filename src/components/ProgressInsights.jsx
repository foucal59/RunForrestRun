import React, { useEffect, useMemo, useState } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  AreaChart, Area, LineChart, Line, ReferenceArea, ReferenceLine
} from 'recharts'
import { useActivities } from '../contexts/ActivityContext'
import { loadVo2max } from '../api'
import { computePaceStability, computeCardiacDecoupling, computeVolumeVsPerformance, fmtPace, fmtTime, localDateStr } from '../lib/compute'
import ChartCard from './ChartCard'
import StatCard from './StatCard'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle, Tip, COLORS } from '../lib/chartTheme'
import {
  PointGraphControls,
  filterByNumericDomains,
  numericZoomDomain,
  pointDot,
  pointId,
  scatterPoint,
  usePointGraph,
} from './PointGraphTools'

export default function ProgressInsights() {
  const { activities, effectiveDateRange } = useActivities()
  const [vo2History, setVo2History] = useState([])
  const [vo2Loading, setVo2Loading] = useState(true)

  useEffect(() => {
    let cancelled = false
    loadVo2max()
      .then(data => {
        if (!cancelled) setVo2History(Array.isArray(data?.history) ? data.history : [])
      })
      .finally(() => { if (!cancelled) setVo2Loading(false) })
    return () => { cancelled = true }
  }, [])

  const stab = useMemo(() => computePaceStability(activities), [activities])
  const card = useMemo(() => computeCardiacDecoupling(activities), [activities])
  const vp = useMemo(() => computeVolumeVsPerformance(activities), [activities])
  const efficiencyData = useMemo(() => card.filter(c => c.efficiency), [card])
  const cardiacData = useMemo(() => card.filter(c => c.avg_hr), [card])
  const stabilityPointGraph = usePointGraph(stab, { yKey: 'pace_s_km', better: 'lower', minVisiblePoints: 12 })
  const efficiencyPointGraph = usePointGraph(efficiencyData, { yKey: 'efficiency', better: 'higher', minVisiblePoints: 12 })
  const cardiacPointGraph = usePointGraph(cardiacData, { yKey: 'pace_s_km', better: 'lower', minVisiblePoints: 18 })
  const volumePerformancePointGraph = usePointGraph(vp, { yKey: 'time_10k', better: 'lower', minVisiblePoints: 18 })
  const cardiacBetterIds = useMemo(() => {
    const selected = cardiacPointGraph.selected
    if (!selected) return new Set()
    return new Set(cardiacData
      .filter(point =>
        pointId(point) !== cardiacPointGraph.selectedId &&
        point.avg_hr <= selected.avg_hr &&
        point.pace_s_km <= selected.pace_s_km)
      .map(point => pointId(point)))
  }, [cardiacData, cardiacPointGraph.selected, cardiacPointGraph.selectedId])
  const volumePerformanceBetterIds = useMemo(() => {
    const selected = volumePerformancePointGraph.selected
    if (!selected) return new Set()
    return new Set(vp
      .filter(point =>
        pointId(point) !== volumePerformancePointGraph.selectedId &&
        point.volume_30d_km >= selected.volume_30d_km &&
        point.time_10k <= selected.time_10k)
      .map(point => pointId(point)))
  }, [vp, volumePerformancePointGraph.selected, volumePerformancePointGraph.selectedId])
  const cardiacDomains = useMemo(() => ({
    x: numericZoomDomain(cardiacData, 'avg_hr', cardiacPointGraph.selected, cardiacPointGraph.zoomIndex, { minPad: 2, minValue: 0 }),
    y: numericZoomDomain(cardiacData, 'pace_s_km', cardiacPointGraph.selected, cardiacPointGraph.zoomIndex, { minPad: 10, minValue: 0 }),
  }), [cardiacData, cardiacPointGraph.selected, cardiacPointGraph.zoomIndex])
  const volumePerformanceDomains = useMemo(() => ({
    x: numericZoomDomain(vp, 'volume_30d_km', volumePerformancePointGraph.selected, volumePerformancePointGraph.zoomIndex, { minPad: 5, minValue: 0 }),
    y: numericZoomDomain(vp, 'time_10k', volumePerformancePointGraph.selected, volumePerformancePointGraph.zoomIndex, { minPad: 60, minValue: 0 }),
  }), [vp, volumePerformancePointGraph.selected, volumePerformancePointGraph.zoomIndex])
  const visibleCardiacData = useMemo(
    () => filterByNumericDomains(cardiacData, 'avg_hr', 'pace_s_km', cardiacDomains.x, cardiacDomains.y),
    [cardiacData, cardiacDomains]
  )
  const visibleVolumePerformanceData = useMemo(
    () => filterByNumericDomains(vp, 'volume_30d_km', 'time_10k', volumePerformanceDomains.x, volumePerformanceDomains.y),
    [vp, volumePerformanceDomains]
  )
  const cardiacSelectedVisible = useMemo(
    () => Boolean(cardiacPointGraph.selected && filterByNumericDomains([cardiacPointGraph.selected], 'avg_hr', 'pace_s_km', cardiacDomains.x, cardiacDomains.y).length),
    [cardiacDomains, cardiacPointGraph.selected]
  )
  const volumePerformanceSelectedVisible = useMemo(
    () => Boolean(volumePerformancePointGraph.selected && filterByNumericDomains([volumePerformancePointGraph.selected], 'volume_30d_km', 'time_10k', volumePerformanceDomains.x, volumePerformanceDomains.y).length),
    [volumePerformanceDomains, volumePerformancePointGraph.selected]
  )
  const vo2Data = useMemo(
    () => vo2History
      .filter(point => {
        if (!effectiveDateRange) return true
        const pointDate = String(point.date || '').slice(0, 10)
        return pointDate >= localDateStr(new Date(effectiveDateRange.from))
          && pointDate <= localDateStr(new Date(effectiveDateRange.to))
      })
      .map(point => ({ date: point.date, vo2max: Number(point.vo2max) }))
      .filter(point => point.date && Number.isFinite(point.vo2max))
      .sort((a, b) => String(a.date).localeCompare(String(b.date))),
    [vo2History, effectiveDateRange]
  )
  const vo2Stats = useMemo(() => {
    const values = vo2Data.map(point => point.vo2max).filter(value => value != null)
    if (!values.length) return { latest: null, trend: null, min: 0, max: 0 }
    return {
      latest: values[values.length - 1],
      trend: values.length > 1 ? +(values[values.length - 1] - values[0]).toFixed(1) : null,
      min: Math.floor(Math.min(...values) - 1),
      max: Math.ceil(Math.max(...values) + 1),
    }
  }, [vo2Data])

  return (
    <section className="mt-8 progress_analysis_section" data-name="progress_analysis_section">
      <hr className="border-surface-border mb-6 progress_analysis_section_divider" data-name="progress_analysis_section_divider" />
      <h3 className="text-lg font-semibold text-txt mb-6 progress_analysis_section_title" data-name="progress_analysis_section_title">VO2max et analyse avancée</h3>

      <section className="mb-6 analyse_vo2max_section" data-name="analyse_vo2max_section">
        <h4 className="text-sm font-semibold text-txt-secondary mb-3 analyse_vo2max_title" data-name="analyse_vo2max_title">VO2max</h4>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 sm:gap-4 mb-4 analyse_vo2max_stats" data-name="analyse_vo2max_stats">
          <StatCard label="VO2max actuel" value={vo2Stats.latest ?? '—'} name="analyse_vo2max_stat_latest" />
          <StatCard label="Mesures" value={vo2Data.length} name="analyse_vo2max_stat_count" />
          <StatCard
            label="Évolution"
            value={vo2Stats.trend != null ? `${vo2Stats.trend > 0 ? '+' : ''}${vo2Stats.trend}` : '—'}
            name="analyse_vo2max_stat_trend"
          />
        </div>
        {vo2Loading ? (
          <div className="card h-[360px] animate-pulse bg-surface-muted analyse_vo2max_loading" data-name="analyse_vo2max_loading" />
        ) : vo2Data.length ? (
          <ChartCard title="Évolution du VO2max" subtitle="Estimation Garmin" name="analyse_vo2max_chart">
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={vo2Data} margin={{ top: 8, right: 16, bottom: 8, left: -8 }}>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="date" tick={axisStyle} minTickGap={32} />
                <YAxis domain={[vo2Stats.min, vo2Stats.max]} tick={axisStyle} allowDecimals={false} />
                <Tooltip content={<Tip />} />
                <Line
                  type="monotone"
                  dataKey="vo2max"
                  name="VO2max"
                  stroke={COLORS.brand}
                  strokeWidth={2.5}
                  dot={false}
                  connectNulls
                  animationDuration={1000}
                />
              </LineChart>
            </ResponsiveContainer>
          </ChartCard>
        ) : (
          <div className="card text-center py-10 text-sm text-txt-muted analyse_vo2max_empty" data-name="analyse_vo2max_empty">
            Aucune donnée VO2max sur la période sélectionnée.
          </div>
        )}
      </section>

      <h4 className="text-sm font-semibold text-txt-secondary mb-3 analyse_advanced_title" data-name="analyse_advanced_title">Analyse avancée</h4>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 analyse_charts_grid" data-name="analyse_charts_grid">
        {stab.length > 0 && (
          <ChartCard title="Stabilite d'allure" subtitle="100 derniers runs" name="analyse_pace_stability_chart">
            <PointGraphControls graph={stabilityPointGraph} className="mb-2" />
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={stabilityPointGraph.visibleData}>
                <defs>
                  <linearGradient id="gradPace" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={COLORS.brand} stopOpacity={0.25} />
                    <stop offset="100%" stopColor={COLORS.brand} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="date" tick={axisStyle} tickFormatter={d => d?.slice(5,10)} />
                <YAxis tick={axisStyle} tickFormatter={v => fmtPace(v)} reversed domain={['dataMin-10','dataMax+10']} />
                <Tooltip content={({ active, payload }) => {
                  if (!active || !payload?.length) return null; const d = payload[0]?.payload
                  return (<div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg analyse_pace_stability_chart_tooltip" data-name="analyse_pace_stability_chart_tooltip">
                    <div className="text-xs text-txt-secondary font-medium analyse_pace_stability_chart_tooltip_slice_label" data-name="analyse_pace_stability_chart_tooltip_slice_label">{d?.date?.slice(0,10)}</div>
                    <div className="text-sm text-txt analyse_pace_stability_chart_tooltip_name_text" data-name="analyse_pace_stability_chart_tooltip_name_text">{d?.name}</div>
                    <div className="text-sm font-mono analyse_pace_stability_chart_tooltip_pace_value" data-name="analyse_pace_stability_chart_tooltip_pace_value" style={{color:COLORS.brand}}>{fmtPace(d?.pace_s_km)}/km</div>
                    <div className="text-xs text-txt-muted analyse_pace_stability_chart_tooltip_distance_meta" data-name="analyse_pace_stability_chart_tooltip_distance_meta">{d?.distance_km} km</div>
                  </div>)
                }} />
                {stabilityPointGraph.selectedVisible && (
                  <>
                    <ReferenceLine x={stabilityPointGraph.selected.date} stroke="#f59e0b" strokeDasharray="5 4" />
                    <ReferenceLine y={stabilityPointGraph.selected.pace_s_km} stroke="#f59e0b" strokeDasharray="5 4" />
                  </>
                )}
                <Area
                  type="monotone"
                  dataKey="pace_s_km"
                  stroke={COLORS.brand}
                  strokeWidth={2}
                  fill="url(#gradPace)"
                  dot={pointDot({
                    selectedId: stabilityPointGraph.selectedId,
                    betterIds: stabilityPointGraph.betterIds,
                    onSelect: stabilityPointGraph.selectPoint,
                    color: COLORS.brand,
                    radius: 2,
                  })}
                  activeDot={false}
                  connectNulls
                  animationDuration={1000}
                />
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        )}

        {efficiencyData.length > 0 && (
          <ChartCard title="Indice d'efficacite" subtitle="Vitesse / FC (plus haut = meilleur)" name="analyse_efficiency_chart">
            <PointGraphControls graph={efficiencyPointGraph} className="mb-2" />
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={efficiencyPointGraph.visibleData}>
                <defs>
                  <linearGradient id="gradEff" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="date" tick={axisStyle} tickFormatter={d => d?.slice(5,10)} />
                <YAxis tick={axisStyle} />
                <Tooltip content={({ active, payload }) => {
                  if (!active || !payload?.length) return null; const d = payload[0]?.payload
                  return (<div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg analyse_efficiency_chart_tooltip" data-name="analyse_efficiency_chart_tooltip">
                    <div className="text-xs text-txt-secondary font-medium analyse_efficiency_chart_tooltip_slice_label" data-name="analyse_efficiency_chart_tooltip_slice_label">{d?.date?.slice(0,10)}</div>
                    <div className="text-sm font-mono text-emerald-600 analyse_efficiency_chart_tooltip_efficiency_value" data-name="analyse_efficiency_chart_tooltip_efficiency_value">Efficacite: {d?.efficiency?.toFixed(4)}</div>
                    <div className="text-xs text-txt-muted analyse_efficiency_chart_tooltip_fc_avg_hr_pace_s_km_meta" data-name="analyse_efficiency_chart_tooltip_fc_avg_hr_pace_s_km_meta">FC: {d?.avg_hr} | {fmtPace(d?.pace_s_km)}/km</div>
                  </div>)
                }} />
                {efficiencyPointGraph.selectedVisible && (
                  <>
                    <ReferenceLine x={efficiencyPointGraph.selected.date} stroke="#f59e0b" strokeDasharray="5 4" />
                    <ReferenceLine y={efficiencyPointGraph.selected.efficiency} stroke="#f59e0b" strokeDasharray="5 4" />
                  </>
                )}
                <Area
                  type="monotone"
                  dataKey="efficiency"
                  stroke="#10b981"
                  strokeWidth={2}
                  fill="url(#gradEff)"
                  dot={pointDot({
                    selectedId: efficiencyPointGraph.selectedId,
                    betterIds: efficiencyPointGraph.betterIds,
                    onSelect: efficiencyPointGraph.selectPoint,
                    color: '#10b981',
                    radius: 2,
                  })}
                  activeDot={false}
                  connectNulls
                  animationDuration={1000}
                />
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        )}

        {cardiacData.length > 0 && (
          <ChartCard title="Decouplage cardiaque" subtitle="Allure vs FC" name="analyse_cardiac_decoupling_chart">
            <PointGraphControls graph={cardiacPointGraph} className="mb-2" />
            <ResponsiveContainer width="100%" height={300}>
              <ScatterChart>
                <CartesianGrid {...gridStyle} />
                <XAxis type="number" dataKey="avg_hr" tick={axisStyle} name="FC moy." unit=" bpm" domain={cardiacDomains.x} allowDataOverflow />
                <YAxis type="number" dataKey="pace_s_km" tick={axisStyle} tickFormatter={v => fmtPace(v)} reversed name="Allure" domain={cardiacDomains.y} allowDataOverflow />
                {cardiacSelectedVisible && (
                  <>
                    <ReferenceArea
                      x1={cardiacDomains.x[0]}
                      x2={cardiacPointGraph.selected.avg_hr}
                      y1={cardiacDomains.y[0]}
                      y2={cardiacPointGraph.selected.pace_s_km}
                      fill="#10b981"
                      fillOpacity={0.08}
                    />
                    <ReferenceLine x={cardiacPointGraph.selected.avg_hr} stroke="#f59e0b" strokeDasharray="5 4" />
                    <ReferenceLine y={cardiacPointGraph.selected.pace_s_km} stroke="#f59e0b" strokeDasharray="5 4" />
                  </>
                )}
                <Tooltip content={({ active, payload }) => {
                  if (!active || !payload?.length) return null; const d = payload[0]?.payload
                  return (<div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg analyse_cardiac_decoupling_chart_tooltip" data-name="analyse_cardiac_decoupling_chart_tooltip">
                    <div className="text-xs text-txt-secondary font-medium analyse_cardiac_decoupling_chart_tooltip_slice_label" data-name="analyse_cardiac_decoupling_chart_tooltip_slice_label">{d?.date?.slice(0,10)}</div>
                    <div className="text-sm font-mono text-blue-600 analyse_cardiac_decoupling_chart_tooltip_pace_hr_value" data-name="analyse_cardiac_decoupling_chart_tooltip_pace_hr_value">{fmtPace(d?.pace_s_km)}/km @ {d?.avg_hr} bpm</div>
                  </div>)
                }} />
                <Scatter
                  data={visibleCardiacData}
                  fill="#3b82f6"
                  fillOpacity={0.5}
                  shape={scatterPoint({
                    selectedId: cardiacPointGraph.selectedId,
                    betterIds: cardiacBetterIds,
                    onSelect: cardiacPointGraph.selectPoint,
                    color: '#3b82f6',
                    radius: 4,
                  })}
                  animationDuration={800}
                />
              </ScatterChart>
            </ResponsiveContainer>
          </ChartCard>
        )}

        {vp.length > 0 && (
          <ChartCard title="Volume 30j vs Performance 10k" name="analyse_volume_vs_performance_chart">
            <PointGraphControls graph={volumePerformancePointGraph} className="mb-2" />
            <ResponsiveContainer width="100%" height={300}>
              <ScatterChart>
                <CartesianGrid {...gridStyle} />
                <XAxis type="number" dataKey="volume_30d_km" tick={axisStyle} name="Vol. 30j" unit=" km" domain={volumePerformanceDomains.x} allowDataOverflow />
                <YAxis type="number" dataKey="time_10k" tick={axisStyle} tickFormatter={v => fmtTime(v)} reversed name="Temps 10k" domain={volumePerformanceDomains.y} allowDataOverflow />
                {volumePerformanceSelectedVisible && (
                  <>
                    <ReferenceArea
                      x1={volumePerformancePointGraph.selected.volume_30d_km}
                      x2={volumePerformanceDomains.x[1]}
                      y1={volumePerformanceDomains.y[0]}
                      y2={volumePerformancePointGraph.selected.time_10k}
                      fill="#10b981"
                      fillOpacity={0.08}
                    />
                    <ReferenceLine x={volumePerformancePointGraph.selected.volume_30d_km} stroke="#f59e0b" strokeDasharray="5 4" />
                    <ReferenceLine y={volumePerformancePointGraph.selected.time_10k} stroke="#f59e0b" strokeDasharray="5 4" />
                  </>
                )}
                <Tooltip content={({ active, payload }) => {
                  if (!active || !payload?.length) return null; const d = payload[0]?.payload
                  return (<div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg analyse_volume_vs_performance_chart_tooltip" data-name="analyse_volume_vs_performance_chart_tooltip">
                    <div className="text-xs text-txt-secondary font-medium analyse_volume_vs_performance_chart_tooltip_date_label" data-name="analyse_volume_vs_performance_chart_tooltip_date_label">{d?.date}</div>
                    <div className="text-sm text-txt font-mono analyse_volume_vs_performance_chart_tooltip_10k_formatted_value" data-name="analyse_volume_vs_performance_chart_tooltip_10k_formatted_value">10k: {d?.formatted}</div>
                    <div className="text-xs text-txt-muted analyse_volume_vs_performance_chart_tooltip_volume_30d_meta" data-name="analyse_volume_vs_performance_chart_tooltip_volume_30d_meta">Vol 30j: {d?.volume_30d_km} km</div>
                  </div>)
                }} />
                <Scatter
                  data={visibleVolumePerformanceData}
                  fill="#f59e0b"
                  fillOpacity={0.6}
                  shape={scatterPoint({
                    selectedId: volumePerformancePointGraph.selectedId,
                    betterIds: volumePerformanceBetterIds,
                    onSelect: volumePerformancePointGraph.selectPoint,
                    color: '#f59e0b',
                    radius: 4,
                  })}
                  animationDuration={800}
                />
              </ScatterChart>
            </ResponsiveContainer>
          </ChartCard>
        )}
      </div>
    </section>
  )
}
