import React, { useEffect, useMemo, useState, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ChevronUp, ChevronDown, Search, X } from 'lucide-react'
import posthog from 'posthog-js'
import { useActivities } from '../contexts/ActivityContext'
import {
  buildRunWeatherSummary,
  computeRunRankingIndex,
  fmtSpeedKmh,
  parseLocalDate,
  fmtTime,
  fmtPaceFromSpeed as fmtSpeed,
  shoeDisplayName,
  normalizeGearId,
} from '../lib/compute'
import Loader from '../components/Loader'

const PAGE_SIZE = 50

export default function Runs() {
  const { allActivities, loading, shoes, gearDetails } = useActivities()
  const [search, setSearch] = useState('')
  const [sortKey, setSortKey] = useState('date')
  const [sortDir, setSortDir] = useState('desc')
  const [page, setPage] = useState(0)
  const [searchParams, setSearchParams] = useSearchParams()
  const gearId = searchParams.get('gear')
  const openRun = (id) => {
    const next = new URLSearchParams(searchParams)
    next.set('run', id)
    setSearchParams(next)
  }
  const searchDebounceRef = useRef(null)

  const selectedGearName = useMemo(() => {
    if (!gearId) return ''
    // Lookup direct dans le profil — inutile de recalculer tout computeGearUsage
    const norm = normalizeGearId(gearId)
    const info = shoes.find(s => s.id && normalizeGearId(s.id) === norm)
      || gearDetails.find(g => g.id && normalizeGearId(g.id) === norm)
      || {}
    const gearNameFromActs = allActivities.find(a => a.gear_id === gearId && a.gear_name)?.gear_name
    return shoeDisplayName({ ...info, name: info.name || gearNameFromActs || gearId }).primary
  }, [allActivities, gearDetails, gearId, shoes])

  useEffect(() => {
    setPage(0)
  }, [gearId])

  useEffect(() => () => clearTimeout(searchDebounceRef.current), [])

  const handleSearch = (value) => {
    setSearch(value)
    setPage(0)
    if (value.trim().length >= 2) {
      clearTimeout(searchDebounceRef.current)
      searchDebounceRef.current = setTimeout(() => {
        posthog.capture('activity_searched', { query_length: value.trim().length })
      }, 800)
    }
  }

  const rankings = useMemo(() => {
    console.log('[Runs] computing rankings for', allActivities.length, 'runs')
    return computeRunRankingIndex(allActivities)
  }, [allActivities])

  const filtered = useMemo(() => {
    let runs = [...allActivities]

    if (gearId) {
      runs = runs.filter(a => a.gear_id === gearId)
    }

    // Search filter
    if (search.trim()) {
      const q = search.toLowerCase()
      runs = runs.filter(a =>
        a.name?.toLowerCase().includes(q) ||
        a.start_date_local?.includes(q)
      )
    }

    // Sort
    runs.sort((a, b) => {
      let va, vb
      switch (sortKey) {
        case 'date': va = a.start_date_local; vb = b.start_date_local; break
        case 'distance': va = a.distance || 0; vb = b.distance || 0; break
        case 'duration': va = a.moving_time || 0; vb = b.moving_time || 0; break
        case 'pace': va = a.average_speed || 0; vb = b.average_speed || 0; break
        case 'speed_kmh': va = a.average_speed || 0; vb = b.average_speed || 0; break
        case 'hr': va = a.average_heartrate || 0; vb = b.average_heartrate || 0; break
        case 'elev': va = a.total_elevation_gain || 0; vb = b.total_elevation_gain || 0; break
        default: va = a.start_date_local; vb = b.start_date_local
      }
      if (va < vb) return sortDir === 'asc' ? -1 : 1
      if (va > vb) return sortDir === 'asc' ? 1 : -1
      return 0
    })

    return runs
  }, [allActivities, gearId, search, sortKey, sortDir])

  const totalPages = Math.ceil(filtered.length / PAGE_SIZE)
  const pageRuns = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  function toggleSort(key) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
    setPage(0)
  }

  const SortIcon = ({ col }) => {
    if (sortKey !== col) return null
    return sortDir === 'asc'
      ? <ChevronUp size={12} className="inline ml-0.5 runs_chevron_up" data-name="runs_chevron_up" />
      : <ChevronDown size={12} className="inline ml-0.5 runs_chevron_down" data-name="runs_chevron_down" />
  }

  if (loading) return <Loader />

  return (
    <div data-name="page_runs">
      <div className="flex items-center justify-between mb-4 sm:mb-6 gap-3 runs_header" data-name="runs_header">
        <div>
          <h2 className="text-lg sm:text-xl font-semibold runs_header_title" data-name="runs_header_title">
            {gearId ? `Runs avec ${selectedGearName}` : 'Toutes les sorties'}
          </h2>
          {gearId && (
            <Link
              to="/runs"
              className="inline-flex items-center gap-1 mt-1 text-xs text-brand hover:underline runs_clear_gear_filter"
              data-name="runs_clear_gear_filter"
            >
              <X size={12} /> Voir toutes les sorties
            </Link>
          )}
        </div>
        <div className="relative runs_header_search" data-name="runs_header_search">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-txt-muted runs_header_search_search" data-name="runs_header_search_search" />
          <input
            type="text"
            value={search}
            onChange={e => handleSearch(e.target.value)}
            placeholder="Rechercher..."
            className="pl-8 pr-3 py-1.5 rounded-lg border border-surface-border bg-white text-sm w-40 sm:w-56 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary runs_header_search_input"
            data-name="runs_header_search_input"
          />
        </div>
      </div>

      <div className="text-xs text-txt-muted mb-3 runs_count" data-name="runs_count">
        {filtered.length} sortie{filtered.length !== 1 ? 's' : ''}
        {gearId && ` avec ${selectedGearName}`}
        {search && ` (filtre: "${search}")`}
      </div>

      {/* Desktop table */}
      <div className="hidden sm:block card overflow-hidden p-0 runs_table_wrap" data-name="runs_table_wrap">
        <table className="w-full runs_table_wrap_table" data-name="runs_table_wrap_table">
          <thead>
            <tr className="border-b border-surface-border bg-surface-muted/50 runs_table_wrap_table_table_header_row" data-name="runs_table_wrap_table_table_header_row">
              {[
                { key: 'date', label: 'Date' },
                { key: 'name', label: 'Nom' },
                { key: 'distance', label: 'Distance' },
                { key: 'duration', label: 'Durée' },
                { key: 'pace', label: 'Allure' },
                { key: 'speed_kmh', label: 'km/h' },
                { key: 'hr', label: 'FC' },
                { key: 'elev', label: 'D+' },
                { key: 'weather', label: 'Météo' },
                { key: 'rank_dist', label: 'Long.' },
                { key: 'rank_pace', label: 'Rapide' },
              ].map(col => (
                <th key={col.key}
                  onClick={() => col.key !== 'name' && col.key !== 'rank_dist' && col.key !== 'rank_pace' && col.key !== 'weather' && toggleSort(col.key)}
                  className={`px-4 py-3 text-left text-xs font-medium text-txt-secondary uppercase tracking-wider ${col.key !== 'name' && col.key !== 'rank_dist' && col.key !== 'rank_pace' && col.key !== 'weather' ? 'cursor-pointer hover:text-txt select-none' : ''} runs_table_wrap_table_table_header_row_label_cell`} data-name="runs_table_wrap_table_table_header_row_label_cell">
                  {col.label}<SortIcon col={col.key} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-surface-border runs_table_wrap_table_table_body" data-name="runs_table_wrap_table_table_body">
            {pageRuns.map(a => {
              const weather = buildRunWeatherSummary(a)
              return (
                <tr key={a.id} className="hover:bg-surface-hover/50 transition-colors cursor-pointer runs_table_row"
                  data-name={`runs_table_row_${a.id}`} onClick={() => openRun(a.id)}>
                  <td className="px-4 py-3 text-sm text-txt-muted whitespace-nowrap runs_table_row_fr_fr_cell" data-name="runs_table_row_fr_fr_cell">
                    {parseLocalDate(a.start_date_local).toLocaleDateString('fr-FR')}
                  </td>
                  <td className="px-4 py-3 text-sm font-medium text-txt hover:text-brand transition-colors runs_table_row_name_cell" data-name="runs_table_row_name_cell">
                    {a.name}
                  </td>
                  <td className="px-4 py-3 text-sm font-mono text-txt runs_table_row_to_fixed_km_cell" data-name="runs_table_row_to_fixed_km_cell">{(a.distance / 1000).toFixed(1)} km</td>
                  <td className="px-4 py-3 text-sm font-mono text-txt-secondary runs_table_row_moving_time_cell" data-name="runs_table_row_moving_time_cell">{fmtTime(a.moving_time)}</td>
                  <td className="px-4 py-3 text-sm font-mono text-txt-secondary runs_table_row_average_speed_km_cell" data-name="runs_table_row_average_speed_km_cell">{fmtSpeed(a.average_speed)}/km</td>
                  <td className="px-4 py-3 text-sm font-mono text-txt-secondary runs_table_row_speed_kmh_cell" data-name="runs_table_row_speed_kmh_cell">{fmtSpeedKmh(a.average_speed)}</td>
                  <td className="px-4 py-3 text-sm font-mono text-txt-secondary runs_table_row_bpm_average_heartrate_cell" data-name="runs_table_row_bpm_average_heartrate_cell">{a.average_heartrate ? `${Math.round(a.average_heartrate)} bpm` : '-'}</td>
                  <td className="px-4 py-3 text-sm font-mono text-txt-secondary runs_table_row_m_total_elevation_gain_cell" data-name="runs_table_row_m_total_elevation_gain_cell">{a.total_elevation_gain ? `${Math.round(a.total_elevation_gain)} m` : '-'}</td>
                  <td className="px-4 py-3 text-sm text-txt-secondary whitespace-nowrap runs_table_row_weather_cell" data-name="runs_table_row_weather_cell">
                    {weather ? `${weather.emoji} ${weather.temperatureLabel}` : '-'}
                  </td>
                  <td className="px-4 py-3 text-right runs_table_row_id_cell" data-name="runs_table_row_id_cell">
                    {rankings.distRank[a.id] != null
                      ? <span className="text-xs text-txt-muted runs_table_row_id_cell_id_meta" data-name="runs_table_row_id_cell_id_meta">#{rankings.distRank[a.id]}<span className="text-[9px] runs_table_row_id_cell_id_meta_e_text" data-name="runs_table_row_id_cell_id_meta_e_text">e</span></span>
                      : <span className="text-xs text-txt-muted runs_table_row_id_cell_meta" data-name="runs_table_row_id_cell_meta">-</span>}
                  </td>
                  <td className="px-4 py-3 text-right runs_table_row_pace_rank_cell" data-name="runs_table_row_pace_rank_cell">
                    {rankings.paceRank[a.id] != null
                      ? <span className="text-xs text-txt-muted runs_table_row_pace_rank_meta" data-name="runs_table_row_pace_rank_meta">#{rankings.paceRank[a.id]}<span className="text-[9px] runs_table_row_pace_rank_suffix_text" data-name="runs_table_row_pace_rank_suffix_text">e</span></span>
                      : <span className="text-xs text-txt-muted runs_table_row_pace_rank_empty_meta" data-name="runs_table_row_pace_rank_empty_meta">-</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile list */}
      <div className="sm:hidden space-y-1 runs_list_mobile" data-name="runs_list_mobile">
        {pageRuns.map(a => {
          const weather = buildRunWeatherSummary(a)
          return (
            <div key={a.id} onClick={() => openRun(a.id)}
              className="flex items-center justify-between py-3 px-3 rounded-lg hover:bg-surface-hover transition-colors card mb-1 cursor-pointer runs_list_row"
              data-name={`runs_list_row_${a.id}`}>
              <div className="min-w-0 flex-1 runs_list_row_details" data-name="runs_list_row_details">
                <div className="text-sm font-medium text-txt truncate runs_list_row_details_name_label" data-name="runs_list_row_details_name_label">{a.name}</div>
                <div className="text-xs text-txt-muted mt-0.5 runs_list_row_details_fr_fr_bpm_average_heartrate_meta" data-name="runs_list_row_details_fr_fr_bpm_average_heartrate_meta">
                  {parseLocalDate(a.start_date_local).toLocaleDateString('fr-FR')}
                  {a.average_heartrate ? ` | ${Math.round(a.average_heartrate)} bpm` : ''}
                  {weather ? ` | ${weather.emoji} ${weather.temperatureLabel}` : ''}
                </div>
              </div>
              <div className="text-right ml-3 flex-shrink-0 runs_list_row_metrics" data-name="runs_list_row_metrics">
                <div className="text-sm font-mono text-txt runs_list_row_metrics_to_fixed_km_value" data-name="runs_list_row_metrics_to_fixed_km_value">{(a.distance / 1000).toFixed(1)} km</div>
                <div className="text-xs text-txt-muted font-mono runs_list_row_metrics_average_speed_km_value" data-name="runs_list_row_metrics_average_speed_km_value">{fmtSpeed(a.average_speed)}/km · {fmtSpeedKmh(a.average_speed)} km/h</div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-4 runs_pagination" data-name="runs_pagination">
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-3 py-1.5 rounded-lg text-sm bg-surface-muted hover:bg-surface-hover disabled:opacity-40 transition-colors runs_pagination_prev"
            data-name="runs_pagination_prev">
            Prev
          </button>
          <span className="text-sm text-txt-secondary runs_pagination_info" data-name="runs_pagination_info">
            {page + 1} / {totalPages}
          </span>
          <button
            onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="px-3 py-1.5 rounded-lg text-sm bg-surface-muted hover:bg-surface-hover disabled:opacity-40 transition-colors runs_pagination_next"
            data-name="runs_pagination_next">
            Next
          </button>
        </div>
      )}
    </div>
  )
}
