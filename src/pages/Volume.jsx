import React, { useState, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, AreaChart, Area
} from 'recharts'
import { Search } from 'lucide-react'
import { useActivities } from '../contexts/ActivityContext'
import { computeMonthly, computeYearly, computeRolling, parseLocalDate, localDateStr, getMonday, fmtPace, fmtTime } from '../lib/compute'
import ChartCard from '../components/ChartCard'
import Loader from '../components/Loader'
import WeeklyHeatmap from '../components/WeeklyHeatmap'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle, Tip, COLORS } from '../lib/chartTheme'

const ROLLING_VOLUME_CHART_COLOR = '#f97316'
const VOLUME_CHART_COLOR = COLORS.primary
const REGULARITY_CHART_COLOR = COLORS.success

function computeYearlyRegularity(activities) {
  const byYear = {}
  activities.forEach(a => {
    const d = parseLocalDate(a.start_date_local)
    const year = d.getFullYear()
    byYear[year] = (byYear[year] || 0) + 1
  })
  return Object.entries(byYear)
    .map(([year, runs]) => ({ year: Number(year), runs }))
    .sort((a, b) => a.year - b.year)
}

function computeMonthlyRegularity(activities) {
  const byMonth = {}
  activities.forEach(a => {
    const d = parseLocalDate(a.start_date_local)
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    byMonth[key] = (byMonth[key] || 0) + 1
  })
  return Object.entries(byMonth)
    .map(([month, runs]) => ({ month, runs }))
    .sort((a, b) => a.month.localeCompare(b.month))
}

function computeWeeklyRegularity(activities) {
  const byWeek = {}
  activities.forEach(a => {
    const d = parseLocalDate(a.start_date_local)
    const key = localDateStr(getMonday(d))
    byWeek[key] = (byWeek[key] || 0) + 1
  })
  return Object.entries(byWeek)
    .map(([week, runs]) => ({ week, runs }))
    .sort((a, b) => a.week.localeCompare(b.week))
}

const PAGE_SIZE = 50

function RunsList() {
  const { allActivities, loading } = useActivities()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const [, setSearchParams] = useSearchParams()

  console.log('[RunsList] render, allActivities:', allActivities.length)

  const filtered = useMemo(() => {
    let runs = [...allActivities]
    if (search.trim()) {
      const q = search.toLowerCase()
      runs = runs.filter(a =>
        a.name?.toLowerCase().includes(q) ||
        a.start_date_local?.includes(q)
      )
    }
    // Default sort: most recent first
    runs.sort((a, b) => {
      if (a.start_date_local < b.start_date_local) return 1
      if (a.start_date_local > b.start_date_local) return -1
      return 0
    })
    return runs
  }, [allActivities, search])

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const pageRuns = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  function handleSearch(e) {
    setSearch(e.target.value)
    setPage(0)
  }

  function openRun(id) {
    setSearchParams({ run: id })
  }

  if (loading) return <Loader />

  return (
    <div data-name="volume_runs_list">
      {/* Search input */}
      <div className="relative mb-3 max-w-xs volume_runs_search" data-name="volume_runs_search">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-txt-secondary volume_runs_search_search" data-name="volume_runs_search_search" />
        <input
          type="text"
          value={search}
          onChange={handleSearch}
          placeholder="Rechercher…"
          className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg bg-surface border border-border focus:outline-none focus:ring-1 focus:ring-brand volume_runs_search_input"
          data-name="volume_runs_search_input"
        />
      </div>

      {/* Desktop table */}
      <div className="hidden sm:block overflow-x-auto rounded-lg border border-border volume_runs_table_wrapper" data-name="volume_runs_table_wrapper">
        <table className="w-full text-sm volume_runs_table" data-name="volume_runs_table">
          <thead>
            <tr className="border-b border-border bg-surface-alt text-txt-secondary volume_runs_table_header" data-name="volume_runs_table_header">
              <th className="px-3 py-2 text-left font-medium volume_runs_table_header_date_cell" data-name="volume_runs_table_header_date_cell">Date</th>
              <th className="px-3 py-2 text-left font-medium volume_runs_table_header_nom_cell" data-name="volume_runs_table_header_nom_cell">Nom</th>
              <th className="px-3 py-2 text-right font-medium volume_runs_table_header_dist_cell" data-name="volume_runs_table_header_dist_cell">Dist.</th>
              <th className="px-3 py-2 text-right font-medium volume_runs_table_header_temps_cell" data-name="volume_runs_table_header_temps_cell">Temps</th>
              <th className="px-3 py-2 text-right font-medium volume_runs_table_header_allure_cell" data-name="volume_runs_table_header_allure_cell">Allure</th>
              <th className="px-3 py-2 text-right font-medium volume_runs_table_header_fc_moy_cell" data-name="volume_runs_table_header_fc_moy_cell">FC moy</th>
              <th className="px-3 py-2 text-right font-medium volume_runs_table_header_d_cell" data-name="volume_runs_table_header_d_cell">D+</th>
            </tr>
          </thead>
          <tbody>
            {pageRuns.map((run, i) => {
              const date = run.start_date_local ? run.start_date_local.slice(0, 10) : '—'
              const distKm = run.distance ? (run.distance / 1000).toFixed(2) : '—'
              const time = run.moving_time ? fmtTime(run.moving_time) : '—'
              const pace = run.average_speed && run.average_speed > 0
                ? fmtPace(1000 / run.average_speed)
                : '—'
              const hr = run.average_heartrate ? Math.round(run.average_heartrate) : '—'
              const elev = run.total_elevation_gain != null ? Math.round(run.total_elevation_gain) : '—'
              return (
                <tr
                  key={run.id}
                  onClick={() => openRun(run.id)}
                  className={`cursor-pointer hover:bg-surface-hover transition-colors volume_runs_table_row ${i % 2 === 0 ? 'bg-surface' : 'bg-surface-alt'}`}
                  data-name="volume_runs_table_row"
                >
                  <td className="px-3 py-1.5 text-txt-secondary whitespace-nowrap volume_runs_table_row_date_cell" data-name="volume_runs_table_row_date_cell">{date}</td>
                  <td className="px-3 py-1.5 text-txt max-w-[200px] truncate volume_runs_table_row_name_cell" data-name="volume_runs_table_row_name_cell">{run.name || '—'}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums volume_runs_table_row_dist_km_cell" data-name="volume_runs_table_row_dist_km_cell">{distKm} km</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-txt-secondary volume_runs_table_row_time_cell" data-name="volume_runs_table_row_time_cell">{time}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-txt-secondary volume_runs_table_row_pace_cell" data-name="volume_runs_table_row_pace_cell">{pace}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-txt-secondary volume_runs_table_row_bpm_hr_cell" data-name="volume_runs_table_row_bpm_hr_cell">{hr !== '—' ? `${hr} bpm` : '—'}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums text-txt-secondary volume_runs_table_row_m_elev_cell" data-name="volume_runs_table_row_m_elev_cell">{elev !== '—' ? `${elev} m` : '—'}</td>
                </tr>
              )
            })}
            {pageRuns.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-txt-secondary volume_runs_table_aucune_course_trouvee_cell" data-name="volume_runs_table_aucune_course_trouvee_cell">Aucune course trouvée</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Mobile cards */}
      <div className="sm:hidden space-y-2 volume_runs_mobile_list" data-name="volume_runs_mobile_list">
        {pageRuns.map(run => {
          const date = run.start_date_local ? run.start_date_local.slice(0, 10) : '—'
          const distKm = run.distance ? (run.distance / 1000).toFixed(2) : '—'
          const time = run.moving_time ? fmtTime(run.moving_time) : '—'
          const pace = run.average_speed && run.average_speed > 0
            ? fmtPace(1000 / run.average_speed)
            : '—'
          const hr = run.average_heartrate ? Math.round(run.average_heartrate) : null
          const elev = run.total_elevation_gain != null ? Math.round(run.total_elevation_gain) : null
          return (
            <div
              key={run.id}
              onClick={() => openRun(run.id)}
              className="bg-surface border border-border rounded-lg px-3 py-2.5 cursor-pointer hover:bg-surface-hover transition-colors volume_runs_mobile_card"
              data-name="volume_runs_mobile_card"
            >
              <div className="flex justify-between items-start gap-2 mb-1 volume_runs_mobile_card_section" data-name="volume_runs_mobile_card_section">
                <span className="font-medium text-txt text-sm truncate volume_runs_mobile_card_section_name_label" data-name="volume_runs_mobile_card_section_name_label">{run.name || '—'}</span>
                <span className="text-xs text-txt-secondary whitespace-nowrap volume_runs_mobile_card_section_date_text" data-name="volume_runs_mobile_card_section_date_text">{date}</span>
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-txt-secondary volume_runs_mobile_card_hr_elev_section" data-name="volume_runs_mobile_card_hr_elev_section">
                <span>{distKm} km</span>
                <span>{time}</span>
                <span>{pace} /km</span>
                {hr && <span>{hr} bpm</span>}
                {elev != null && <span>D+ {elev} m</span>}
              </div>
            </div>
          )
        })}
        {pageRuns.length === 0 && (
          <p className="text-center text-txt-secondary text-sm py-6 volume_runs_mobile_list_aucune_course_trouvee_text" data-name="volume_runs_mobile_list_aucune_course_trouvee_text">Aucune course trouvée</p>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-3 text-sm volume_runs_pagination" data-name="volume_runs_pagination">
          <span className="text-txt-secondary volume_runs_pagination_page_page_total_pages_text" data-name="volume_runs_pagination_page_page_total_pages_text">
            Page {page + 1} / {totalPages} — {filtered.length} courses
          </span>
          <div className="flex gap-2 volume_runs_pagination_precedent_suivant_section" data-name="volume_runs_pagination_precedent_suivant_section">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1 rounded bg-surface border border-border hover:bg-surface-hover disabled:opacity-40 disabled:cursor-not-allowed transition-colors volume_runs_pagination_prev"
              data-name="volume_runs_pagination_prev"
            >
              Précédent
            </button>
            <button
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="px-3 py-1 rounded bg-surface border border-border hover:bg-surface-hover disabled:opacity-40 disabled:cursor-not-allowed transition-colors volume_runs_pagination_next"
              data-name="volume_runs_pagination_next"
            >
              Suivant
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function Volume() {
  const { activities, loading, allActivities } = useActivities()

  console.log('[Volume] render, allActivities:', allActivities.length)

  // Rolling charts must use the unfiltered set: by definition a "X-day
  // rolling" sum needs at least X days of lookback regardless of any UI
  // date filter. With `activities` (filtered), a 365-day rolling on a
  // 90-day-filtered view would clamp to whatever 90 days are visible and
  // the chart would show near-flat / artificially low km totals.
  const rolling7 = useMemo(() => computeRolling(allActivities, 7), [allActivities])
  const rolling90 = useMemo(() => computeRolling(allActivities, 90), [allActivities])
  const rolling365 = useMemo(() => computeRolling(allActivities, 365), [allActivities])
  const monthly = useMemo(() => computeMonthly(activities), [activities])
  const yearly = useMemo(() => computeYearly(activities), [activities])
  const weekly = useMemo(() => {
    if (!activities || activities.length === 0) return []
    const byWeek = {}
    activities.forEach(a => {
      const weekKey = localDateStr(getMonday(parseLocalDate(a.start_date_local)))
      byWeek[weekKey] = (byWeek[weekKey] || 0) + (a.distance || 0)
    })
    return Object.entries(byWeek).map(([week, dist]) => ({ week, km: Math.round((dist / 1000) * 100) / 100 })).sort((a, b) => a.week.localeCompare(b.week))
  }, [activities])

  const yearlyReg = useMemo(() => computeYearlyRegularity(activities), [activities])
  const monthlyReg = useMemo(() => computeMonthlyRegularity(activities), [activities])
  const weeklyReg = useMemo(() => computeWeeklyRegularity(activities), [activities])

  if (loading) return <Loader />

  return (
    <div data-name="page_volume">
      <h2 className="text-xl font-semibold mb-6 volume_header" data-name="volume_header">Volume</h2>

      <WeeklyHeatmap />

      <div className="mt-4 volume_rolling_section" data-name="volume_rolling_section">
        <h3 className="text-sm font-medium text-txt-secondary mb-3 volume_rolling_title" data-name="volume_rolling_title">Volumes glissants</h3>

      {/* Row 1: Rolling volumes */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-6 volume_rolling_grid" data-name="volume_rolling_grid">
        <ChartCard title="Volume 365 jours glissants" name="volume_rolling_365">
          {rolling365.length > 0 && (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={rolling365}>
                <defs>
                  <linearGradient id="gradRolling365" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={ROLLING_VOLUME_CHART_COLOR} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={ROLLING_VOLUME_CHART_COLOR} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 10 }} tickFormatter={d => d.slice(5)} />
                <YAxis tick={axisStyle} />
                <Tooltip content={<Tip />} />
                <Area type="monotone" dataKey="km" stroke={ROLLING_VOLUME_CHART_COLOR} strokeWidth={2} fill="url(#gradRolling365)" name="365j (km)" connectNulls animationDuration={1200} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard title="Volume 90 jours glissants" name="volume_rolling_90">
          {rolling90.length > 0 && (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={rolling90}>
                <defs>
                  <linearGradient id="gradRolling90" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={ROLLING_VOLUME_CHART_COLOR} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={ROLLING_VOLUME_CHART_COLOR} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 10 }} tickFormatter={d => d.slice(5)} />
                <YAxis tick={axisStyle} />
                <Tooltip content={<Tip />} />
                <Area type="monotone" dataKey="km" stroke={ROLLING_VOLUME_CHART_COLOR} strokeWidth={2} fill="url(#gradRolling90)" name="90j (km)" connectNulls animationDuration={1200} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard title="Volume 7 jours glissants" name="volume_rolling_7">
          {rolling7.length > 0 && (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={rolling7}>
                <defs>
                  <linearGradient id="gradRolling7" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={ROLLING_VOLUME_CHART_COLOR} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={ROLLING_VOLUME_CHART_COLOR} stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 10 }} tickFormatter={d => d.slice(5)} />
                <YAxis tick={axisStyle} />
                <Tooltip content={<Tip />} />
                <Area type="monotone" dataKey="km" stroke={ROLLING_VOLUME_CHART_COLOR} strokeWidth={2} fill="url(#gradRolling7)" name="7j (km)" connectNulls animationDuration={1200} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </ChartCard>
      </div>

      {/* Row 2: Volume annuel / mensuel / hebdo */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-6 mt-4 volume_totals_grid" data-name="volume_totals_grid">
        <ChartCard title="Volume annuel" name="volume_yearly_bar">
          {yearly.length > 0 && (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={yearly}>
                <defs>
                  <linearGradient id="gradYearly" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={VOLUME_CHART_COLOR} stopOpacity={0.9} />
                    <stop offset="100%" stopColor={VOLUME_CHART_COLOR} stopOpacity={0.3} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="year" tick={axisStyle} />
                <YAxis tick={axisStyle} />
                <Tooltip content={<Tip />} />
                <Bar dataKey="km" fill="url(#gradYearly)" name="km" radius={[6, 6, 0, 0]} animationDuration={800} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard title="Volume mensuel" name="volume_monthly_bar">
          {monthly.length > 0 && (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={monthly}>
                <defs>
                  <linearGradient id="gradMonthly" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={VOLUME_CHART_COLOR} stopOpacity={0.9} />
                    <stop offset="100%" stopColor={VOLUME_CHART_COLOR} stopOpacity={0.4} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey={d => `${d.year}-${d.month}`} tick={{ ...axisStyle, fontSize: 10 }} />
                <YAxis tick={axisStyle} />
                <Tooltip content={<Tip />} />
                <Bar dataKey="km" fill="url(#gradMonthly)" name="km" radius={[4, 4, 0, 0]} animationDuration={800} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard title="Volume hebdo" name="volume_weekly_bar">
          {weekly.length > 0 && (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={weekly}>
                <defs>
                  <linearGradient id="gradWeekly" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={VOLUME_CHART_COLOR} stopOpacity={0.85} />
                    <stop offset="100%" stopColor={VOLUME_CHART_COLOR} stopOpacity={0.35} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="week" tick={{ ...axisStyle, fontSize: 10 }} />
                <YAxis tick={axisStyle} />
                <Tooltip content={<Tip />} />
                <Bar dataKey="km" fill="url(#gradWeekly)" name="km" radius={[4, 4, 0, 0]} animationDuration={800} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>
      </div>

      {/* Row 3: Regularite annuelle / mensuelle / hebdo */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 sm:gap-6 mt-4 volume_regularity_grid" data-name="volume_regularity_grid">
        <ChartCard title="Regularite annuelle" subtitle="Nombre de sorties par an" name="volume_regularity_yearly">
          {yearlyReg.length > 0 && (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={yearlyReg}>
                <defs>
                  <linearGradient id="gradYearlyReg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={REGULARITY_CHART_COLOR} stopOpacity={0.9} />
                    <stop offset="100%" stopColor={REGULARITY_CHART_COLOR} stopOpacity={0.3} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="year" tick={axisStyle} />
                <YAxis tick={axisStyle} allowDecimals={false} />
                <Tooltip content={<Tip />} />
                <Bar dataKey="runs" fill="url(#gradYearlyReg)" name="Sorties" radius={[6, 6, 0, 0]} animationDuration={800} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard title="Regularite mensuelle" subtitle="Nombre de sorties par mois" name="volume_regularity_monthly">
          {monthlyReg.length > 0 && (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={monthlyReg}>
                <defs>
                  <linearGradient id="gradMonthlyReg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={REGULARITY_CHART_COLOR} stopOpacity={0.9} />
                    <stop offset="100%" stopColor={REGULARITY_CHART_COLOR} stopOpacity={0.35} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="month" tick={{ ...axisStyle, fontSize: 10 }} tickFormatter={d => d.slice(5)} />
                <YAxis tick={axisStyle} allowDecimals={false} />
                <Tooltip content={<Tip />} />
                <Bar dataKey="runs" fill="url(#gradMonthlyReg)" name="Sorties" radius={[4, 4, 0, 0]} animationDuration={800} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard title="Regularite hebdo" subtitle="Nombre de sorties par semaine" name="volume_regularity_weekly">
          {weeklyReg.length > 0 && (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={weeklyReg}>
                <defs>
                  <linearGradient id="gradWeeklyReg" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={REGULARITY_CHART_COLOR} stopOpacity={0.9} />
                    <stop offset="100%" stopColor={REGULARITY_CHART_COLOR} stopOpacity={0.4} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="week" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={d => d.slice(5)} />
                <YAxis tick={axisStyle} allowDecimals={false} />
                <Tooltip content={<Tip />} />
                <Bar dataKey="runs" fill="url(#gradWeeklyReg)" name="Sorties" radius={[3, 3, 0, 0]} animationDuration={800} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>
      </div>

      </div>{/* end Volumes glissants wrapper */}

      {/* Courses list */}
      <div className="mt-8 volume_runs_section" data-name="volume_runs_section">
        <h3 className="text-sm font-medium text-txt-secondary mb-3 volume_runs_section_title" data-name="volume_runs_section_title">Courses</h3>
        <RunsList />
      </div>
    </div>
  )
}
