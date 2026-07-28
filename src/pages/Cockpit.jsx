import React, { useMemo, useState, useCallback, useEffect, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Pencil, Check, X, RotateCcw, HeartPulse } from 'lucide-react'
import posthog from 'posthog-js'
import { useActivities } from '../contexts/ActivityContext'
import { computeCockpit, parseLocalDate, fmtPace, localDateStr } from '../lib/compute'
import { loadDailyTraining, peekDailyTraining, getActivityStreams, recentTrainingRunsFromActivities } from '../api'
import { computeLoadDistribution } from '../lib/training'
import { getCurrentMaxHr, getManualFcMax, setManualFcMax, getMaxHrForDate, getMaxHrWithDate } from '../lib/heartRateZones'
import { useNow } from '../lib/clock'
import { isLikelyEncodedPolyline, toMapRun } from '../lib/runMaps'
import StatCard from '../components/StatCard'
import AlertBanner from '../components/AlertBanner'
import Loader from '../components/Loader'
import RunMap from '../components/RunMap'
import PaceDistanceScatter from '../components/PaceDistanceScatter'
import GarminWorkoutButton from '../components/GarminWorkoutButton'

const RECENT_RUNS_LIMIT = 5
const MAP_WINDOW_DAYS = 30

function fmtHrRange(pctMin, pctMax, maxHr) {
  return `${Math.round(pctMin * maxHr)}-${Math.round(pctMax * maxHr)}`
}

function HrTargets({ hr, maxHr }) {
  if (!hr?.length || !maxHr) return null
  return (
    <div className="mt-2.5 pt-2.5 border-t border-surface-border cockpit_daily_training_hr" data-name="cockpit_daily_training_hr">
      <div className="flex items-center gap-1.5 text-[11px] sm:text-xs font-medium text-txt-secondary mb-1.5 cockpit_daily_training_hr_title" data-name="cockpit_daily_training_hr_title">
        <HeartPulse size={12} className="text-rose-500" /> FC cible <span className="font-normal text-txt-muted">· à piloter</span>
      </div>
      <div className="space-y-1 cockpit_daily_training_hr_list" data-name="cockpit_daily_training_hr_list">
        {hr.map(h => (
          <div key={h.label} className="cockpit_daily_training_hr_item" data-name="cockpit_daily_training_hr_item">
            <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
              <span className="text-[11px] sm:text-xs text-txt-secondary cockpit_daily_training_hr_item_label" data-name="cockpit_daily_training_hr_item_label">{h.label}</span>
              <span className="text-xs sm:text-sm font-mono font-semibold text-txt whitespace-nowrap cockpit_daily_training_hr_item_value" data-name="cockpit_daily_training_hr_item_value">
                {fmtHrRange(h.pctMin, h.pctMax, maxHr)}
                <span className="text-[10px] font-sans font-normal text-txt-muted ml-1">bpm · {Math.round(h.pctMin * 100)}-{Math.round(h.pctMax * 100)}%</span>
              </span>
            </div>
            {h.note && (
              <div className="text-[10px] text-txt-muted mt-0.5 cockpit_daily_training_hr_item_note" data-name="cockpit_daily_training_hr_item_note">{h.note}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function TrainingStep({ label, value }) {
  if (!value) return null
  return (
    <div className="cockpit_daily_training_day_step" data-name="cockpit_daily_training_day_step">
      <div className="text-[11px] font-medium text-txt cockpit_daily_training_step_label" data-name="cockpit_daily_training_step_label">{label}</div>
      <div className="text-txt-secondary cockpit_daily_training_step_text" data-name="cockpit_daily_training_step_text">{value}</div>
    </div>
  )
}

function TrainingDayPanel({ guidance, primary = false, maxHr }) {
  const session = guidance.session || {}
  const hasStructuredSession = session.warmup || session.cooldown

  return (
    <div
      className={`rounded-lg border px-3 py-3 sm:px-3.5 sm:py-3.5 cockpit_daily_training_day ${primary ? 'border-primary/30 bg-primary/5' : 'border-surface-border bg-surface-muted/40 dark:bg-slate-500/10'}`}
      data-name={`cockpit_daily_training_day_${guidance.relativeLabel || guidance.date}`}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-primary">{guidance.relativeLabel}</div>
          <div className="text-sm sm:text-[15px] leading-snug font-semibold text-txt cockpit_daily_training_day_title">{guidance.title}</div>
          <div className="text-[11px] text-txt-muted mt-0.5">{guidance.dateLabel}</div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <span className="text-[10px] font-medium rounded-full px-1.5 py-0.5 bg-surface text-txt-secondary whitespace-nowrap">
            {guidance.statusLabel}
          </span>
          {guidance.workoutEligible && (
            <GarminWorkoutButton
              date={guidance.date}
              workout={workoutPayloadFromGuidance(guidance)}
              dataName="cockpit_daily_training_garmin"
            />
          )}
        </div>
      </div>
      <div className="space-y-1.5 text-[13px] sm:text-sm leading-snug cockpit_daily_training_day_steps" data-name="cockpit_daily_training_day_steps">
        <TrainingStep label="Échauffement" value={session.warmup} />
        <TrainingStep label={hasStructuredSession ? 'Courir' : 'Séance'} value={session.main || guidance.title} />
        <TrainingStep label="Retour au calme" value={session.cooldown} />
      </div>
      <HrTargets hr={guidance.hr} maxHr={maxHr} />
    </div>
  )
}

function titleWithoutEffort(title) {
  return String(title || 'Séance').split(' · ')[0]
}

function workoutPayloadFromGuidance(guidance) {
  if (!guidance?.workoutEligible) return null
  return {
    title: titleWithoutEffort(guidance.title),
    category: guidance.category,
    tag: guidance.tag,
    structure: guidance.session,
    estimatedKm: guidance.estimatedKm,
    estimatedMinutes: guidance.estimatedMinutes,
  }
}

function futureEffortLabel(guidance) {
  if (guidance.estimatedKm) return `~${Math.round(guidance.estimatedKm)} km`
  if (guidance.estimatedDuration) return guidance.estimatedDuration
  return guidance.statusLabel || ''
}

function nextGarminLabel(guidance) {
  const relative = guidance?.relativeLabel === "Aujourd'hui" ? "aujourd'hui" : guidance?.relativeLabel
  return relative ? `Envoyer Garmin ${relative}` : 'Envoyer Garmin'
}

function FutureTrainingPanel({ sessions }) {
  if (!sessions.length) return null

  return (
    <div
      className="rounded-lg border border-surface-border bg-surface-muted/40 dark:bg-slate-500/10 px-3 py-3 sm:px-3.5 sm:py-3.5 cockpit_daily_training_day cockpit_daily_training_future"
      data-name="cockpit_daily_training_day_J+3_J+7"
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="min-w-0">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-primary">J+3 à J+7</div>
          <div className="text-sm sm:text-[15px] leading-snug font-semibold text-txt">Suite de semaine</div>
          <div className="text-[11px] text-txt-muted mt-0.5">{sessions.length} jours</div>
        </div>
        <span className="text-[10px] font-medium rounded-full px-1.5 py-0.5 bg-surface text-txt-secondary whitespace-nowrap">
          Aperçu
        </span>
      </div>
      <div className="space-y-1.5 cockpit_daily_training_future_list" data-name="cockpit_daily_training_future_list">
        {sessions.map(guidance => (
          <div
            key={guidance.date}
            className="grid grid-cols-[2.25rem_1fr_auto] items-center gap-2 rounded-md bg-surface/80 px-2 py-1.5 cockpit_daily_training_future_row"
            data-name={`cockpit_daily_training_future_day_${guidance.relativeLabel || guidance.date}`}
          >
            <div className="text-[11px] font-semibold text-primary cockpit_daily_training_future_label">{guidance.relativeLabel}</div>
            <div className="min-w-0">
              <div className="text-xs font-medium text-txt truncate cockpit_daily_training_future_title">{titleWithoutEffort(guidance.title)}</div>
              <div className="text-[10px] text-txt-muted truncate cockpit_daily_training_future_date">{guidance.dateLabel}</div>
            </div>
            <div className="flex items-center justify-end gap-1.5">
              {guidance.workoutEligible && (
                <GarminWorkoutButton
                  date={guidance.date}
                  workout={workoutPayloadFromGuidance(guidance)}
                  compact
                  dataName="cockpit_daily_training_future_garmin"
                />
              )}
              <div className="text-[10px] font-mono text-txt-secondary whitespace-nowrap cockpit_daily_training_future_effort">
                {futureEffortLabel(guidance)}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function LoadDistributionCard({ loadDist, loading }) {
  if (!loadDist && !loading) return null
  if (!loadDist) {
    return (
      <div className="card cockpit_load_distribution_card animate-pulse mt-4 sm:mt-6" data-name="cockpit_load_distribution_card">
        <h3 className="card_section_title_compact cockpit_load_distribution_title" data-name="cockpit_load_distribution_title">Répartition de charge (28j)</h3>
        <div className="h-6 bg-surface-muted rounded-lg mb-3 cockpit_load_distribution_skeleton_bar" data-name="cockpit_load_distribution_skeleton_bar" />
        <div className="h-4 w-40 bg-surface-muted rounded cockpit_load_distribution_skeleton_row" data-name="cockpit_load_distribution_skeleton_row" />
      </div>
    )
  }
  const zones = [
    { key: 'lowAerobic', label: 'Aérobie faible', color: 'bg-blue-400' },
    { key: 'aerobic', label: 'Aérobie', color: 'bg-emerald-500' },
    { key: 'anaerobic', label: 'Anaérobie', color: 'bg-red-500' },
  ]
  return (
    <div className="card cockpit_load_distribution_card mt-4 sm:mt-6" data-name="cockpit_load_distribution_card">
      <h3 className="card_section_title_compact cockpit_load_distribution_title" data-name="cockpit_load_distribution_title">Répartition de charge ({loadDist.period})</h3>
      <div className="flex rounded-lg overflow-hidden h-6 mb-3 cockpit_load_distribution_bar" data-name="cockpit_load_distribution_bar">
        {zones.map(zone => {
          const pct = loadDist[zone.key].pct
          if (pct <= 0) return null
          return (
            <div
              key={zone.key}
              className={`h-full flex items-center justify-center text-[10px] font-medium text-white ${zone.color} cockpit_load_distribution_segment`}
              style={{ width: `${pct}%`, minWidth: pct > 0 ? '8px' : undefined }}
            >
              {pct >= 10 && `${pct}%`}
            </div>
          )
        })}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 cockpit_load_distribution_legend" data-name="cockpit_load_distribution_legend">
        {zones.map(zone => (
          <div key={zone.key} className="flex items-center justify-between text-xs rounded-lg bg-surface-muted/50 dark:bg-slate-500/10 px-3 py-2 cockpit_load_distribution_legend_row">
            <span className="flex items-center gap-1.5">
              <span className={`w-2.5 h-2.5 rounded-sm ${zone.color}`} />
              {zone.label}
            </span>
            <span className="font-mono text-txt-secondary">{loadDist[zone.key].pct}% ({loadDist[zone.key].minutes} min)</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function Cockpit() {
  const { activities, allActivities, loading, loadingMore, computedPRs } = useActivities()
  const now = useNow()
  const [searchParams, setSearchParams] = useSearchParams()
  const [editingFcMax, setEditingFcMax] = useState(false)
  const [fcMaxInput, setFcMaxInput] = useState('')
  const [fcMaxVersion, setFcMaxVersion] = useState(0)
  const [dailyTraining, setDailyTraining] = useState(null)
  const [dailyTrainingLoading, setDailyTrainingLoading] = useState(false)
  const [mapWindowStreams, setMapWindowStreams] = useState({})
  const mountedRef = useRef(false)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const trainingDay = localDateStr(new Date(now))
  const recentTrainingRuns = useMemo(
    () => recentTrainingRunsFromActivities(allActivities, trainingDay),
    [allActivities, trainingDay]
  )
  const recentTrainingSignature = useMemo(
    () => recentTrainingRuns
      .map(run => `${run.id}:${run.start_date_local || run.date}:${run.distance_m}:${run.moving_time}`)
      .join('|'),
    [recentTrainingRuns]
  )

  useEffect(() => {
    let cancelled = false
    // Retour sur Cockpit avec les memes entrees : on ressort la seance deja
    // calculee (cache) sans refetch ni spinner -> l'entrainement n'est pas recree.
    const cached = peekDailyTraining(trainingDay, recentTrainingRuns)
    if (cached) {
      setDailyTraining(cached)
      setDailyTrainingLoading(false)
      return () => { cancelled = true }
    }
    setDailyTrainingLoading(true)
    loadDailyTraining(trainingDay, recentTrainingRuns)
      .then(data => { if (!cancelled) setDailyTraining(data) })
      .finally(() => { if (!cancelled) setDailyTrainingLoading(false) })
    return () => { cancelled = true }
  }, [trainingDay, recentTrainingSignature])

  const data = useMemo(() => {
    if (!activities.length) return null
    return computeCockpit(activities, computedPRs)
  }, [activities, computedPRs, now])

  // FC max must be computed first — load distribution depends on it.
  const currentFcMax = useMemo(() => {
    void fcMaxVersion
    return getCurrentMaxHr(activities)
  }, [activities, fcMaxVersion, now])

  const autoFcMax = useMemo(() => getMaxHrForDate(activities, new Date(now), 90), [activities, now])
  const autoFcMaxInfo = useMemo(() => getMaxHrWithDate(activities, new Date(now), 90), [activities, now])
  const isOverridden = getManualFcMax() > 0

  // Use the same FC max as the card to ensure consistent zone boundaries.
  const loadDist = useMemo(() => {
    if (loadingMore) return null
    return computeLoadDistribution(allActivities, { fcMax: currentFcMax })
  }, [allActivities, loadingMore, currentFcMax, now])

  const startEdit = useCallback(() => {
    setFcMaxInput(String(currentFcMax))
    setEditingFcMax(true)
  }, [currentFcMax])

  const saveFcMax = useCallback(() => {
    const val = parseInt(fcMaxInput, 10)
    if (val >= 100 && val <= 230) {
      setManualFcMax(val)
      setFcMaxVersion(v => v + 1)
      posthog.capture('hr_max_updated', { value: val, is_reset: false })
    }
    setEditingFcMax(false)
  }, [fcMaxInput])

  const resetFcMax = useCallback(() => {
    setManualFcMax(0)
    setFcMaxVersion(v => v + 1)
    setEditingFcMax(false)
    posthog.capture('hr_max_updated', { is_reset: true })
  }, [])

  const handleRunClick = useCallback(id => {
    console.log('[Cockpit] opening run from map:', id)
    setSearchParams({ run: id })
  }, [setSearchParams])

  const recentRuns = useMemo(() => activities.slice(0, RECENT_RUNS_LIMIT), [activities])
  const recentRunIds = useMemo(() => recentRuns.map(activity => activity.id), [recentRuns])
  const mapWindowRuns = useMemo(() => {
    const since = now - MAP_WINDOW_DAYS * 86400000
    return activities.filter(activity => parseLocalDate(activity.start_date_local).getTime() >= since)
  }, [activities, now])
  const mapWindowSignature = useMemo(
    () => mapWindowRuns
      .map(activity => [
        activity.id,
        activity.start_date_local || '',
        isLikelyEncodedPolyline(activity.summary_polyline) ? 'polyline' : 'streams',
      ].join(':'))
      .join('|'),
    [mapWindowRuns]
  )

  useEffect(() => {
    if (!mapWindowRuns.length) return

    const missing = mapWindowRuns.filter(activity => {
      const id = String(activity.id)
      return !isLikelyEncodedPolyline(activity.summary_polyline) &&
        !Object.prototype.hasOwnProperty.call(mapWindowStreams, id)
    })
    if (!missing.length) return

    setMapWindowStreams(prev => {
      const next = { ...prev }
      for (const activity of missing) {
        const id = String(activity.id)
        if (!Object.prototype.hasOwnProperty.call(next, id)) {
          next[id] = { loading: true, points: null }
        }
      }
      return next
    })

    Promise.all(missing.map(async activity => {
      const id = String(activity.id)
      try {
        const data = await getActivityStreams(activity.id)
        const points = data?.streams?.latlng?.data
        return [id, { loading: false, points: Array.isArray(points) ? points : null }]
      } catch (e) {
        console.warn('[Cockpit] recent map streams unavailable for', activity.id, e?.message || e)
        return [id, { loading: false, points: null }]
      }
    })).then(entries => {
      if (!mountedRef.current) return
      setMapWindowStreams(prev => {
        const next = { ...prev }
        for (const [id, value] of entries) next[id] = value
        return next
      })
    })
  }, [mapWindowSignature])

  // Map runs: show the last 30 days. Garmin activities usually
  // have no encoded summary polyline, so their real GPS stream is loaded above.
  const mapRuns = useMemo(() => {
    console.log('[Cockpit] preparing 30-day map runs from', mapWindowRuns.length, 'activities')
    return mapWindowRuns
      .map(activity => {
        const cached = mapWindowStreams[String(activity.id)]
        return toMapRun(activity, [], cached?.points || null, { allowFallback: false })
      })
      .filter(Boolean)
  }, [mapWindowRuns, mapWindowStreams])

  if (loading && !activities.length) return <Loader />
  if (!data && !activities.length) return <div className="text-txt-muted cockpit_empty_state" data-name="cockpit_empty_state">Aucune donnee disponible.</div>

  const dailyTrainingSessions = dailyTraining?.sessions || (dailyTraining ? [dailyTraining] : [])
  const dailyTrainingPrimarySessions = dailyTrainingSessions.slice(0, 3)
  const dailyTrainingFutureSessions = dailyTrainingSessions.slice(3, 8)
  const dailyTrainingDataThrough = dailyTraining?.dataThrough
    ? parseLocalDate(dailyTraining.dataThrough).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
    : null
  const dailyTrainingPlanLabel = dailyTraining?.planDescription
    || (dailyTraining?.planSource ? `Plan ${dailyTraining.planSource}` : 'Plan coach')
  const nextGarminSession = dailyTrainingSessions.find(guidance => guidance.workoutEligible)

  return (
    <div data-name="page_cockpit">
      <h2 className="page_heading mb-4 sm:mb-6 cockpit_header_title" data-name="cockpit_header_title">Cockpit</h2>
      {data && <AlertBanner alerts={data.alerts} />}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3 sm:gap-4 mb-4 sm:mb-6 cockpit_hero_stats" data-name="cockpit_hero_stats">
          <StatCard label="Semaine en cours" value={data.week_volume} unit="km" />
          <StatCard label="7 jours" value={data.volume_7d} unit="km" />
          <StatCard label="90 jours" value={data.volume_90d} unit="km" />
          <StatCard label="365 jours" value={data.volume_365d} unit="km" />
          <StatCard label="Moy. 4 sem." value={data.avg_4_weeks} unit="km/sem" />
          <StatCard label="Total runs" value={data.total_activities} />

          {/* FC Max card with inline edit */}
          <div className="card px-3 py-3 relative cockpit_fcmax_card" data-name="cockpit_fcmax_card">
            <div className="metric_label_caps mb-1 cockpit_fcmax_label" data-name="cockpit_fcmax_label">FC max</div>
            {editingFcMax ? (
              <div className="cockpit_fcmax_editor" data-name="cockpit_fcmax_editor">
                <div className="flex items-center gap-1 cockpit_fcmax_editor_row" data-name="cockpit_fcmax_editor_row">
                  <input
                    type="number"
                    value={fcMaxInput}
                    onChange={e => setFcMaxInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') saveFcMax(); if (e.key === 'Escape') setEditingFcMax(false) }}
                    className="w-16 text-lg font-mono font-semibold bg-surface-muted rounded px-1.5 py-0.5 border border-surface-border focus:outline-none focus:border-primary text-txt cockpit_fcmax_input"
                    data-name="cockpit_fcmax_input"
                    min={100} max={230} autoFocus
                  />
                  <button onClick={saveFcMax} className="p-1 rounded hover:bg-emerald-50 text-emerald-600 cockpit_fcmax_save_button" data-name="cockpit_fcmax_save_button"><Check size={14} /></button>
                  <button onClick={() => setEditingFcMax(false)} className="p-1 rounded hover:bg-red-50 text-red-400 cockpit_fcmax_cancel_button" data-name="cockpit_fcmax_cancel_button"><X size={14} /></button>
                </div>
                {isOverridden && (
                  <button onClick={resetFcMax} className="flex items-center gap-1 mt-1 text-[10px] text-txt-muted hover:text-primary cockpit_fcmax_reset_button" data-name="cockpit_fcmax_reset_button">
                    <RotateCcw size={10} /> Auto ({autoFcMax})
                  </button>
                )}
              </div>
            ) : (
              <div className="flex items-baseline gap-1.5 cockpit_fcmax_value_row" data-name="cockpit_fcmax_value_row">
                <div className="text-xl font-mono font-semibold text-txt cockpit_fcmax_value" data-name="cockpit_fcmax_value">{currentFcMax}</div>
                <span className="text-xs text-txt-secondary cockpit_fcmax_unit" data-name="cockpit_fcmax_unit">bpm</span>
                <button onClick={startEdit} className="ml-auto p-1 rounded hover:bg-surface-hover text-txt-muted hover:text-primary transition-colors cockpit_fcmax_edit_button" data-name="cockpit_fcmax_edit_button">
                  <Pencil size={12} />
                </button>
              </div>
            )}
            {!editingFcMax && isOverridden && (
              <div className="metric_note_tiny_warning cockpit_fcmax_manual_note" data-name="cockpit_fcmax_manual_note">Manuel</div>
            )}
            {!editingFcMax && !isOverridden && (
              <div className="metric_note_tiny cockpit_fcmax_auto_note" data-name="cockpit_fcmax_auto_note">
                90j auto{autoFcMaxInfo.date ? ` · ${parseLocalDate(autoFcMaxInfo.date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })}` : ''}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Daily Training — carte pleine largeur */}
      <div className="card mb-4 sm:mb-6 cockpit_daily_training_card" data-name="cockpit_daily_training_card">
        <div className="flex items-center justify-between mb-3 cockpit_daily_training_header" data-name="cockpit_daily_training_header">
          <h3 className="text-sm font-medium text-txt-secondary cockpit_daily_training_title" data-name="cockpit_daily_training_title">Entraînement du jour</h3>
          <div className="flex items-center gap-3 cockpit_daily_training_header_right" data-name="cockpit_daily_training_header_right">
            {nextGarminSession && (
              <GarminWorkoutButton
                date={nextGarminSession.date}
                workout={workoutPayloadFromGuidance(nextGarminSession)}
                label={nextGarminLabel(nextGarminSession)}
                prominent
                dataName="cockpit_daily_training_header_garmin"
              />
            )}
            <Link to="/plan" className="card_link_text cockpit_daily_training_plan_link" data-name="cockpit_daily_training_plan_link">
              Plan détaillé →
            </Link>
            <span className="text-xs text-txt-muted cockpit_daily_training_date" data-name="cockpit_daily_training_date">J à J+7</span>
          </div>
        </div>

        {dailyTrainingLoading ? (
          <div className="space-y-2 cockpit_daily_training_loading" data-name="cockpit_daily_training_loading">
            <div className="h-5 w-20 rounded bg-surface-muted cockpit_daily_training_loading_badge" data-name="cockpit_daily_training_loading_badge" />
            <div className="h-4 w-full rounded bg-surface-muted cockpit_daily_training_loading_line_1" data-name="cockpit_daily_training_loading_line_1" />
            <div className="h-4 w-5/6 rounded bg-surface-muted cockpit_daily_training_loading_line_2" data-name="cockpit_daily_training_loading_line_2" />
            <div className="h-4 w-4/6 rounded bg-surface-muted cockpit_daily_training_loading_line_3" data-name="cockpit_daily_training_loading_line_3" />
          </div>
        ) : dailyTraining ? (
          <div className="cockpit_daily_training_body" data-name="cockpit_daily_training_body">
            {dailyTraining.sleep?.sleep_score != null && (
              <div className="muted_caption_text mb-2 cockpit_daily_training_sleep" data-name="cockpit_daily_training_sleep">
                Sommeil: {dailyTraining.sleep.sleep_score}/100{dailyTraining.sleep.sleep_quality ? ` · ${String(dailyTraining.sleep.sleep_quality).toLowerCase()}` : ''}
              </div>
            )}
            <p className="text-base text-txt mb-2 cockpit_daily_training_observations" data-name="cockpit_daily_training_observations">{dailyTraining.observations}</p>
            <p className="text-sm text-txt-secondary mb-4 cockpit_daily_training_adjustment" data-name="cockpit_daily_training_adjustment">{dailyTraining.adjustment}</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5 sm:gap-3 cockpit_daily_training_sessions" data-name="cockpit_daily_training_sessions">
              {dailyTrainingPrimarySessions.map((guidance, index) => (
                <TrainingDayPanel key={guidance.date} guidance={guidance} primary={index === 0} maxHr={currentFcMax} />
              ))}
              <FutureTrainingPanel sessions={dailyTrainingFutureSessions} />
            </div>
            <div className="mt-3 text-[11px] text-txt-muted cockpit_daily_training_source" data-name="cockpit_daily_training_source">
              {dailyTrainingPlanLabel}
              {dailyTraining.planBasis ? ` · ${dailyTraining.planBasis}` : ''}
              {dailyTrainingDataThrough ? ` · derniers runs au ${dailyTrainingDataThrough}` : ''}
            </div>
          </div>
        ) : (
          <div className="text-sm text-txt-muted cockpit_daily_training_empty" data-name="cockpit_daily_training_empty">
            Seance du jour indisponible pour le moment.
          </div>
        )}

      </div>

      {/* Recent Activities + Map side by side */}
      {recentRuns.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-4 mb-4 sm:mb-6 cockpit_section_recent" data-name="cockpit_section_recent">
          {/* Recent runs list */}
          <div className="card cockpit_recent_runs_card" data-name="cockpit_recent_runs_card">
            <div className="card_header_row_spaced cockpit_recent_runs_header" data-name="cockpit_recent_runs_header">
              <h3 className="card_section_title cockpit_recent_runs_title" data-name="cockpit_recent_runs_title">Dernieres sorties</h3>
              <Link to="/runs" className="card_link_text cockpit_recent_runs_link" data-name="cockpit_recent_runs_link">Voir tout →</Link>
            </div>
            <div className="space-y-0.5 cockpit_recent_runs_list" data-name="cockpit_recent_runs_list">
              {recentRuns.map(a => (
                <div key={a.id} onClick={() => setSearchParams({ run: a.id })}
                  className="flex items-center justify-between py-2 sm:py-2.5 px-2 sm:px-3 rounded-lg hover:bg-surface-hover transition-colors cursor-pointer cockpit_recent_runs_row"
                  data-name={`cockpit_recent_runs_row_${a.id}`}>
                  <div className="min-w-0 flex-1 cockpit_recent_runs_details" data-name={`cockpit_recent_runs_details_${a.id}`}>
                    <div className="text-sm font-medium text-txt truncate cockpit_recent_runs_name" data-name={`cockpit_recent_runs_name_${a.id}`}>{a.name}</div>
                    <div className="text-xs text-txt-muted cockpit_recent_runs_date" data-name={`cockpit_recent_runs_date_${a.id}`}>{parseLocalDate(a.start_date_local).toLocaleDateString('fr-FR')}</div>
                  </div>
                  <div className="text-right ml-3 flex-shrink-0 cockpit_recent_runs_metrics" data-name={`cockpit_recent_runs_metrics_${a.id}`}>
                    <div className="text-sm font-mono text-txt cockpit_recent_runs_distance" data-name={`cockpit_recent_runs_distance_${a.id}`}>{(a.distance / 1000).toFixed(1)} km</div>
                    <div className="text-xs text-txt-muted font-mono cockpit_recent_runs_pace" data-name={`cockpit_recent_runs_pace_${a.id}`}>
                      {fmtPace(1000 / a.average_speed)}/km
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Map of recent runs */}
          <div className="card p-0 overflow-hidden cockpit_recent_map_card" data-name="cockpit_recent_map_card">
            <RunMap
              runs={mapRuns}
              height="100%"
              className="h-full min-h-[280px]"
              onRunClick={handleRunClick}
              fitRunIds={recentRunIds}
              highlightRunIds={recentRunIds}
              flush
            />
          </div>
        </div>
      )}

      {/* Allure vs Distance scatter */}
      <PaceDistanceScatter />

      <LoadDistributionCard loadDist={loadDist} loading={loadingMore} />
    </div>
  )
}
