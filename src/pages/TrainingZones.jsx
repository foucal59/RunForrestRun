import React, { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell, AreaChart, Area
} from 'recharts'
import { useActivities } from '../contexts/ActivityContext'
import {
  buildZones, ZONE_COLORS, ZONE_LABELS,
  computeZoneDistribution, computeWeeklyZones, computeLoadEvolution,
  estimateRelativeEffort, getCurrentMaxHr
} from '../lib/heartRateZones'
import { parseLocalDate, localDateStr, getMonday } from '../lib/compute'
import { useNow } from '../lib/clock'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle, Tip } from '../lib/chartTheme'
import ChartCard from '../components/ChartCard'
import StatCard from '../components/StatCard'
import Loader from '../components/Loader'

const PERIODS = [
  { label: '7j', days: 7 },
  { label: '30j', days: 30 },
  { label: '90j', days: 90 },
]

export default function TrainingZones() {
  const { activities, loading } = useActivities()
  const now = useNow()
  const [period, setPeriod] = useState(90)

  // Current FC max: 90-day auto or manual override
  const maxHr = useMemo(() => getCurrentMaxHr(activities), [activities, now])

  const zones = useMemo(() => buildZones(null, maxHr), [maxHr])

  const distribution = useMemo(
    () => computeZoneDistribution(activities, zones, period),
    [activities, zones, period, now]
  )

  const weeklyZones = useMemo(
    () => computeWeeklyZones(activities, zones),
    [activities, zones]
  )

  const loadEvolution = useMemo(
    () => computeLoadEvolution(activities, zones),
    [activities, zones, now]
  )

  // Recent activities with effort
  const recentWithEffort = useMemo(() => {
    const cutoff = now - period * 86400000
    return activities
      .filter(a => parseLocalDate(a.start_date_local).getTime() >= cutoff && a.average_heartrate)
      .map(a => ({
        ...a,
        effort: estimateRelativeEffort(a.average_heartrate, a.moving_time, zones, a.max_heartrate),
        date: a.start_date_local.slice(0, 10),
      }))
      .sort((a, b) => b.effort - a.effort)
      .slice(0, 10)
  }, [activities, zones, period, now])

  // Weekly effort
  const weeklyEffort = useMemo(() => {
    const byWeek = {}
    activities.forEach(a => {
      if (!a.average_heartrate) return
      const d = parseLocalDate(a.start_date_local)
      const key = localDateStr(getMonday(d))
      if (!byWeek[key]) byWeek[key] = { week: key, effort: 0 }
      byWeek[key].effort += estimateRelativeEffort(a.average_heartrate, a.moving_time, zones, a.max_heartrate)
    })
    return Object.values(byWeek).sort((a, b) => a.week.localeCompare(b.week))
  }, [activities, zones])

  if (loading) return <Loader />

  const hrActivities = activities.filter(a => a.average_heartrate)
  const hasHR = hrActivities.length > 0

  if (!hasHR) {
    return (
      <div data-name="page_training_zones">
        <h2 className="text-xl font-semibold mb-6 text-txt training_zones_header" data-name="training_zones_header">Zones d'entrainement</h2>
        <div className="card text-center py-12 training_zones_empty_card" data-name="training_zones_empty_card">
          <p className="text-txt-muted training_zones_empty_card_aucune_activite_avec_frequence_description" data-name="training_zones_empty_card_aucune_activite_avec_frequence_description">Aucune activite avec frequence cardiaque detectee.</p>
          <p className="text-sm text-txt-muted mt-2 training_zones_empty_card_connectez_un_capteur_fc_description" data-name="training_zones_empty_card_connectez_un_capteur_fc_description">Connectez un capteur FC pour debloquer cette analyse.</p>
        </div>
      </div>
    )
  }

  return (
    <div data-name="page_training_zones">
      <div className="flex items-center justify-between mb-6 training_zones_header_row" data-name="training_zones_header_row">
        <h2 className="text-xl font-semibold text-txt training_zones_header" data-name="training_zones_header">Zones d'entrainement</h2>
        <div className="flex gap-2 training_zones_period_filters" data-name="training_zones_period_filters">
          {PERIODS.map(p => (
            <button key={p.days} onClick={() => setPeriod(p.days)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200 training_zones_period_btn ${period === p.days ? 'bg-primary text-white' : 'bg-surface-muted text-txt-secondary hover:text-txt hover:bg-surface-hover'}`} data-name="training_zones_period_btn">
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 sm:gap-4 mb-6 sm:mb-8 training_zones_stats_grid" data-name="training_zones_stats_grid">
        <StatCard label="FC max estimee" value={maxHr} unit="bpm" name="training_zones_stat_max_hr" />
        <StatCard label="Aerobic" value={distribution.aerobic} unit="%" name="training_zones_stat_aerobic" />
        <StatCard label="Anaerobic" value={distribution.anaerobic} unit="%" name="training_zones_stat_anaerobic" />
        <StatCard label="Temps total" value={Math.round(distribution.totalMinutes / 60)} unit="h" name="training_zones_stat_total_time" />
        <StatCard label="Activites FC" value={hrActivities.length} name="training_zones_stat_hr_activities" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 mb-6 page_training_zones_grid" data-name="page_training_zones_grid">
        {/* Zone Distribution Donut */}
        <ChartCard title={`Repartition ${period}j`} name="training_zones_distribution_chart">
          <div className="flex items-center gap-6 training_zones_distribution_layout" data-name="training_zones_distribution_layout">
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
            <div className="flex-1 space-y-2 training_zones_legend" data-name="training_zones_legend">
              {distribution.zoneDistribution.map((z, idx) => (
                <div key={z.zone} className={`flex items-center gap-3 training_zones_zone_${idx + 1} training_zones_zone`} data-name={`training_zones_zone_${idx + 1}`}>
                  <div className="w-3 h-3 rounded-full training_zones_zone_section" data-name="training_zones_zone_section" style={{ backgroundColor: z.color }} />
                  <div className="flex-1 training_zones_zone_min_section" data-name="training_zones_zone_min_section">
                    <div className="text-xs font-medium text-txt training_zones_zone_min_label" data-name="training_zones_zone_min_label">{z.label}</div>
                    <div className="text-xs text-txt-muted training_zones_zone_min_section_minutes_min_meta" data-name="training_zones_zone_min_section_minutes_min_meta">{z.minutes} min</div>
                  </div>
                  <div className="text-sm font-mono font-medium text-txt training_zones_zone_pct_value" data-name="training_zones_zone_pct_value">{z.pct}%</div>
                </div>
              ))}
            </div>
          </div>
        </ChartCard>

        {/* Weekly Stacked Zones */}
        {weeklyZones.length > 0 && (
          <ChartCard title="Zones hebdomadaires" subtitle="Min par zone par semaine" name="training_zones_weekly_chart">
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={weeklyZones.slice(-26)}>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="week" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={d => d.slice(5)} />
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

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 sm:gap-6 mb-6 training_zones_secondary_grid" data-name="training_zones_secondary_grid">
        {/* Aerobic/Anaerobic Load Evolution */}
        {loadEvolution.length > 0 && (
          <ChartCard title="Evolution charge aerobie/anaerobie" subtitle="Fenetre glissante 30j" name="training_zones_load_evolution_chart">
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={loadEvolution}>
                <defs>
                  <linearGradient id="gradLowAero" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#3b82f6" stopOpacity={0.02} />
                  </linearGradient>
                  <linearGradient id="gradHighAero" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#10b981" stopOpacity={0.02} />
                  </linearGradient>
                  <linearGradient id="gradAnae" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#ef4444" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#ef4444" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="date" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={d => d.slice(5)} />
                <YAxis tick={axisStyle} />
                <Tooltip content={<Tip unit="min" />} />
                <Area type="monotone" dataKey="lowAerobic" stroke="#3b82f6" strokeWidth={2} fill="url(#gradLowAero)" name="Aerobie leger" connectNulls />
                <Area type="monotone" dataKey="highAerobic" stroke="#10b981" strokeWidth={2} fill="url(#gradHighAero)" name="Aerobie intense" connectNulls />
                <Area type="monotone" dataKey="anaerobic" stroke="#ef4444" strokeWidth={2} fill="url(#gradAnae)" name="Anaerobie" connectNulls />
              </AreaChart>
            </ResponsiveContainer>
          </ChartCard>
        )}

        {/* Weekly Effort */}
        {weeklyEffort.length > 0 && (
          <ChartCard title="Effort relatif hebdomadaire" name="training_zones_weekly_effort_chart">
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={weeklyEffort.slice(-26)}>
                <defs>
                  <linearGradient id="gradEffort" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.9} />
                    <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0.4} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...gridStyle} />
                <XAxis dataKey="week" tick={{ ...axisStyle, fontSize: 9 }} tickFormatter={d => d.slice(5)} />
                <YAxis tick={axisStyle} />
                <Tooltip content={<Tip />} />
                <Bar dataKey="effort" fill="url(#gradEffort)" name="Effort" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        )}
      </div>

      {/* Top Efforts Table */}
      {recentWithEffort.length > 0 && (
        <div className="card training_zones_top_efforts_card" data-name="training_zones_top_efforts_card">
          <h3 className="text-sm font-medium text-txt-secondary mb-4 training_zones_top_efforts_title" data-name="training_zones_top_efforts_title">Top efforts ({period}j)</h3>
          <div className="space-y-1 training_zones_top_efforts_list" data-name="training_zones_top_efforts_list">
            {recentWithEffort.map(a => (
              <Link key={a.id} to={`/activity/${a.id}`}
                className="flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-surface-hover transition-colors training_zones_top_efforts_item" data-name="training_zones_top_efforts_item">
                <div>
                  <div className="text-sm text-txt font-medium training_zones_top_efforts_item_name_label" data-name="training_zones_top_efforts_item_name_label">{a.name}</div>
                  <div className="text-xs text-txt-muted training_zones_top_efforts_item_date_to_fixed_km_meta" data-name="training_zones_top_efforts_item_date_to_fixed_km_meta">{a.date} | {(a.distance / 1000).toFixed(1)} km</div>
                </div>
                <div className="text-right training_zones_top_efforts_item_bpm_moy_value" data-name="training_zones_top_efforts_item_bpm_moy_value">
                  <div className="text-lg font-mono font-semibold text-primary training_zones_top_efforts_item_bpm_moy_value_effort_value" data-name="training_zones_top_efforts_item_bpm_moy_value_effort_value">{a.effort}</div>
                  <div className="text-xs text-txt-muted training_zones_top_efforts_item_bpm_moy_value_average_heartrate_bpm_moy_meta" data-name="training_zones_top_efforts_item_bpm_moy_value_average_heartrate_bpm_moy_meta">{a.average_heartrate} bpm moy.</div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
