import React, { useState, useMemo, useCallback, useRef, useEffect } from 'react'
import { Calendar, RotateCcw } from 'lucide-react'
import { useActivities } from '../contexts/ActivityContext'
import { useVirtualNow, setVirtualNow, getNow } from '../lib/clock'
import { localDateStr } from '../lib/compute'

const PRESETS = [
  { label: '7j', days: 7 },
  { label: '30j', days: 30 },
  { label: '90j', days: 90 },
  { label: '6m', days: 183 },
  { label: '1a', days: 365 },
  { label: '2a', days: 730 },
  { label: 'Tout', days: null },
]

function fmtDate(ts) {
  const d = new Date(ts)
  const months = ['jan', 'fev', 'mar', 'avr', 'mai', 'jun', 'jul', 'aou', 'sep', 'oct', 'nov', 'dec']
  return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`
}

// YYYY-MM-DD in local time, suitable for <input type="date">
const toInputDate = ts => localDateStr(new Date(ts))

function DualSlider({ min, max, valueFrom, valueTo, onChange }) {
  const trackRef = useRef(null)
  const [dragging, setDragging] = useState(null)

  const pctFrom = max > min ? ((valueFrom - min) / (max - min)) * 100 : 0
  const pctTo = max > min ? ((valueTo - min) / (max - min)) * 100 : 100

  const getValueFromEvent = useCallback((e) => {
    const track = trackRef.current
    if (!track) return valueFrom
    const rect = track.getBoundingClientRect()
    const clientX = e.touches ? e.touches[0].clientX : e.clientX
    const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    return Math.round(min + pct * (max - min))
  }, [min, max, valueFrom])

  const handleStart = useCallback((thumb) => (e) => {
    e.preventDefault()
    setDragging(thumb)
  }, [])

  useEffect(() => {
    if (!dragging) return
    const handleMove = (e) => {
      const val = getValueFromEvent(e)
      if (dragging === 'from') {
        onChange(Math.min(val, valueTo - 86400000), valueTo)
      } else {
        onChange(valueFrom, Math.max(val, valueFrom + 86400000))
      }
    }
    const handleEnd = () => setDragging(null)
    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', handleEnd)
    window.addEventListener('touchmove', handleMove)
    window.addEventListener('touchend', handleEnd)
    return () => {
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', handleEnd)
      window.removeEventListener('touchmove', handleMove)
      window.removeEventListener('touchend', handleEnd)
    }
  }, [dragging, valueFrom, valueTo, getValueFromEvent, onChange])

  return (
    <div className="relative h-8 flex items-center select-none date_range_dual_slider" data-name="date_range_dual_slider" ref={trackRef}>
      <div className="absolute inset-x-0 h-1.5 rounded-full bg-surface-border date_range_dual_slider_track" data-name="date_range_dual_slider_track" />
      <div
        className="absolute h-1.5 rounded-full bg-gradient-to-r from-primary to-primary-light transition-all duration-75 date_range_dual_slider_range"
        data-name="date_range_dual_slider_range"
        style={{ left: `${pctFrom}%`, right: `${100 - pctTo}%` }}
      />
      <div
        className={`absolute w-4 h-4 rounded-full border-2 border-primary bg-white cursor-grab -translate-x-1/2 transition-shadow date_range_dual_slider_thumb_from ${dragging === 'from' ? 'shadow-lg shadow-primary/30 scale-110' : 'hover:shadow-md hover:shadow-primary/20'}`}
        data-name="date_range_dual_slider_thumb_from"
        style={{ left: `${pctFrom}%` }}
        onMouseDown={handleStart('from')}
        onTouchStart={handleStart('from')}
      />
      <div
        className={`absolute w-4 h-4 rounded-full border-2 border-primary bg-white cursor-grab -translate-x-1/2 transition-shadow date_range_dual_slider_thumb_to ${dragging === 'to' ? 'shadow-lg shadow-primary/30 scale-110' : 'hover:shadow-md hover:shadow-primary/20'}`}
        data-name="date_range_dual_slider_thumb_to"
        style={{ left: `${pctTo}%` }}
        onMouseDown={handleStart('to')}
        onTouchStart={handleStart('to')}
      />
    </div>
  )
}

export default function DateRangeFilter() {
  const { activities, allActivities, dateRange, setDateRange, effectiveDateRange, now } = useActivities()
  const virtualNow = useVirtualNow()
  const [expanded, setExpanded] = useState(false)

  const bounds = useMemo(() => {
    if (!allActivities.length) return { min: now - 365 * 86400000, max: now }
    const dates = allActivities.map(a => new Date(a.start_date_local).getTime())
    return { min: Math.min(...dates), max: Math.max(...dates) }
  }, [allActivities, now])

  // The active preset is identified by `presetDays` on the dateRange object.
  // Slider drags clear `presetDays`, so the highlight follows user intent.
  const activePreset = useMemo(() => {
    if (!dateRange) return 'Tout'
    if (dateRange.presetDays != null) {
      return PRESETS.find(p => p.days === dateRange.presetDays)?.label ?? null
    }
    return null
  }, [dateRange])

  const handlePreset = useCallback((preset) => {
    if (!preset.days) {
      setDateRange(null)
    } else {
      // Store the preset *intent* — context resolves it against `now` so
      // the window slides automatically when the user changes the simulated date.
      setDateRange({ presetDays: preset.days })
    }
  }, [setDateRange])

  const handleSliderChange = useCallback((from, to) => {
    setDateRange({ from, to })
  }, [setDateRange])

  // ── Date simulation ──
  // We let the user pick any date as the dashboard's "today". Once set, all
  // rolling-window computations (90j cards, training load, records filter,
  // HR zones) anchor on this virtual now, so a 90-day rolling window can be
  // viewed at any past point in time.
  const handleSimulatedDateChange = useCallback((e) => {
    const value = e.target.value
    if (!value) {
      setVirtualNow(null)
      return
    }
    // Anchor at end of day in local TZ so the chosen day is "today" inclusive.
    const [y, m, d] = value.split('-').map(Number)
    const ts = new Date(y, m - 1, d, 23, 59, 59).getTime()
    setVirtualNow(ts)
  }, [])

  const resetSimulatedDate = useCallback(() => setVirtualNow(null), [])

  const rangeLabel = effectiveDateRange
    ? `${fmtDate(effectiveDateRange.from)} — ${fmtDate(effectiveDateRange.to)}`
    : 'Toutes les donnees'

  const filteredCount = activities.length

  if (!allActivities.length) return null

  return (
    <div className="border-b border-surface-border bg-white/80 backdrop-blur-sm date_range_filter" data-name="date_range_filter">
      <div className="max-w-screen-2xl mx-auto px-3 sm:px-6 date_range_filter_inner" data-name="date_range_filter_inner">
        <div className="flex items-center justify-between h-10 gap-2 date_range_filter_bar" data-name="date_range_filter_bar">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 sm:gap-2 text-xs text-txt-secondary hover:text-txt transition-colors min-w-0 date_range_filter_summary_button"
            data-name="date_range_filter_summary_button"
          >
            <Calendar size={13} className="flex-shrink-0 date_range_filter_summary_button_calendar" data-name="date_range_filter_summary_button_calendar" />
            <span className="font-medium truncate hidden sm:inline date_range_filter_range_label" data-name="date_range_filter_range_label">{rangeLabel}</span>
            <span className="font-medium sm:hidden date_range_filter_count_mobile" data-name="date_range_filter_count_mobile">{filteredCount} runs</span>
            <span className="text-surface-border hidden sm:inline date_range_filter_summary_button_text" data-name="date_range_filter_summary_button_text">|</span>
            <span className="text-txt-muted hidden sm:inline date_range_filter_count_desktop" data-name="date_range_filter_count_desktop">{filteredCount} runs</span>
          </button>

          <div className="flex items-center gap-2 date_range_filter_actions" data-name="date_range_filter_actions">
            {/* Date simulation control */}
            <div className="hidden md:flex items-center gap-1.5 date_range_filter_simulated_date_desktop" data-name="date_range_filter_simulated_date_desktop">
              <span className="text-[10px] text-txt-muted uppercase tracking-wider date_range_filter_simulated_date_label" data-name="date_range_filter_simulated_date_label">Date simulée</span>
              <input
                type="date"
                value={toInputDate(virtualNow ?? Date.now())}
                onChange={handleSimulatedDateChange}
                className={`text-xs rounded-md border px-2 py-1 transition-colors date_range_filter_simulated_date_input ${virtualNow ? 'border-primary/60 bg-primary/5 text-primary font-medium' : 'border-surface-border bg-white text-txt-secondary hover:border-primary/40'}`}
                data-name="date_range_filter_simulated_date_input"
                title="Simuler une date pour ramener tous les calculs (90j, charges, records…) à ce jour"
              />
              {virtualNow && (
                <button
                  onClick={resetSimulatedDate}
                  className="flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium border border-primary/40 text-primary bg-white hover:bg-primary hover:text-white transition-colors date_range_filter_simulated_date_reset"
                  data-name="date_range_filter_simulated_date_reset"
                  title="Revenir à aujourd'hui (date réelle)"
                >
                  <RotateCcw size={12} />
                  Réinitialiser
                </button>
              )}
            </div>

            {/* Presets */}
            <div className="flex items-center gap-1 date_range_filter_presets" data-name="date_range_filter_presets">
              {PRESETS.map(p => (
                <button
                  key={p.label}
                  onClick={() => handlePreset(p)}
                  className={`px-2 sm:px-2.5 py-1 rounded-md text-xs font-medium transition-all duration-200 date_range_filter_preset date_range_filter_preset_${p.label.toLowerCase()} ${activePreset === p.label ? 'bg-primary/10 text-primary' : 'text-txt-muted hover:text-txt-secondary hover:bg-surface-muted'}`}
                  data-name={`date_range_filter_preset_${p.label.toLowerCase()}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Mobile date-sim row */}
        {expanded && (
          <div className="md:hidden flex items-center gap-2 pb-2 date_range_filter_simulated_date_mobile" data-name="date_range_filter_simulated_date_mobile">
            <span className="text-[10px] text-txt-muted uppercase tracking-wider date_range_filter_simulated_date_mobile_date_simulee_meta" data-name="date_range_filter_simulated_date_mobile_date_simulee_meta">Date simulée</span>
            <input
              type="date"
              value={toInputDate(virtualNow ?? Date.now())}
              onChange={handleSimulatedDateChange}
              className={`text-xs rounded-md border px-2 py-1 date_range_filter_simulated_date_input_mobile ${virtualNow ? 'border-primary/60 bg-primary/5 text-primary font-medium' : 'border-surface-border bg-white text-txt-secondary'}`}
              data-name="date_range_filter_simulated_date_input_mobile"
            />
            {virtualNow && (
              <button
                onClick={resetSimulatedDate}
                className="flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium border border-primary/40 text-primary bg-white date_range_filter_simulated_date_reset_mobile"
                data-name="date_range_filter_simulated_date_reset_mobile"
              >
                <RotateCcw size={12} />
                Réinitialiser
              </button>
            )}
          </div>
        )}

        {expanded && (
          <div className="pb-3 pt-1 date_range_filter_slider_panel" data-name="date_range_filter_slider_panel">
            <DualSlider
              min={bounds.min}
              max={bounds.max}
              valueFrom={effectiveDateRange?.from ?? bounds.min}
              valueTo={effectiveDateRange?.to ?? now}
              onChange={handleSliderChange}
            />
            <div className="flex justify-between text-xs text-txt-muted mt-1 px-1 date_range_filter_slider_bounds" data-name="date_range_filter_slider_bounds">
              <span>{fmtDate(bounds.min)}</span>
              <span>{fmtDate(bounds.max)}</span>
            </div>
          </div>
        )}

        {/* Persistent badge when simulating a past date */}
        {virtualNow && (
          <div className="flex items-center gap-2 pb-2 -mt-1 text-[11px] text-primary date_range_filter_simulation_badge" data-name="date_range_filter_simulation_badge">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-primary animate-pulse date_range_filter_simulation_badge_text" data-name="date_range_filter_simulation_badge_text" />
            <span>Mode simulation — calculs ancrés au {fmtDate(virtualNow)}</span>
            <button
              onClick={resetSimulatedDate}
              className="underline decoration-dotted hover:no-underline hover:text-primary-light date_range_filter_simulation_badge_reset"
              data-name="date_range_filter_simulation_badge_reset"
            >
              Réinitialiser
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
