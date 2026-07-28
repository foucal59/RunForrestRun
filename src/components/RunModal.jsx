import React, { useEffect, useState, useMemo, useCallback } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { X, ExternalLink, Trash2 } from 'lucide-react'
import { AreaChart, Area, XAxis, YAxis, ResponsiveContainer, CartesianGrid, Tooltip, ReferenceDot, ReferenceLine } from 'recharts'
import { useActivities } from '../contexts/ActivityContext'
import { getActivityStreams } from '../api'
import { parseLocalDate, fmtTime, fmtPaceFromSpeed as fmtSpeed, buildRunExtraStats } from '../lib/compute'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle } from '../lib/chartTheme'
import { formatChartMinutes } from '../lib/streamCursor'
import { useRunStreams } from '../lib/useRunStreams'
import RunMap from './RunMap'
import Loader from './Loader'
import RunSplitSummary from './RunSplitSummary'

export default function RunModal() {
  const [searchParams, setSearchParams] = useSearchParams()
  const runId = searchParams.get('run')
  const { allActivities, deleteActivity } = useActivities()
  const [streams, setStreams] = useState(null)
  // 'idle' | 'confirm' | 'deleting'
  const [deleteState, setDeleteState] = useState('idle')
  const [zoneData, setZoneData] = useState(null)
  const [loading, setLoading] = useState(false)
  const activity = useMemo(() =>
    allActivities.find(a => String(a.id) === runId),
    [allActivities, runId]
  )

  const {
    zones, timeInZones, relativeEffort, bestEfforts, speedData, speedDomain, speedMax, hrData,
    activeStreamPoint, activeSpeedPoint, activeHrPoint,
    handleChartMouseMove, clearActiveStreamIndex, handleTraceHover, mapRun,
  } = useRunStreams({ activity, allActivities, streams, zoneData, activityId: runId, samples: 200 })

  // Effort relatif : calculé depuis les streams FC quand ils existent, sinon
  // repli sur le suffer_score stocké en base (runs Strava sans streams).
  const displayedEffort = relativeEffort > 0 ? relativeEffort : Math.round(activity?.suffer_score || 0)

  useEffect(() => {
    if (!runId) { setStreams(null); setZoneData(null); return }
    // Clear the previous run's data immediately so its map/charts never linger
    // on the new run while the fetch is in flight.
    setStreams(null)
    setZoneData(null)
    clearActiveStreamIndex()
    setLoading(true)
    getActivityStreams(runId)
      .then(data => {
        setStreams(data?.streams || null)
        setZoneData(data?.zones || null)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [runId])

  const close = useCallback(() => {
    const next = new URLSearchParams(searchParams)
    next.delete('run')
    setSearchParams(next, { replace: true })
    setDeleteState('idle')
  }, [searchParams, setSearchParams])

  // Lock body scroll when modal is open
  useEffect(() => {
    if (runId) document.body.style.overflow = 'hidden'
    else document.body.style.overflow = ''
    return () => { document.body.style.overflow = '' }
  }, [runId])

  // Close on Escape key
  useEffect(() => {
    if (!runId) return
    function handleKeyDown(e) {
      if (e.key === 'Escape') {
        console.log('[RunModal] Escape pressed, closing modal')
        close()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [runId, close])

  async function handleDelete() {
    if (deleteState === 'idle') {
      console.log('[RunModal] Requesting delete confirmation for activity', runId)
      setDeleteState('confirm')
      return
    }
    console.log('[RunModal] Confirmed delete for activity', runId)
    setDeleteState('deleting')
    try {
      await deleteActivity(Number(runId))
      close()
    } catch (e) {
      console.error('[RunModal] Delete failed:', e)
      setDeleteState('idle')
    }
  }

  if (!runId || !activity) return null

  return (
    <div className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center run_modal" data-name="run_modal" onClick={close}>
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm run_modal_backdrop" data-name="run_modal_backdrop" />

      <div
        className="relative bg-white rounded-t-2xl sm:rounded-2xl w-full sm:max-w-2xl max-h-[90vh] overflow-y-auto shadow-2xl animate-slide-up run_modal_dialog"
        data-name="run_modal_dialog"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 bg-white/95 backdrop-blur-sm border-b border-surface-border px-5 py-4 flex items-start justify-between rounded-t-2xl z-10 run_modal_header" data-name="run_modal_header">
          <div className="min-w-0 flex-1 mr-3 run_modal_title_block" data-name="run_modal_title_block">
            <h3 className="text-base font-semibold text-txt truncate run_modal_title" data-name="run_modal_title">{activity.name}</h3>
            <p className="text-xs text-txt-muted mt-0.5 run_modal_subtitle" data-name="run_modal_subtitle">
              {parseLocalDate(activity.start_date_local).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
            </p>
          </div>
          <div className="flex items-center gap-1.5 flex-shrink-0 run_modal_actions" data-name="run_modal_actions">
            <Link to={`/activity/${activity.id}`} onClick={close}
              className="p-1.5 rounded-lg hover:bg-surface-hover transition-colors text-txt-muted hover:text-txt run_modal_open_full_button" data-name="run_modal_open_full_button" title="Page complete">
              <ExternalLink size={16} />
            </Link>
            {/* Delete button */}
            {deleteState !== 'idle' ? (
              <div className="flex items-center gap-1 run_modal_delete_confirm" data-name="run_modal_delete_confirm">
                <span className="text-xs text-red-500 font-medium run_modal_delete_confirm_label" data-name="run_modal_delete_confirm_label">Supprimer ?</span>
                <button
                  onClick={handleDelete}
                  disabled={deleteState === 'deleting'}
                  className="px-2 py-1 rounded-lg text-xs font-medium bg-red-500 text-white hover:bg-red-600 transition-colors disabled:opacity-50 run_modal_delete_confirm_yes"
                  data-name="run_modal_delete_confirm_yes"
                >
                  {deleteState === 'deleting' ? '...' : 'Oui'}
                </button>
                <button
                  onClick={() => setDeleteState('idle')}
                  className="px-2 py-1 rounded-lg text-xs font-medium bg-surface-muted text-txt-muted hover:text-txt transition-colors run_modal_delete_confirm_no"
                  data-name="run_modal_delete_confirm_no"
                >
                  Non
                </button>
              </div>
            ) : (
              <button
                onClick={handleDelete}
                className="p-1.5 rounded-lg hover:bg-red-50 transition-colors text-txt-muted hover:text-red-500 run_modal_delete_button"
                data-name="run_modal_delete_button"
                title="Supprimer du tableau de bord (ne supprime pas sur Garmin)"
              >
                <Trash2 size={16} />
              </button>
            )}
            <button onClick={close}
              className="p-1.5 rounded-lg hover:bg-surface-hover transition-colors text-txt-muted hover:text-txt run_modal_close_button"
              data-name="run_modal_close_button">
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="px-5 py-4 space-y-4 run_modal_content" data-name="run_modal_content">
          {/* Key stats */}
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 run_modal_stats_grid" data-name="run_modal_stats_grid">
            <div className="bg-surface-muted rounded-xl p-3 run_modal_stat_card" data-name="run_modal_stat_card">
              <div className="text-[10px] text-txt-muted uppercase run_modal_stat_label" data-name="run_modal_stat_label">Distance</div>
              <div className="text-lg font-mono font-semibold text-txt mt-0.5 run_modal_stat_value" data-name="run_modal_stat_value">{(activity.distance / 1000).toFixed(2)} <span className="text-xs text-txt-secondary run_modal_stat_value_km_text" data-name="run_modal_stat_value_km_text">km</span></div>
            </div>
            <div className="bg-surface-muted rounded-xl p-3 run_modal_stat_card" data-name="run_modal_stat_card">
              <div className="text-[10px] text-txt-muted uppercase run_modal_stat_label" data-name="run_modal_stat_label">Duree</div>
              <div className="text-lg font-mono font-semibold text-txt mt-0.5 run_modal_stat_value" data-name="run_modal_stat_value">{fmtTime(activity.moving_time)}</div>
            </div>
            <div className="bg-surface-muted rounded-xl p-3 run_modal_stat_card" data-name="run_modal_stat_card">
              <div className="text-[10px] text-txt-muted uppercase run_modal_stat_label" data-name="run_modal_stat_label">Allure moy.</div>
              <div className="text-lg font-mono font-semibold text-txt mt-0.5 run_modal_stat_value" data-name="run_modal_stat_value">{fmtSpeed(activity.average_speed)} <span className="text-xs text-txt-secondary run_modal_stat_value_pace_unit_text" data-name="run_modal_stat_value_pace_unit_text">/km</span></div>
            </div>
            <div className="bg-surface-muted rounded-xl p-3 run_modal_stat_card" data-name="run_modal_stat_card">
              <div className="text-[10px] text-txt-muted uppercase run_modal_stat_label" data-name="run_modal_stat_label">FC moy.</div>
              <div className="text-lg font-mono font-semibold text-txt mt-0.5 run_modal_stat_value" data-name="run_modal_stat_value">
                {activity.average_heartrate ? `${Math.round(activity.average_heartrate)}` : '-'}
                {activity.average_heartrate && <span className="text-xs text-txt-secondary run_modal_stat_value_bpm_text" data-name="run_modal_stat_value_bpm_text"> bpm</span>}
              </div>
            </div>
            <div className="bg-surface-muted rounded-xl p-3 run_modal_stat_card" data-name="run_modal_stat_card">
              <div className="text-[10px] text-txt-muted uppercase run_modal_stat_label" data-name="run_modal_stat_label">D+</div>
              <div className="text-lg font-mono font-semibold text-txt mt-0.5 run_modal_stat_value" data-name="run_modal_stat_value">
                {activity.total_elevation_gain ? `${Math.round(activity.total_elevation_gain)}` : '-'}
                {activity.total_elevation_gain > 0 && <span className="text-xs text-txt-secondary run_modal_stat_value_m_text" data-name="run_modal_stat_value_m_text"> m</span>}
              </div>
            </div>
          </div>

          {/* Extra stats row — colonnes DB historiquement non affichées */}
          <div className="flex items-center gap-4 text-xs text-txt-secondary flex-wrap run_modal_extra_stats" data-name="run_modal_extra_stats">
            {buildRunExtraStats(activity, { maxSpeed: speedMax }).map(s => (
              <span key={s.label}>{s.label} {s.value} {s.unit}</span>
            ))}
            {displayedEffort > 0 && (
              <span className="font-medium text-primary run_modal_extra_stats_effort_relatif_relative_effort_label" data-name="run_modal_extra_stats_effort_relatif_relative_effort_label">Effort relatif: {displayedEffort}</span>
            )}
            {activity.pr_count > 0 && (
              <span className="font-medium text-brand run_modal_extra_stats_pr_count_pr_label" data-name="run_modal_extra_stats_pr_count_pr_label">{activity.pr_count} PR</span>
            )}
          </div>

          <RunSplitSummary activity={activity} allActivities={allActivities} />

          {/* Description (saisie Garmin/Strava) */}
          {activity.description && (
            <p className="text-xs text-txt-muted italic whitespace-pre-line run_modal_description" data-name="run_modal_description">{activity.description}</p>
          )}

          {/* Map */}
          {mapRun.length > 0 && (
            <div className="rounded-xl overflow-hidden border border-surface-border run_modal_map" data-name="run_modal_map">
              <RunMap
                runs={mapRun}
                height={200}
                activePoint={activeStreamPoint?.latlng}
                onTraceHover={handleTraceHover}
                onTraceLeave={clearActiveStreamIndex}
              />
            </div>
          )}

          {loading && <Loader />}

          {/* Speed chart */}
          {speedData.length > 0 && (
            <div className="run_modal_speed_section" data-name="run_modal_speed_section">
              <div className="text-xs font-medium text-txt-secondary mb-2 run_modal_section_title" data-name="run_modal_section_title">Allure</div>
              <ResponsiveContainer width="100%" height={140} className="run_modal_speed_chart_container">
                <AreaChart data={speedData} onMouseMove={handleChartMouseMove} onMouseLeave={clearActiveStreamIndex}>
                  <defs>
                    <linearGradient id="gradSpeedModal" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#2563EB" stopOpacity={0.2} />
                      <stop offset="100%" stopColor="#2563EB" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid {...gridStyle} />
                  <XAxis dataKey="distance" type="number" tick={{ ...axisStyle, fontSize: 9 }} unit=" km" domain={['dataMin', 'dataMax']} />
                  <YAxis tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={fmtSpeed} domain={speedDomain} allowDataOverflow />
                  <Tooltip content={({ active, payload }) => {
                    if (!active || !payload?.length) return null
                    const d = payload[0]?.payload
                    return (
                      <div className="bg-white border border-surface-border rounded-lg px-3 py-2 shadow-lg text-xs run_modal_chart_tooltip" data-name="run_modal_chart_tooltip">
                        <div className="text-txt-secondary run_modal_chart_tooltip_to_fixed_km_value" data-name="run_modal_chart_tooltip_to_fixed_km_value">{d?.distance?.toFixed(1)} km</div>
                        <div className="font-mono text-primary run_modal_chart_tooltip_pace_km_value" data-name="run_modal_chart_tooltip_pace_km_value">{d?.pace}/km</div>
                      </div>
                    )
                  }} />
                  {/* isAnimationActive={false} : avec allowDataOverflow, le
                      clipPath d'animation Recharts reste parfois à width=0 et
                      la courbe devient invisible (bug connu). */}
                  <Area type="monotone" dataKey="speed" stroke="#2563EB" strokeWidth={1.5} fill="url(#gradSpeedModal)" dot={false} isAnimationActive={false} />
                  {activeSpeedPoint?.distance != null && activeSpeedPoint?.speed != null && (
                    <>
                      <ReferenceLine x={activeSpeedPoint.distance} stroke="#2563EB" strokeOpacity={0.3} strokeDasharray="3 3" ifOverflow="extendDomain" />
                      <ReferenceDot x={activeSpeedPoint.distance} y={activeSpeedPoint.speed} r={4} fill="#2563EB" stroke="#ffffff" strokeWidth={2} isFront ifOverflow="extendDomain" />
                    </>
                  )}
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* HR chart */}
          {hrData.length > 0 && (
            <div className="run_modal_hr_section" data-name="run_modal_hr_section">
              <div className="text-xs font-medium text-txt-secondary mb-2 run_modal_section_title" data-name="run_modal_section_title">Frequence cardiaque</div>
              <ResponsiveContainer width="100%" height={140}>
                <AreaChart data={hrData} onMouseMove={handleChartMouseMove} onMouseLeave={clearActiveStreamIndex}>
                  <defs>
                    <linearGradient id="gradHRModal" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#EF4444" stopOpacity={0.2} />
                      <stop offset="100%" stopColor="#EF4444" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid {...gridStyle} />
                  <XAxis dataKey="time" type="number" tick={{ ...axisStyle, fontSize: 9 }} unit=" min" domain={['dataMin', 'dataMax']} />
                  <YAxis tick={{ ...axisStyle, fontSize: 9 }} domain={['dataMin - 5', 'dataMax + 5']} />
                  <Tooltip content={({ active, payload }) => {
                    if (!active || !payload?.length) return null
                    const d = payload[0]?.payload
                    return (
                      <div className="bg-white border border-surface-border rounded-lg px-3 py-2 shadow-lg text-xs run_modal_chart_tooltip" data-name="run_modal_chart_tooltip">
                        <div className="text-txt-secondary run_modal_chart_tooltip_time_min_section" data-name="run_modal_chart_tooltip_time_min_section">{formatChartMinutes(d?.time)}</div>
                        <div className="font-mono text-red-500 run_modal_chart_tooltip_hr_bpm_value" data-name="run_modal_chart_tooltip_hr_bpm_value">{d?.hr} bpm</div>
                      </div>
                    )
                  }} />
                  <Area type="monotone" dataKey="hr" stroke="#EF4444" strokeWidth={1.5} fill="url(#gradHRModal)" dot={false} />
                  {activeHrPoint?.time != null && activeHrPoint?.hr != null && (
                    <>
                      <ReferenceLine x={activeHrPoint.time} stroke="#EF4444" strokeOpacity={0.3} strokeDasharray="3 3" ifOverflow="extendDomain" />
                      <ReferenceDot x={activeHrPoint.time} y={activeHrPoint.hr} r={4} fill="#EF4444" stroke="#ffffff" strokeWidth={2} isFront ifOverflow="extendDomain" />
                    </>
                  )}
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* HR Zones */}
          {timeInZones.length > 0 && (
            <div className="run_modal_zones_section" data-name="run_modal_zones_section">
              <div className="text-xs font-medium text-txt-secondary mb-2 run_modal_section_title" data-name="run_modal_section_title">Zones FC</div>
              <div className="flex rounded-lg overflow-hidden h-5 mb-1 run_modal_zones_bar" data-name="run_modal_zones_bar">
                {timeInZones.filter(z => z.seconds > 0).map(z => (
                  <div
                    key={z.zone}
                    className="h-full flex items-center justify-center text-[8px] font-medium text-white run_modal_zones_bar_segment"
                    data-name="run_modal_zones_bar_segment"
                    style={{ backgroundColor: z.color, width: `${z.pct}%` }}
                    title={`${z.label}: ${fmtTime(z.seconds)} (${z.pct}%)`}
                  >
                    {z.pct >= 10 && `Z${z.zone}`}
                  </div>
                ))}
              </div>
              <div className="flex items-center gap-2.5 text-[10px] text-txt-muted flex-wrap run_modal_zones_legend" data-name="run_modal_zones_legend">
                {timeInZones.filter(z => z.seconds > 0).map(z => (
                  <span key={z.zone} className="flex items-center gap-0.5 run_modal_zones_legend_item" data-name="run_modal_zones_legend_item">
                    <span className="w-1.5 h-1.5 rounded-full run_modal_zones_legend_swatch" data-name="run_modal_zones_legend_swatch" style={{ backgroundColor: z.color }} />
                    {z.label} {z.pct}%
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Best Efforts */}
          {bestEfforts.distances.length > 0 && (
            <div className="run_modal_best_efforts_section" data-name="run_modal_best_efforts_section">
              <div className="text-xs font-medium text-txt-secondary mb-2 run_modal_section_title" data-name="run_modal_section_title">Meilleurs efforts</div>
              <div className="grid grid-cols-3 sm:grid-cols-5 gap-2 run_modal_best_efforts_grid" data-name="run_modal_best_efforts_grid">
                {bestEfforts.distances.map(d => (
                  <div key={d.key} className="bg-surface-muted rounded-lg p-2 text-center run_modal_best_effort_card" data-name="run_modal_best_effort_card">
                    <div className="text-[10px] text-txt-muted uppercase run_modal_best_effort_label" data-name="run_modal_best_effort_label">{d.label}</div>
                    <div className="text-sm font-mono font-semibold text-txt mt-0.5 run_modal_best_effort_value" data-name="run_modal_best_effort_value">{d.formatted}</div>
                    <div className="text-[10px] text-txt-muted run_modal_best_effort_pace" data-name="run_modal_best_effort_pace">{d.pace}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
