import React, { useState, useEffect, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'
import posthog from 'posthog-js'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceDot, ReferenceLine,
  ResponsiveContainer, PieChart, Pie, Cell
} from 'recharts'
import { ArrowLeft } from 'lucide-react'
import { useActivities } from '../contexts/ActivityContext'
import { getActivityStreams } from '../api'
import { ZONE_COLORS } from '../lib/heartRateZones'
import { fmtEffortTime } from '../lib/bestEfforts'
import { parseLocalDate, fmtPaceFromSpeed as fmtPace, fmtTime as fmtDuration, buildRunExtraStats } from '../lib/compute'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle } from '../lib/chartTheme'
import { formatChartMinutes } from '../lib/streamCursor'
import { useRunStreams } from '../lib/useRunStreams'
import ChartCard from '../components/ChartCard'
import RunMap from '../components/RunMap'
import Loader from '../components/Loader'
import RunSplitSummary from '../components/RunSplitSummary'

export default function ActivityDetail() {
  const { id } = useParams()
  const { allActivities } = useActivities()
  const [streams, setStreams] = useState(null)
  const [zoneData, setZoneData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const activity = useMemo(() =>
    allActivities.find(a => String(a.id) === id),
    [allActivities, id]
  )

  const {
    zones, timeInZones, relativeEffort, bestEfforts, speedData, speedDomain, speedMax, hrData,
    activeStreamPoint, activeSpeedPoint, activeHrPoint,
    handleChartMouseMove, clearActiveStreamIndex, handleTraceHover, mapRun,
  } = useRunStreams({ activity, allActivities, streams, zoneData, activityId: id, samples: 500 })

  // Effort relatif : calculé depuis les streams FC quand ils existent, sinon
  // repli sur le suffer_score stocké en base (runs Strava sans streams).
  const displayedEffort = relativeEffort > 0 ? relativeEffort : Math.round(activity?.suffer_score || 0)

  useEffect(() => {
    if (!id) return
    posthog.capture('activity_detail_viewed', { activity_id: id })
    // Clear the previous activity's streams so its map/charts don't linger on
    // the new one while the fetch is in flight.
    setStreams(null)
    setZoneData(null)
    setError(null)
    clearActiveStreamIndex()
    setLoading(true)
    getActivityStreams(id)
      .then(data => {
        setStreams(data?.streams || null)
        setZoneData(data?.zones || null)
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false))
  }, [id])

  if (!activity) return <div className="text-txt-muted activity_detail_activite_introuvable_meta" data-name="activity_detail_activite_introuvable_meta">Activite introuvable.</div>

  return (
    <div data-name="page_activity_detail">
      <Link to="/" className="inline-flex items-center gap-2 text-sm text-txt-secondary hover:text-primary mb-4 transition-colors activity_detail_back_link" data-name="activity_detail_back_link">
        <ArrowLeft size={16} /> Retour au cockpit
      </Link>

      <div className="flex items-start justify-between mb-4 sm:mb-6 gap-3 activity_detail_header" data-name="activity_detail_header">
        <div className="min-w-0 activity_detail_header_title_block" data-name="activity_detail_header_title_block">
          <h2 className="text-lg sm:text-xl font-semibold text-txt truncate activity_detail_header_title_block_name_title" data-name="activity_detail_header_title_block_name_title">{activity.name}</h2>
          <p className="text-xs sm:text-sm text-txt-muted mt-1 activity_detail_header_title_block_fr_fr_description" data-name="activity_detail_header_title_block_fr_fr_description">
            {parseLocalDate(activity.start_date_local).toLocaleDateString('fr-FR', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}
          </p>
          {activity.description && (
            <p className="text-xs sm:text-sm text-txt-muted italic whitespace-pre-line mt-1 activity_detail_description" data-name="activity_detail_description">{activity.description}</p>
          )}
        </div>
        <div className="flex gap-2 flex-shrink-0 activity_detail_header_badges" data-name="activity_detail_header_badges">
          {displayedEffort > 0 && (
            <div className="card px-3 sm:px-4 py-2 activity_detail_header_effort" data-name="activity_detail_header_effort">
              <div className="text-xs text-txt-muted activity_detail_header_effort_effort_meta" data-name="activity_detail_header_effort_effort_meta">Effort</div>
              <div className="text-xl sm:text-2xl font-mono font-semibold text-primary activity_detail_header_effort_relative_effort_value" data-name="activity_detail_header_effort_relative_effort_value">{displayedEffort}</div>
            </div>
          )}
        </div>
      </div>

      {/* Key Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 sm:gap-4 mb-6 sm:mb-8 activity_detail_stats" data-name="activity_detail_stats">
        {[
          { label: 'Distance', value: `${(activity.distance / 1000).toFixed(2)}`, unit: 'km' },
          { label: 'Duree', value: fmtDuration(activity.moving_time) },
          { label: 'Allure moy.', value: fmtPace(activity.average_speed), unit: '/km' },
          { label: 'FC moy.', value: activity.average_heartrate || '-', unit: activity.average_heartrate ? 'bpm' : '' },
          { label: 'D+', value: Math.round(activity.total_elevation_gain), unit: 'm' },
          // Stats secondaires (colonnes DB historiquement non affichées).
          // speedMax (pic de la courbe lissée) remplace le max_speed Garmin
          // dès que les streams sont chargés.
          ...buildRunExtraStats(activity, { maxSpeed: speedMax }),
        ].map(s => (
          <div key={s.label} className="card activity_detail_stat_card" data-name={`activity_detail_stat_card_${s.label}`}>
            <div className="text-xs text-txt-muted uppercase tracking-wider activity_detail_stat_card_label" data-name="activity_detail_stat_card_label">{s.label}</div>
            <div className="text-xl font-mono font-semibold text-txt mt-1 activity_detail_stat_card_value_unit_value" data-name="activity_detail_stat_card_value_unit_value">
              {s.value} {s.unit && <span className="text-sm text-txt-secondary activity_detail_stat_card_value_unit_value_unit_text" data-name="activity_detail_stat_card_value_unit_value_unit_text">{s.unit}</span>}
            </div>
          </div>
        ))}
      </div>

      <RunSplitSummary activity={activity} allActivities={allActivities} className="mb-6 sm:mb-8" />

      {loading && <Loader />}
      {error && <div className="text-red-500 text-sm mb-4 page_activity_detail_erreur_chargement_streams_error_section" data-name="page_activity_detail_erreur_chargement_streams_error_section">Erreur chargement streams: {error}</div>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 mb-6 page_activity_detail_grid" data-name="page_activity_detail_grid">
        {/* Map */}
        {mapRun.length > 0 && (
          <div className="card activity_detail_map" data-name="activity_detail_map">
            <h3 className="text-sm font-medium text-txt-secondary mb-4 activity_detail_map_parcours_title" data-name="activity_detail_map_parcours_title">Parcours</h3>
            <RunMap
              runs={mapRun}
              height={300}
              activePoint={activeStreamPoint?.latlng}
              onTraceHover={handleTraceHover}
              onTraceLeave={clearActiveStreamIndex}
            />
          </div>
        )}

        {/* HR Zone Donut */}
        {timeInZones.length > 0 && (
          <ChartCard title="Repartition par zone FC" name="activity_detail_hr_zones">
            <div className="flex items-center gap-6 activity_detail_hr_zones_inner" data-name="activity_detail_hr_zones_inner">
              <ResponsiveContainer width="50%" height={220}>
                <PieChart>
                  <Pie
                    data={timeInZones.filter(z => z.seconds > 0)}
                    dataKey="seconds"
                    nameKey="label"
                    cx="50%" cy="50%"
                    innerRadius={55} outerRadius={90}
                    paddingAngle={2}
                  >
                    {timeInZones.filter(z => z.seconds > 0).map((z, i) => (
                      <Cell key={z.zone} fill={z.color} />
                    ))}
                  </Pie>
                  <Tooltip content={({ active, payload }) => {
                    if (!active || !payload?.length) return null
                    const d = payload[0]?.payload
                    return (
                      <div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg activity_detail_hr_zones_inner_tooltip" data-name="activity_detail_hr_zones_inner_tooltip">
                        <div className="text-sm font-medium text-txt activity_detail_hr_zones_inner_tooltip_zone_label" data-name="activity_detail_hr_zones_inner_tooltip_zone_label">{d?.fullLabel}</div>
                        <div className="text-xs text-txt-secondary activity_detail_hr_zones_inner_tooltip_seconds_pct_section" data-name="activity_detail_hr_zones_inner_tooltip_seconds_pct_section">{fmtDuration(d?.seconds)} ({d?.pct}%)</div>
                        <div className="text-xs text-txt-muted activity_detail_hr_zones_inner_tooltip_range_bpm_meta" data-name="activity_detail_hr_zones_inner_tooltip_range_bpm_meta">{d?.range} bpm</div>
                      </div>
                    )
                  }} />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-2 activity_detail_hr_zones_inner_list" data-name="activity_detail_hr_zones_inner_list">
                {timeInZones.map(z => (
                  <div key={z.zone} className="flex items-center gap-3 activity_detail_hr_zones_inner_list_section" data-name="activity_detail_hr_zones_inner_list_section">
                    <div className="w-3 h-3 rounded-full activity_detail_hr_zones_inner_list_zone_dot" data-name="activity_detail_hr_zones_inner_list_zone_dot" style={{ backgroundColor: z.color }} />
                    <div className="flex-1 activity_detail_hr_zones_inner_list_zone_details" data-name="activity_detail_hr_zones_inner_list_zone_details">
                      <div className="text-xs font-medium text-txt activity_detail_hr_zones_inner_list_zone_label" data-name="activity_detail_hr_zones_inner_list_zone_label">{z.label}</div>
                      <div className="text-xs text-txt-muted activity_detail_hr_zones_inner_list_zone_duration_meta" data-name="activity_detail_hr_zones_inner_list_zone_duration_meta">{fmtDuration(z.seconds)}</div>
                    </div>
                    <div className="text-sm font-mono font-medium text-txt activity_detail_hr_zones_inner_list_section_pct_value" data-name="activity_detail_hr_zones_inner_list_section_pct_value">{z.pct}%</div>
                  </div>
                ))}
              </div>
            </div>
          </ChartCard>
        )}

        {/* Speed Chart — même grille que la carte : sans donnée FC (pas de
            donut ni graphe FC), il vient se placer à côté du parcours au lieu
            de laisser un trou. */}
        {speedData.length > 0 && (
          <ChartCard title="Vitesse" subtitle="Distance vs Allure" name="activity_detail_chart_speed">
            <ResponsiveContainer width="100%" height={250} className="activity_detail_chart_speed_container">
              <AreaChart data={speedData} onMouseMove={handleChartMouseMove} onMouseLeave={clearActiveStreamIndex}>
                <defs>
                  <linearGradient id="gradSpeed" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#2563EB" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#2563EB" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="distance" type="number" tick={axisStyle} unit=" km" domain={['dataMin', 'dataMax']} />
                <YAxis tick={axisStyle} tickFormatter={v => fmtPace(v)} domain={speedDomain} allowDataOverflow />
                <Tooltip content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const d = payload[0]?.payload
                  return (
                    <div className="bg-white border border-surface-border rounded-xl px-3 py-2 shadow-lg activity_detail_chart_speed_tooltip" data-name="activity_detail_chart_speed_tooltip">
                      <div className="text-xs text-txt-secondary activity_detail_chart_speed_tooltip_to_fixed_km_value" data-name="activity_detail_chart_speed_tooltip_to_fixed_km_value">{d?.distance?.toFixed(1)} km</div>
                      <div className="text-sm font-mono text-primary activity_detail_chart_speed_tooltip_pace_km_value" data-name="activity_detail_chart_speed_tooltip_pace_km_value">{d?.pace}/km</div>
                    </div>
                  )
                }} />
                {/* isAnimationActive={false} : avec allowDataOverflow, le
                    clipPath d'animation Recharts reste parfois à width=0 et
                    la courbe devient invisible (bug connu). */}
                <Area type="monotone" dataKey="speed" stroke="#2563EB" strokeWidth={1.5} fill="url(#gradSpeed)" dot={false} isAnimationActive={false} />
                {activeSpeedPoint?.distance != null && activeSpeedPoint?.speed != null && (
                  <>
                    <ReferenceLine x={activeSpeedPoint.distance} stroke="#2563EB" strokeOpacity={0.3} strokeDasharray="3 3" ifOverflow="extendDomain" />
                    <ReferenceDot x={activeSpeedPoint.distance} y={activeSpeedPoint.speed} r={4} fill="#2563EB" stroke="#ffffff" strokeWidth={2} isFront ifOverflow="extendDomain" />
                  </>
                )}
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        )}

        {/* HR Chart */}
        {hrData.length > 0 && (
          <ChartCard title="Frequence cardiaque" subtitle="Temps vs FC" name="activity_detail_chart_hr">
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={hrData} onMouseMove={handleChartMouseMove} onMouseLeave={clearActiveStreamIndex}>
                <defs>
                  <linearGradient id="gradHR" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#EF4444" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#EF4444" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="time" type="number" tick={axisStyle} unit=" min" domain={['dataMin', 'dataMax']} />
                <YAxis tick={axisStyle} domain={['dataMin - 5', 'dataMax + 5']} />
                <Tooltip content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const d = payload[0]?.payload
                  return (
                    <div className="bg-white border border-surface-border rounded-xl px-3 py-2 shadow-lg activity_detail_chart_hr_tooltip" data-name="activity_detail_chart_hr_tooltip">
                      <div className="text-xs text-txt-secondary activity_detail_chart_hr_tooltip_time_min_section" data-name="activity_detail_chart_hr_tooltip_time_min_section">{formatChartMinutes(d?.time)}</div>
                      <div className="text-sm font-mono text-red-500 activity_detail_chart_hr_tooltip_hr_bpm_value" data-name="activity_detail_chart_hr_tooltip_hr_bpm_value">{d?.hr} bpm</div>
                    </div>
                  )
                }} />
                <Area type="monotone" dataKey="hr" stroke="#EF4444" strokeWidth={1.5} fill="url(#gradHR)" dot={false} />
                {activeHrPoint?.time != null && activeHrPoint?.hr != null && (
                  <>
                    <ReferenceLine x={activeHrPoint.time} stroke="#EF4444" strokeOpacity={0.3} strokeDasharray="3 3" ifOverflow="extendDomain" />
                    <ReferenceDot x={activeHrPoint.time} y={activeHrPoint.hr} r={4} fill="#EF4444" stroke="#ffffff" strokeWidth={2} isFront ifOverflow="extendDomain" />
                  </>
                )}
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        )}
      </div>

      {/* Best Efforts Table */}
      {bestEfforts.distances.length > 0 && (
        <div className="card mb-6 activity_detail_best_efforts" data-name="activity_detail_best_efforts">
          <h3 className="text-sm font-medium text-txt-secondary mb-4 activity_detail_best_efforts_meilleurs_efforts_cette_activite_title" data-name="activity_detail_best_efforts_meilleurs_efforts_cette_activite_title">Meilleurs efforts (cette activite)</h3>
          <div className="overflow-x-auto activity_detail_best_efforts_scroller" data-name="activity_detail_best_efforts_scroller">
            <table className="w-full text-sm activity_detail_best_efforts_table" data-name="activity_detail_best_efforts_table">
              <thead>
                <tr className="text-left text-txt-muted border-b border-surface-border activity_detail_best_efforts_table_table_header_row" data-name="activity_detail_best_efforts_table_table_header_row">
                  <th className="pb-3 font-medium activity_detail_best_efforts_table_table_header_row_distance_cell" data-name="activity_detail_best_efforts_table_table_header_row_distance_cell">Distance</th>
                  <th className="pb-3 font-medium activity_detail_best_efforts_table_table_header_row_temps_cell" data-name="activity_detail_best_efforts_table_table_header_row_temps_cell">Temps</th>
                  <th className="pb-3 font-medium activity_detail_best_efforts_table_table_header_row_allure_cell" data-name="activity_detail_best_efforts_table_table_header_row_allure_cell">Allure</th>
                </tr>
              </thead>
              <tbody>
                {bestEfforts.distances.map(d => (
                  <tr key={d.key} className="border-b border-surface-border activity_detail_best_efforts_row" data-name={`activity_detail_best_efforts_row_${d.key}`}>
                    <td className="py-2.5 text-txt font-medium activity_detail_best_efforts_row_label_cell" data-name="activity_detail_best_efforts_row_label_cell">{d.label}</td>
                    <td className="py-2.5 text-txt font-mono activity_detail_best_efforts_row_formatted_cell" data-name="activity_detail_best_efforts_row_formatted_cell">{d.formatted}</td>
                    <td className="py-2.5 text-txt-secondary font-mono activity_detail_best_efforts_row_pace_cell" data-name="activity_detail_best_efforts_row_pace_cell">{d.pace}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

    </div>
  )
}
