import React, { useEffect, useMemo, useState } from 'react'
import {
  BarChart, Bar, Cell, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Activity, CloudSun } from 'lucide-react'
import { loadRunWeather } from '../api'
import {
  buildRunRankingInsights,
  buildRunWeatherSummary,
  fmtOrdinalFr,
  fmtPaceFromSpeed,
  fmtSpeedKmh,
} from '../lib/compute'
import { AXIS_STYLE as axisStyle, GRID_STYLE as gridStyle } from '../lib/chartTheme'

function formatNumber(value, digits = 0) {
  if (!Number.isFinite(Number(value))) return '-'
  return new Intl.NumberFormat('fr-FR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value))
}

function formatTemp(value) {
  if (!Number.isFinite(Number(value))) return '-'
  return `${formatNumber(value, 0)} °C`
}

function SpeedCard({ activity }) {
  return (
    <div className="rounded-xl border border-surface-border bg-surface-muted/45 p-3 run_split_speed_card" data-name="run_split_speed_card">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[10px] text-txt-muted uppercase tracking-wider run_split_speed_label" data-name="run_split_speed_label">Vitesse moyenne</div>
          <div className="text-2xl font-mono font-semibold text-txt mt-1 run_split_speed_value" data-name="run_split_speed_value">
            {fmtSpeedKmh(activity.average_speed)}
            <span className="text-xs text-txt-secondary ml-1 run_split_speed_unit" data-name="run_split_speed_unit">km/h</span>
          </div>
        </div>
        <div className="w-9 h-9 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center flex-shrink-0 run_split_speed_icon" data-name="run_split_speed_icon">
          <Activity size={18} />
        </div>
      </div>
      <div className="text-xs text-txt-muted mt-2 font-mono run_split_speed_pace" data-name="run_split_speed_pace">
        {fmtPaceFromSpeed(activity.average_speed)}/km
      </div>
    </div>
  )
}

function RunWeatherCard({ activity }) {
  const fallback = useMemo(() => buildRunWeatherSummary(activity), [activity])
  const [weather, setWeather] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setWeather(null)
    setLoading(false)
    // Météo en base (weather_*) utilisable même sans start_latlng dans le
    // payload ; le fetch Open-Meteo, lui, exige les coordonnées GPS.
    const hasDbWeather = activity?.weather_temperature != null
    if (!activity?.id || (!hasDbWeather && !activity?.start_latlng?.length)) return () => { cancelled = true }

    setLoading(true)
    loadRunWeather(activity)
      .then(data => {
        if (!cancelled) setWeather(data)
      })
      .catch(e => console.warn('[RunWeatherCard] weather unavailable:', e?.message || e))
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [activity?.id, activity?.start_date_local])

  const display = useMemo(() => {
    if (weather) {
      const details = []
      if (Number.isFinite(weather.apparentTemperature) && Math.round(weather.apparentTemperature) !== Math.round(weather.temperature)) {
        details.push(`ressenti ${formatTemp(weather.apparentTemperature)}`)
      }
      if (Number.isFinite(weather.humidity)) details.push(`${formatNumber(weather.humidity)}% rh`)
      if (Number.isFinite(weather.windSpeed)) details.push(`vent ${formatNumber(weather.windSpeed)} km/h`)
      if (Number.isFinite(weather.precipitation) && weather.precipitation > 0) details.push(`${formatNumber(weather.precipitation, 1)} mm`)
      return {
        emoji: weather.emoji,
        label: weather.label,
        temperatureLabel: formatTemp(weather.temperature),
        details,
        source: weather.source,
      }
    }
    if (fallback) {
      return {
        emoji: fallback.emoji,
        label: fallback.label,
        temperatureLabel: fallback.temperatureLabel,
        details: fallback.rangeLabel ? [fallback.rangeLabel] : [],
        source: fallback.source,
      }
    }
    return null
  }, [fallback, weather])

  return (
    <div className="rounded-xl border border-surface-border bg-surface-muted/45 p-3 run_split_weather_card" data-name="run_split_weather_card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] text-txt-muted uppercase tracking-wider run_split_weather_label" data-name="run_split_weather_label">Météo du run</div>
          {display ? (
            <>
              <div className="text-2xl font-mono font-semibold text-txt mt-1 run_split_weather_value" data-name="run_split_weather_value">
                <span className="mr-1" aria-hidden="true">{display.emoji}</span>{display.temperatureLabel}
              </div>
              <div className="text-xs text-txt-muted mt-1 truncate run_split_weather_details" data-name="run_split_weather_details">
                {display.label}{display.details.length ? ` · ${display.details.join(' · ')}` : ''}
              </div>
            </>
          ) : (
            <>
              <div className="text-2xl font-mono font-semibold text-txt mt-1 run_split_weather_value_empty" data-name="run_split_weather_value_empty">
                {loading ? '...' : '-'}
              </div>
              <div className="text-xs text-txt-muted mt-1 run_split_weather_details_empty" data-name="run_split_weather_details_empty">
                {loading ? 'Chargement météo' : 'Météo indisponible'}
              </div>
            </>
          )}
        </div>
        <div className="w-9 h-9 rounded-lg bg-sky-50 text-sky-600 flex items-center justify-center flex-shrink-0 run_split_weather_icon" data-name="run_split_weather_icon">
          {display?.emoji ? <span className="text-lg" aria-hidden="true">{display.emoji}</span> : <CloudSun size={18} />}
        </div>
      </div>
      <div className="text-[10px] text-txt-muted mt-2 run_split_weather_source" data-name="run_split_weather_source">
        {display?.source || (loading ? 'Open-Meteo' : 'GPS/température requis')}
      </div>
    </div>
  )
}

function RankTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  return (
    <div className="tooltip_surface_card text-xs run_rank_tooltip" data-name="run_rank_tooltip">
      <div className="font-medium text-txt run_rank_tooltip_label" data-name="run_rank_tooltip_label">{d?.tooltip || label}</div>
      <div className="font-mono text-primary run_rank_tooltip_count" data-name="run_rank_tooltip_count">{d?.count} run{d?.count > 1 ? 's' : ''}</div>
      {d?.isCurrent && <div className="text-txt-muted mt-1 run_rank_tooltip_current" data-name="run_rank_tooltip_current">tranche du run</div>}
    </div>
  )
}

function RankDistributionCard({ name, title, subtitle, rank, total, data, highlightColor, baseColor, axisLabel, rankNote, details }) {
  if (!data?.length) return null
  const interval = data.length > 12 ? Math.ceil(data.length / 8) : 0

  return (
    <div className="rounded-xl border border-surface-border bg-white p-3 run_rank_card" data-name={`run_rank_card_${name}`}>
      <div className="flex items-start justify-between gap-3 mb-2 run_rank_header" data-name="run_rank_header">
        <div className="min-w-0">
          <div className="text-xs font-medium text-txt-secondary run_rank_title" data-name="run_rank_title">{title}</div>
          <div className="text-[10px] text-txt-muted truncate run_rank_subtitle" data-name="run_rank_subtitle">{subtitle}</div>
        </div>
        <div className="text-right flex-shrink-0 run_rank_value_block" data-name="run_rank_value_block">
          <div className="text-xl font-mono font-semibold text-txt run_rank_value" data-name="run_rank_value">{fmtOrdinalFr(rank)}</div>
          <div className="text-[10px] text-txt-muted run_rank_total" data-name="run_rank_total">sur {total || '-'}</div>
          {rankNote && <div className="text-[10px] font-medium text-primary run_rank_note" data-name="run_rank_note">{rankNote}</div>}
        </div>
      </div>

      <div className="h-[150px] run_rank_chart" data-name="run_rank_chart">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -18 }}>
            <CartesianGrid {...gridStyle} vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ ...axisStyle, fontSize: 9 }}
              interval={interval}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              tick={{ ...axisStyle, fontSize: 9 }}
              allowDecimals={false}
              width={28}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip content={<RankTooltip />} />
            <Bar dataKey="count" radius={[3, 3, 0, 0]} maxBarSize={28}>
              {data.map(item => (
                <Cell key={item.key} fill={item.isCurrent ? highlightColor : baseColor} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="text-[10px] text-txt-muted text-right mt-1 run_rank_axis_label" data-name="run_rank_axis_label">{axisLabel}</div>
      {details?.length > 0 && (
        <div className="mt-2 pt-2 border-t border-surface-border space-y-1 run_rank_details" data-name="run_rank_details">
          {details.map((line, i) => (
            <div key={i} className="flex items-baseline justify-between gap-2 text-[10px] run_rank_detail_line" data-name={`run_rank_detail_${i}`}>
              <span className="text-txt-muted flex-shrink-0 run_rank_detail_label" data-name="run_rank_detail_label">{line.label}</span>
              <span className="font-mono text-txt-secondary text-right run_rank_detail_value" data-name="run_rank_detail_value">{line.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function RunSplitSummary({ activity, allActivities, className = '' }) {
  const insights = useMemo(
    () => buildRunRankingInsights(activity, allActivities),
    [activity, allActivities]
  )

  if (!activity) return null

  return (
    <section className={`run_split_summary ${className}`} data-name="run_split_summary">
      <div className="text-xs font-medium text-txt-secondary mb-2 run_split_summary_title" data-name="run_split_summary_title">
        Splits et classements
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 run_split_facts" data-name="run_split_facts">
        <SpeedCard activity={activity} />
        <RunWeatherCard activity={activity} />
      </div>
      {insights && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-3 run_split_rankings" data-name="run_split_rankings">
          <RankDistributionCard
            name="distance"
            title="Nième la plus longue"
            subtitle={insights.distance.valueLabel}
            rank={insights.distance.rank}
            total={insights.distance.total}
            data={insights.distance.data}
            highlightColor="#2563EB"
            baseColor="#DBEAFE"
            axisLabel="kilomètres"
          />
          <RankDistributionCard
            name="pace"
            title="Nième la plus rapide"
            subtitle={`runs ≥ ${insights.pace.minDistanceLabel} · ${insights.pace.valueLabel}`}
            rank={insights.pace.rank}
            total={insights.pace.total}
            data={insights.pace.data}
            highlightColor="#059669"
            baseColor="#D1FAE5"
            axisLabel="allure"
            rankNote={insights.pace.percentLabel}
            details={[
              insights.pace.medianLabel
                ? { label: 'Médiane du scope', value: insights.pace.medianLabel }
                : null,
              insights.pace.isRecord
                ? { label: 'Record du scope', value: 'ce run 🏆' }
                : insights.pace.best
                  ? { label: 'Record du scope', value: `${insights.pace.best.paceLabel} · ${insights.pace.best.dateLabel}${insights.pace.best.gapLabel ? ` · ${insights.pace.best.gapLabel}` : ''}` }
                  : null,
              !insights.pace.isRecord && insights.pace.nextFaster
                ? { label: 'À battre', value: `${insights.pace.nextFaster.paceLabel} · ${insights.pace.nextFaster.dateLabel}${insights.pace.nextFaster.gapLabel ? ` · ${insights.pace.nextFaster.gapLabel}` : ''}` }
                : null,
            ].filter(Boolean)}
          />
        </div>
      )}
    </section>
  )
}
