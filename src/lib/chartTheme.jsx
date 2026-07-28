/**
 * Shared chart styling constants for Recharts (light theme).
 */
export const AXIS_STYLE = { fontSize: 11, fill: '#64748b' }
export const GRID_STYLE = { strokeDasharray: '3 3', stroke: '#e2e8f0', strokeOpacity: 0.8 }

export const COLORS = {
  primary: '#2563EB',
  secondary: '#0EA5E9',
  brand: '#6366F1',
  success: '#10B981',
  warning: '#F59E0B',
  danger: '#EF4444',
  purple: '#8B5CF6',
  slate: '#64748B',
}

export const PALETTE = [COLORS.primary, COLORS.brand, COLORS.success, COLORS.warning, COLORS.purple, COLORS.secondary]

export function Tip({ active, payload, label, unit = '' }) {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-white border border-surface-border rounded-xl px-4 py-3 shadow-lg chart_theme_tooltip" data-name="chart_theme_tooltip">
      <div className="text-xs text-txt-secondary mb-1.5 font-medium chart_theme_tooltip_label" data-name="chart_theme_tooltip_label">{label}</div>
      {payload.map((p, i) => (
        <div key={i} className="text-sm font-mono font-medium chart_theme_tooltip_name_to_fixed_unit_value" data-name="chart_theme_tooltip_name_to_fixed_unit_value" style={{ color: p.color }}>
          {p.name}: {typeof p.value === 'number' ? p.value.toFixed(1) : p.value}{unit ? ` ${unit}` : ''}
        </div>
      ))}
    </div>
  )
}
