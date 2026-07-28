import React from 'react'

export default function ChartCard({ title, subtitle, children, className = '', name }) {
  const dataName = name ? `chart_card chart_card_${name}` : 'chart_card'
  return (
    <div className={`card ${className} ${dataName} chart_card_root`} data-name={dataName}>
      {title && (
        <div className="mb-4 chart_card_header" data-name="chart_card_header">
          <h3 className="text-sm font-semibold text-txt chart_card_title" data-name="chart_card_title">{title}</h3>
          {subtitle && <p className="text-xs text-txt-secondary mt-0.5 chart_card_subtitle" data-name="chart_card_subtitle">{subtitle}</p>}
        </div>
      )}
      {children}
    </div>
  )
}
