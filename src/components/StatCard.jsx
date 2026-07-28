import React from 'react'

function fmtValue(v) {
  if (typeof v === 'number') {
    if (Math.abs(v) >= 1000) return Math.round(v).toLocaleString('fr-FR')
    if (Number.isInteger(v)) return v.toString()
    return v.toFixed(1)
  }
  return v
}

export default function StatCard({ label, value, unit, trend, trendLabel, name }) {
  const trendColor = trend > 0 ? 'text-emerald-600' : trend < 0 ? 'text-red-600' : 'text-txt-secondary'
  const dataName = name ? `stat_card stat_card_${name}` : 'stat_card'
  return (
    <div className={`card ${dataName} stat_card_root`} data-name={dataName}>
      <div className="stat-label stat_card_label" data-name="stat_card_label">{label}</div>
      <div className="mt-1 flex items-baseline gap-1.5 stat_card_value_row" data-name="stat_card_value_row">
        <span className="stat-value text-2xl sm:text-3xl stat_card_value" data-name="stat_card_value">{fmtValue(value)}</span>
        {unit && <span className="text-xs sm:text-sm text-txt-secondary stat_card_unit" data-name="stat_card_unit">{unit}</span>}
      </div>
      {trend !== undefined && (
        <div className={`mt-1.5 text-xs stat_card_trend ${trendColor}`} data-name="stat_card_trend">
          {trend > 0 ? '+' : ''}{trend}% {trendLabel || ''}
        </div>
      )}
    </div>
  )
}
