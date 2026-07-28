import React, { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Footprints } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useActivities } from '../contexts/ActivityContext'
import { computeGearUsage, fmtPace, parseLocalDate, shoeDisplayName } from '../lib/compute'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle, PALETTE, COLORS } from '../lib/chartTheme'
import ChartCard from '../components/ChartCard'
import Loader from '../components/Loader'

const OTHER_GEAR_KEY = '__other_gear__'
const MAX_VISIBLE_SHOES = 7
const SHOE_COLORS = [...PALETTE, COLORS.danger, COLORS.slate]

function buildShoeTimeline(activities, shoeStats) {
  const labelById = Object.fromEntries(shoeStats.map(shoe => [shoe.id, shoe.displayLabel]))
  const totals = {}
  const gearedRuns = activities.filter(activity => {
    const activityType = String(activity.sport_type || activity.type || '').toLowerCase()
    return activity.gear_id
      && !String(activity.gear_id).startsWith('b')
      && activity.start_date_local
      && activityType.includes('run')
  })
  gearedRuns.forEach(activity => {
    totals[activity.gear_id] = (totals[activity.gear_id] || 0) + (activity.distance || 0) / 1000
  })
  const usedIds = new Set(Object.keys(totals))
  const prioritizedIds = [
    ...shoeStats
      .filter(shoe => usedIds.has(shoe.id) && (shoe.primary || !shoe.retired))
      .sort((a, b) => b.last_used.localeCompare(a.last_used))
      .map(shoe => shoe.id),
    ...shoeStats
      .filter(shoe => usedIds.has(shoe.id))
      .sort((a, b) => b.last_used.localeCompare(a.last_used))
      .map(shoe => shoe.id),
    ...Object.entries(totals)
      .sort((a, b) => b[1] - a[1])
      .map(([id]) => id),
  ]
  const visibleIds = [...new Set(prioritizedIds)].slice(0, MAX_VISIBLE_SHOES)
  const visibleSet = new Set(visibleIds)
  const monthly = {}
  let otherKm = 0

  gearedRuns.forEach(activity => {
    const month = activity.start_date_local.slice(0, 7)
    const key = visibleSet.has(activity.gear_id) ? activity.gear_id : OTHER_GEAR_KEY
    if (!monthly[month]) monthly[month] = { month }
    monthly[month][key] = (monthly[month][key] || 0) + (activity.distance || 0) / 1000
    if (key === OTHER_GEAR_KEY) otherKm += (activity.distance || 0) / 1000
  })

  const data = Object.values(monthly)
    .sort((a, b) => a.month.localeCompare(b.month))
    .map(row => Object.fromEntries(
      Object.entries(row).map(([key, value]) => [key, typeof value === 'number' ? +value.toFixed(1) : value])
    ))
  const series = visibleIds.map((id, index) => ({
    key: id,
    label: labelById[id] || id,
    color: SHOE_COLORS[index % SHOE_COLORS.length],
  }))
  if (otherKm > 0) series.push({ key: OTHER_GEAR_KEY, label: 'Autres', color: '#cbd5e1' })
  return { data, series }
}

function ShoeTimelineTooltip({ active, label, payload }) {
  if (!active || !payload?.length) return null
  const rows = payload.filter(item => item.value > 0).sort((a, b) => b.value - a.value)
  return (
    <div className="tooltip_surface_card gear_timeline_tooltip" data-name="gear_timeline_tooltip">
      <div className="tooltip_date_label mb-1.5">{label}</div>
      {rows.map(item => (
        <div key={item.dataKey} className="flex items-center justify-between gap-4 text-xs">
          <span className="truncate max-w-44" style={{ color: item.color }}>{item.name}</span>
          <span className="font-mono text-txt">{Number(item.value).toFixed(1)} km</span>
        </div>
      ))}
    </div>
  )
}

function ShoeProgressBar({ pct, retirementKm }) {
  const color = pct < 0.6 ? '#10B981' : pct < 0.8 ? '#F59E0B' : '#EF4444'
  return (
    <div className="shoe-progress-container w-full gear_shoe_progress" data-name="gear_shoe_progress">
      <div className="shoe-progress-labels flex justify-between text-[10px] text-txt-muted mb-1 gear_shoe_progress_labels" data-name="gear_shoe_progress_labels">
        <span>{Math.round(pct * retirementKm)} km</span>
        <span>{retirementKm} km</span>
      </div>
      <div className="h-2 bg-surface-muted rounded-full overflow-hidden gear_shoe_progress_track" data-name="gear_shoe_progress_track">
        <div className="h-full rounded-full transition-all duration-500 gear_shoe_progress_fill" data-name="gear_shoe_progress_fill" style={{ width: `${Math.round(pct * 100)}%`, backgroundColor: color }} />
      </div>
    </div>
  )
}

export default function Gear() {
  const { activities, allActivities, loading, refresh, syncing, shoes, gearDetails } = useActivities()

  const gear = useMemo(() => {
    const raw = computeGearUsage(allActivities, shoes, gearDetails)
    // Enrich with gear details from DB (brand, model, nickname)
    raw.shoes = raw.shoes.map(s => {
      const detail = gearDetails.find(g => g.id === s.id)
      const enriched = detail ? { ...s, nickname: detail.nickname, model_name: detail.model_name, brand_name: detail.brand_name, db_distance: detail.distance } : s
      return { ...enriched, displayLabel: shoeDisplayName(enriched).primary }
    })
    return raw
  }, [allActivities, shoes, gearDetails])

  const hasGearData = allActivities.some(a => a.gear_id)
  const hasShoesData = shoes.length > 0
  const hasRawIds = gear.shoes.some(s => /^g\d+$/.test(s.name))
  const shoeTimeline = useMemo(() => buildShoeTimeline(activities, gear.shoes), [activities, gear.shoes])

  if (loading) return <Loader />

  return (
    <div className="gear-page page_gear" data-name="page_gear">
      <h2 className="text-lg sm:text-xl font-semibold mb-4 sm:mb-6 gear_header" data-name="gear_header">Matériel</h2>

      {(!hasGearData || !hasShoesData || hasRawIds) && (
        <div className="mb-4 sm:mb-6 space-y-2 gear_warnings" data-name="gear_warnings">
          {!hasGearData && (
            <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 gear_warning_no_data" data-name="gear_warning_no_data">
              <AlertTriangle size={16} className="text-amber-600 mt-0.5 flex-shrink-0 gear_warning_no_data_alert_triangle" data-name="gear_warning_no_data_alert_triangle" />
              <div className="text-sm text-amber-800 gear_warning_no_data_pour_recuperer_les_donnees_section" data-name="gear_warning_no_data_pour_recuperer_les_donnees_section">
                <span className="font-medium gear_warning_no_data_pour_recuperer_les_donnees_section_donnees_manquantes_label" data-name="gear_warning_no_data_pour_recuperer_les_donnees_section_donnees_manquantes_label">Donnees manquantes.</span>{' '}
                <button onClick={refresh} disabled={syncing} className="text-amber-700 underline hover:text-amber-900 gear_warning_button_sync" data-name="gear_warning_button_sync">
                  {syncing ? 'Synchronisation...' : 'Forcer la synchronisation'}
                </button>{' '}
                pour recuperer les donnees materiel de vos activites.
              </div>
            </div>
          )}
          {!hasShoesData && hasGearData && (
            <div className="flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg px-4 py-3 gear_warning_no_shoes_names" data-name="gear_warning_no_shoes_names">
              <AlertTriangle size={16} className="text-amber-600 mt-0.5 flex-shrink-0 gear_warning_no_shoes_names_alert_triangle" data-name="gear_warning_no_shoes_names_alert_triangle" />
              <div className="text-sm text-amber-800 gear_warning_no_shoes_names_pour_recuperer_les_noms_section" data-name="gear_warning_no_shoes_names_pour_recuperer_les_noms_section">
                <span className="font-medium gear_warning_no_shoes_names_pour_recuperer_les_noms_section_noms_de_chaussures_manquants_label" data-name="gear_warning_no_shoes_names_pour_recuperer_les_noms_section_noms_de_chaussures_manquants_label">Noms de chaussures manquants.</span>{' '}
                <a href="/api/auth/login" className="text-amber-700 underline hover:text-amber-900 gear_warning_link_reconnect" data-name="gear_warning_link_reconnect">Reconnectez-vous</a>{' '}
                pour recuperer les noms depuis Garmin.
              </div>
            </div>
          )}
        </div>
      )}

      {shoeTimeline.data.length > 0 && (
        <ChartCard
          title="Rotation des chaussures"
          subtitle="Kilomètres courus par mois · paires actives et récentes nommées, anciennes regroupées"
          className="mb-4 sm:mb-6 gear_timeline_chart"
          name="gear_timeline"
        >
          <ResponsiveContainer width="100%" height={380}>
            <BarChart data={shoeTimeline.data} margin={{ top: 8, right: 8, bottom: 8, left: -8 }}>
              <CartesianGrid {...gridStyle} vertical={false} />
              <XAxis dataKey="month" tick={axisStyle} minTickGap={28} tickFormatter={month => String(month).slice(2)} />
              <YAxis tick={axisStyle} unit=" km" width={52} />
              <Tooltip content={<ShoeTimelineTooltip />} />
              <Legend wrapperStyle={{ fontSize: 11, paddingTop: 12 }} />
              {shoeTimeline.series.map(series => (
                <Bar
                  key={series.key}
                  dataKey={series.key}
                  name={series.label}
                  stackId="shoes"
                  fill={series.color}
                  maxBarSize={28}
                  animationDuration={900}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      )}

      {!gear.shoes.length ? (
        <div className="text-center py-16 gear_empty_state" data-name="gear_empty_state">
          <Footprints size={48} className="mx-auto text-txt-muted mb-4 opacity-40 gear_empty_state_footprints" data-name="gear_empty_state_footprints" />
          <p className="text-txt-muted text-sm gear_empty_state_aucune_chaussure_detectee_dans_description" data-name="gear_empty_state_aucune_chaussure_detectee_dans_description">Aucune chaussure detectee dans vos activites.</p>
          <a href="https://connect.garmin.com" target="_blank" rel="noopener noreferrer"
            className="text-brand hover:underline text-sm mt-2 inline-block gear_empty_link_configure" data-name="gear_empty_link_configure">
            Configurer mes chaussures sur Garmin &rarr;
          </a>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-4 gear_shoe_grid" data-name="gear_shoe_grid">
          {gear.shoes.map(shoe => {
            const display = shoeDisplayName(shoe)
            return (
              <Link
                key={shoe.id}
                to={`/runs?gear=${encodeURIComponent(shoe.id)}`}
                aria-label={`Voir les ${shoe.total_runs} runs liés à ${display.primary}`}
                className={`shoe-card card group block px-4 py-4 gear_shoe_card transition-all hover:border-primary/40 hover:shadow-sm focus:outline-none focus:ring-2 focus:ring-primary/30 ${shoe.retired ? 'opacity-60' : ''}`}
                data-name={`gear_shoe_card_${shoe.id}`}
              >
                <div className="flex items-start justify-between mb-2 gear_shoe_card_header" data-name="gear_shoe_card_header">
                  <div className="min-w-0 mr-2 gear_shoe_card_title_block" data-name="gear_shoe_card_title_block">
                    <h3 className="text-sm font-semibold text-txt truncate gear_shoe_card_title_block_primary_title" data-name="gear_shoe_card_title_block_primary_title">{display.primary}</h3>
                    {display.secondary && <p className="text-xs text-txt-muted truncate gear_shoe_card_title_block_secondary_description" data-name="gear_shoe_card_title_block_secondary_description">{display.secondary}</p>}
                  </div>
                  <div className="flex gap-1.5 flex-shrink-0 gear_shoe_card_badges" data-name="gear_shoe_card_badges">
                    {shoe.primary && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-primary/10 text-primary border border-primary/20 gear_shoe_card_badge_primary" data-name="gear_shoe_card_badge_primary">Principale</span>
                    )}
                    {shoe.retired && (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-surface-muted text-txt-muted border border-surface-border gear_shoe_card_badge_retired" data-name="gear_shoe_card_badge_retired">Retiree</span>
                    )}
                    <ArrowRight size={14} className="text-txt-muted transition-transform group-hover:translate-x-0.5 gear_shoe_card_open_runs_icon" data-name="gear_shoe_card_open_runs_icon" />
                  </div>
                </div>
                <div className="text-2xl font-bold text-txt mb-1 gear_shoe_card_total_km" data-name="gear_shoe_card_total_km">
                  {Math.round(shoe.total_km)} <span className="text-sm font-normal text-txt-muted gear_shoe_card_total_km_unit" data-name="gear_shoe_card_total_km_unit">km</span>
                </div>
                <div className="flex items-center gap-2 sm:gap-3 text-xs text-txt-secondary mb-3 flex-wrap gear_shoe_card_stats" data-name="gear_shoe_card_stats">
                  <span>{shoe.total_runs} runs</span>
                  <span className="text-txt-muted gear_shoe_card_stats_meta" data-name="gear_shoe_card_stats_meta">&bull;</span>
                  <span>{fmtPace(shoe.avg_pace_s)}/km</span>
                  {shoe.last_used && (
                    <>
                      <span className="text-txt-muted gear_shoe_card_stats_separator_last_used" data-name="gear_shoe_card_stats_separator_last_used">&bull;</span>
                      <span>{parseLocalDate(shoe.last_used).toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', year: 'numeric' })}</span>
                    </>
                  )}
                </div>
                {!shoe.retired && <ShoeProgressBar pct={shoe.pct} retirementKm={gear.retirementKm} />}
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
