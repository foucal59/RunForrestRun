import React from 'react'
import { AlertTriangle, AlertCircle, Info } from 'lucide-react'

const icons = { warning: AlertTriangle, danger: AlertCircle, info: Info }
const colors = {
  warning: 'border-amber-300 bg-amber-50 text-amber-700',
  danger: 'border-red-300 bg-red-50 text-red-700',
  info: 'border-blue-300 bg-blue-50 text-blue-700',
}

export default function AlertBanner({ alerts = [] }) {
  if (!alerts.length) return null
  return (
    <div className="space-y-2 mb-6 alert_banner_list" data-name="alert_banner_list">
      {alerts.map((a, i) => {
        const Icon = icons[a.type] || Info
        return (
          <div key={i}
            className={`flex items-center gap-3 px-4 py-3 rounded-lg border alert_banner alert_banner_${a.type || 'info'} ${colors[a.type] || colors.info}`}
            data-name={`alert_banner_${a.type || 'info'}`}>
            <Icon size={16} className="flex-shrink-0 alert_banner_icon" data-name="alert_banner_icon" />
            <span className="text-sm alert_banner_message" data-name="alert_banner_message">{a.message}</span>
          </div>
        )
      })}
    </div>
  )
}
