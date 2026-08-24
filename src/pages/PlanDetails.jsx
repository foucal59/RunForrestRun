import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarDays, ChevronDown, Flag, HeartPulse, Timer } from 'lucide-react'
import { loadPlanOverview } from '../api'
import { useActivities } from '../contexts/ActivityContext'
import { getCurrentMaxHr } from '../lib/heartRateZones'
import Loader from '../components/Loader'
import StatCard from '../components/StatCard'
import GarminWorkoutButton from '../components/GarminWorkoutButton'

const CATEGORY_STYLES = {
  easy: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400',
  quality: 'bg-orange-100 text-orange-700 dark:bg-orange-500/15 dark:text-orange-400',
  long: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-500/15 dark:text-indigo-400',
  rest: 'bg-slate-100 text-slate-500 dark:bg-slate-500/15 dark:text-slate-400',
  race: 'bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400',
}

function fmtHrRange(pctMin, pctMax, maxHr) {
  return `${Math.round(pctMin * maxHr)}-${Math.round(pctMax * maxHr)} bpm`
}

function fmtHrPct(pctMin, pctMax) {
  return `${Math.round(pctMin * 100)}-${Math.round(pctMax * 100)}% FCmax`
}

function CategoryBadge({ category, label }) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium whitespace-nowrap ${CATEGORY_STYLES[category] || CATEGORY_STYLES.rest} plan_session_badge`}
      data-name="plan_session_badge"
    >
      {label}
    </span>
  )
}

function PaceChips({ paces }) {
  if (!paces?.length) return null
  return (
    <div className="plan_session_paces" data-name="plan_session_paces">
      <div className="flex items-center gap-1.5 text-xs font-medium text-txt-secondary mb-1.5 plan_session_paces_title" data-name="plan_session_paces_title">
        <Timer size={13} /> Allures
      </div>
      <div className="flex flex-wrap gap-1.5 plan_session_paces_list" data-name="plan_session_paces_list">
        {paces.map(p => (
          <div key={p.label} className="px-2.5 py-1.5 rounded-lg bg-surface-muted plan_session_pace_chip" data-name="plan_session_pace_chip">
            <div className="text-[11px] text-txt-muted plan_session_pace_chip_label" data-name="plan_session_pace_chip_label">{p.label}</div>
            <div className="text-sm font-mono font-semibold text-txt plan_session_pace_chip_value" data-name="plan_session_pace_chip_value">{p.value}</div>
            {p.note && <div className="text-[10px] text-txt-muted plan_session_pace_chip_note" data-name="plan_session_pace_chip_note">{p.note}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

function HrTargets({ hr, maxHr }) {
  if (!hr?.length) return null
  return (
    <div className="plan_session_hr" data-name="plan_session_hr">
      <div className="flex items-center gap-1.5 text-xs font-medium text-txt-secondary mb-1.5 plan_session_hr_title" data-name="plan_session_hr_title">
        <HeartPulse size={13} /> FC cible <span className="font-normal text-txt-muted">(FCmax {maxHr} bpm)</span>
      </div>
      <div className="flex flex-wrap gap-1.5 plan_session_hr_list" data-name="plan_session_hr_list">
        {hr.map(h => (
          <div key={h.label} className="px-2.5 py-1.5 rounded-lg bg-surface-muted plan_session_hr_chip" data-name="plan_session_hr_chip">
            <div className="text-[11px] text-txt-muted plan_session_hr_chip_label" data-name="plan_session_hr_chip_label">{h.label}</div>
            <div className="text-sm font-mono font-semibold text-txt plan_session_hr_chip_value" data-name="plan_session_hr_chip_value">
              {fmtHrRange(h.pctMin, h.pctMax, maxHr)}
              <span className="ml-1.5 text-[10px] font-sans font-normal text-txt-muted">{fmtHrPct(h.pctMin, h.pctMax)}</span>
            </div>
            {h.note && <div className="text-[10px] text-txt-muted max-w-[16rem] plan_session_hr_chip_note" data-name="plan_session_hr_chip_note">{h.note}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

function workoutPayloadFromSession(session) {
  if (!session?.workoutEligible) return null
  return {
    title: session.title,
    category: session.category,
    tag: session.tag,
    structure: session.structure,
    estimatedKm: session.estimatedKm,
    estimatedMinutes: session.estimatedMinutes,
  }
}

function SessionCard({ session, maxHr }) {
  const [open, setOpen] = useState(Boolean(session.isToday))
  const hasDetails = session.kind !== 'rest'
  const meta = [
    session.estimatedKm ? `~${session.estimatedKm} km` : null,
    session.estimatedDuration ? `~${session.estimatedDuration}` : null,
    session.optional ? 'optionnel' : null,
  ].filter(Boolean).join(' · ')

  return (
    <div
      className={`rounded-xl border transition-colors plan_session ${
        session.isToday
          ? 'border-accent ring-1 ring-accent bg-accent/5'
          : 'border-surface-border bg-surface-card'
      } ${session.isPast ? 'opacity-55' : ''}`}
      data-name={`plan_session_${session.date}`}
    >
      <button
        type="button"
        onClick={() => hasDetails && setOpen(o => !o)}
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left plan_session_header"
        data-name="plan_session_header"
      >
        <div className="w-20 flex-shrink-0 plan_session_day" data-name="plan_session_day">
          <div className={`text-xs font-medium ${session.isToday ? 'text-brand' : 'text-txt-secondary'} plan_session_day_label`} data-name="plan_session_day_label">
            {session.dayLabel}
          </div>
          {session.isToday && (
            <div className="text-[10px] font-semibold text-brand plan_session_day_today" data-name="plan_session_day_today">Aujourd'hui</div>
          )}
        </div>
        <div className="flex-1 min-w-0 plan_session_summary" data-name="plan_session_summary">
          <div className="flex items-center gap-2 flex-wrap plan_session_title_row" data-name="plan_session_title_row">
            <span className={`text-sm ${session.keySession ? 'font-semibold' : 'font-medium'} text-txt plan_session_title`} data-name="plan_session_title">
              {session.title}
            </span>
            <CategoryBadge category={session.category} label={session.categoryLabel} />
            {session.coachOverride && (
              <span
                className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-brand/15 text-brand plan_session_coach_badge"
                data-name="plan_session_coach_badge"
              >
                Coach
              </span>
            )}
            {session.adjusted && !session.coachOverride && (
              <span
                className="text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-accent/15 text-accent plan_session_adjusted_badge"
                data-name="plan_session_adjusted_badge"
              >
                Ajusté
              </span>
            )}
          </div>
          {meta && <div className="text-[11px] text-txt-muted font-mono plan_session_meta" data-name="plan_session_meta">{meta}</div>}
        </div>
        {hasDetails && (
          <ChevronDown
            size={16}
            className={`flex-shrink-0 text-txt-muted transition-transform ${open ? 'rotate-180' : ''} plan_session_chevron`}
            data-name="plan_session_chevron"
          />
        )}
      </button>

      {(session.adjustment || session.coachNote) && (
        <div
          className="px-3 pb-2 -mt-1 text-[11px] leading-snug text-txt-secondary plan_session_adjustment"
          data-name="plan_session_adjustment"
        >
          {session.plannedTitle && (
            <span className="text-txt-muted">Plan initial : {session.plannedTitle} — </span>
          )}
          {session.adjustment}
          {session.coachNote && (
            <span className="text-txt-muted">{session.adjustment ? ' ' : ''}{session.coachNote}</span>
          )}
        </div>
      )}

      {session.workoutEligible && (
        <div className="px-3 pb-2 -mt-1 flex justify-end plan_session_workouts" data-name="plan_session_workouts">
          <GarminWorkoutButton
            date={session.date}
            workout={workoutPayloadFromSession(session)}
            compact
            dataName="plan_session_garmin"
          />
        </div>
      )}

      {open && hasDetails && (
        <div className="px-3 pb-3 space-y-3 plan_session_details" data-name="plan_session_details">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs plan_session_structure" data-name="plan_session_structure">
            <div className="px-2.5 py-2 rounded-lg bg-surface-muted plan_session_structure_warmup" data-name="plan_session_structure_warmup">
              <div className="text-[10px] uppercase tracking-wide text-txt-muted mb-0.5">Échauffement</div>
              <div className="text-txt">{session.structure.warmup}</div>
            </div>
            <div className="px-2.5 py-2 rounded-lg bg-surface-muted plan_session_structure_main" data-name="plan_session_structure_main">
              <div className="text-[10px] uppercase tracking-wide text-txt-muted mb-0.5">Corps de séance</div>
              <div className="text-txt font-medium">{session.structure.main}</div>
            </div>
            <div className="px-2.5 py-2 rounded-lg bg-surface-muted plan_session_structure_cooldown" data-name="plan_session_structure_cooldown">
              <div className="text-[10px] uppercase tracking-wide text-txt-muted mb-0.5">Retour au calme</div>
              <div className="text-txt">{session.structure.cooldown}</div>
            </div>
          </div>
          <PaceChips paces={session.paces} />
          <HrTargets hr={session.hr} maxHr={maxHr} />
        </div>
      )}
    </div>
  )
}

function WeekCard({ week, maxHr }) {
  const [open, setOpen] = useState(Boolean(week.isCurrent))
  const kmMin = week.estimatedKmMin ?? week.estimatedKm
  const kmMax = week.estimatedKmMax ?? week.estimatedKm
  const volumeLabel = kmMax
    ? ` · ~${kmMin !== kmMax ? `${kmMin}–${kmMax}` : kmMax} km planifiés`
    : ''
  const runDaysLabel = week.plannedRunDaysMax
    ? ` · ${week.plannedRunDaysMin !== week.plannedRunDaysMax
      ? `${week.plannedRunDaysMin}–${week.plannedRunDaysMax}`
      : week.plannedRunDaysMax} sorties`
    : ''
  return (
    <div className={`card p-0 overflow-hidden plan_week ${week.isPast ? 'opacity-70' : ''}`} data-name={`plan_week_${week.start}`}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left plan_week_header"
        data-name="plan_week_header"
      >
        <div className="min-w-0 plan_week_header_text" data-name="plan_week_header_text">
          <div className="flex items-center gap-2 flex-wrap plan_week_title_row" data-name="plan_week_title_row">
            <span className="text-sm font-semibold text-txt plan_week_label" data-name="plan_week_label">{week.label}</span>
            {week.isCurrent && (
              <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-accent text-white plan_week_current_badge" data-name="plan_week_current_badge">
                En cours
              </span>
            )}
          </div>
          <div className="text-xs text-txt-muted plan_week_subtitle" data-name="plan_week_subtitle">
            {week.phaseLabel}{volumeLabel}{runDaysLabel}
          </div>
        </div>
        <ChevronDown
          size={18}
          className={`flex-shrink-0 text-txt-muted transition-transform ${open ? 'rotate-180' : ''} plan_week_chevron`}
          data-name="plan_week_chevron"
        />
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-2 plan_week_sessions" data-name="plan_week_sessions">
          {week.sessions.map(s => (
            <SessionCard key={s.date} session={s} maxHr={maxHr} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function PlanDetails() {
  const { activities, allActivities, loading: activitiesLoading } = useActivities()
  const [plan, setPlan] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    loadPlanOverview()
      .then(data => {
        if (cancelled) return
        console.log('[PlanDetails] plan-overview loaded:', data?.weeks?.length, 'weeks')
        setPlan(data)
      })
      .catch(e => {
        console.error('[PlanDetails] plan-overview failed:', e?.message || e)
        if (!cancelled) setError('Impossible de charger le plan. Réessayez dans quelques secondes.')
      })
    return () => { cancelled = true }
  }, [])

  const maxHr = useMemo(
    () => getCurrentMaxHr(allActivities?.length ? allActivities : activities),
    [allActivities, activities]
  )

  const nextKeySession = useMemo(() => {
    if (!plan) return null
    for (const week of plan.weeks) {
      for (const s of week.sessions) {
        if (!s.isPast && s.keySession) return s
      }
    }
    return null
  }, [plan])

  const currentWeek = useMemo(() => plan?.weeks?.find(w => w.isCurrent) || null, [plan])

  if (error) {
    return (
      <div className="card text-sm text-txt-secondary plan_error" data-name="plan_error">{error}</div>
    )
  }
  if (!plan || activitiesLoading) return <Loader />

  return (
    <div data-name="page_plan">
      <div className="flex items-start justify-between gap-3 mb-1 plan_header_row" data-name="plan_header_row">
        <h2 className="page_heading plan_header" data-name="plan_header">Plan marathon — séances détaillées</h2>
      </div>
      <p className="text-sm text-txt-secondary mb-6 plan_subtitle" data-name="plan_subtitle">
        {plan.planDescription} · {plan.planBasis}
      </p>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mb-6 plan_stats_grid" data-name="plan_stats_grid">
        <StatCard label="Jours avant la course" value={`J-${plan.daysToRace}`} name="plan_stat_countdown" />
        <StatCard label="Phase actuelle" value={currentWeek?.phaseLabel || '—'} name="plan_stat_phase" />
        <StatCard
          label="Prochaine séance clé"
          value={nextKeySession ? nextKeySession.dayLabel : '—'}
          name="plan_stat_next_key"
        />
        <StatCard label="FC max utilisée" value={`${maxHr} bpm`} name="plan_stat_fcmax" />
      </div>

      {nextKeySession && (
        <div className="card mb-6 border-l-4 border-l-accent plan_next_key_card" data-name="plan_next_key_card">
          <div className="flex items-center gap-1.5 text-xs font-medium text-txt-secondary mb-1 plan_next_key_title" data-name="plan_next_key_title">
            <Flag size={13} /> Prochaine séance clé — {nextKeySession.dayLabel}
          </div>
          <div className="text-base font-semibold text-txt plan_next_key_session" data-name="plan_next_key_session">{nextKeySession.title}</div>
          <div className="text-sm text-txt-secondary plan_next_key_main" data-name="plan_next_key_main">{nextKeySession.structure.main}</div>
        </div>
      )}

      {/* Références d'allures et FC cibles */}
      <div className="card mb-6 plan_pace_refs_card" data-name="plan_pace_refs_card">
        <h3 className="text-sm font-medium text-txt-secondary mb-3 plan_pace_refs_title" data-name="plan_pace_refs_title">
          Références d'allures & FC cibles <span className="font-normal text-txt-muted">(FC max utilisée : {maxHr} bpm · réglage local ou maximum observé sur 90 j)</span>
        </h3>
        <div className="data_table_scroller plan_pace_refs_scroller" data-name="plan_pace_refs_scroller">
          <table className="w-full text-sm plan_pace_refs_table" data-name="plan_pace_refs_table">
            <thead>
              <tr className="data_table_header_row plan_pace_refs_header" data-name="plan_pace_refs_header">
                <th className="data_table_header_cell_left">Zone</th>
                <th className="data_table_header_cell_right">Allure</th>
                <th className="data_table_header_cell_right">FC cible</th>
                <th className="data_table_header_cell_left pl-6">Usage</th>
              </tr>
            </thead>
            <tbody>
              {plan.paceRefs.map(ref => (
                <tr key={ref.key} className="data_table_body_row plan_pace_refs_row" data-name={`plan_pace_refs_row_${ref.key}`}>
                  <td className="data_table_body_cell_title whitespace-nowrap">{ref.label}</td>
                  <td className="data_table_body_cell_metric whitespace-nowrap">{ref.pace}</td>
                  <td className="data_table_body_cell_metric whitespace-nowrap">
                    {ref.hrPct
                      ? <>{fmtHrRange(ref.hrPct[0], ref.hrPct[1], maxHr)} <span className="text-[10px] text-txt-muted">({fmtHrPct(ref.hrPct[0], ref.hrPct[1])})</span></>
                      : '—'}
                  </td>
                  <td className="text-xs text-txt-secondary pl-6 py-2 min-w-[16rem]">{ref.usage}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Semaines */}
      <div className="flex items-center gap-1.5 text-sm font-medium text-txt-secondary mb-3 plan_weeks_title" data-name="plan_weeks_title">
        <CalendarDays size={14} /> Les {plan.weeks.length} semaines du plan
      </div>
      <div className="space-y-3 plan_weeks_list" data-name="plan_weeks_list">
        {plan.weeks.map(week => (
          <WeekCard key={week.start} week={week} maxHr={maxHr} />
        ))}
      </div>

      <p className="mt-4 text-[11px] text-txt-muted plan_footer_note" data-name="plan_footer_note">
        Plan vivant : les séances J à J+2 affichées sur le <Link to="/" className="text-brand hover:underline">Cockpit</Link> sont
        recalculées chaque jour avec les derniers runs et le sommeil ; ce calendrier est la trame de référence.
      </p>
    </div>
  )
}
