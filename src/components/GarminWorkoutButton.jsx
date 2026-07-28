import React, { useState } from 'react'
import { AlertCircle, Check, Loader2, Send } from 'lucide-react'
import posthog from 'posthog-js'
import { sendWorkoutToGarmin } from '../api'

export default function GarminWorkoutButton({
  date,
  workout = null,
  compact = false,
  label = 'Garmin',
  prominent = false,
  className = '',
  dataName = 'garmin_workout_button',
}) {
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')

  if (!date) return null

  const sending = status === 'sending'
  const sent = status === 'sent'
  const hasError = status === 'error'
  const disabled = sending || sent
  const Icon = sending ? Loader2 : sent ? Check : hasError ? AlertCircle : Send
  const text = sending
    ? 'Envoi...'
    : sent
      ? 'Envoye'
      : hasError
        ? compact ? 'Erreur' : 'Reessayer'
        : label
  const tone = sent
    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400'
    : hasError
      ? 'bg-red-100 text-red-700 hover:bg-red-200 dark:bg-red-500/15 dark:text-red-400'
      : prominent
        ? 'bg-primary text-white hover:bg-primary-dark shadow-sm'
        : 'bg-accent/10 text-accent hover:bg-accent/20'
  const sizing = compact ? 'px-2 py-1 text-[11px]' : 'px-2.5 py-1.5 text-xs'

  async function handleClick() {
    if (disabled) return
    setStatus('sending')
    setError('')
    try {
      const result = await sendWorkoutToGarmin(date, workout)
      setStatus('sent')
      posthog.capture('workout_garmin_upload_clicked', {
        date,
        ok: true,
        workout_id: result?.workoutId,
      })
    } catch (e) {
      const message = e?.message || 'Upload Garmin impossible'
      setError(message)
      setStatus('error')
      posthog.capture('workout_garmin_upload_clicked', {
        date,
        ok: false,
        error: message,
      })
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-default disabled:opacity-80 whitespace-nowrap ${tone} ${sizing} ${className}`}
      data-name={dataName}
      title={error || (sent ? 'Seance creee dans Garmin Connect' : 'Envoyer vers Garmin Connect')}
      aria-live="polite"
    >
      <Icon size={compact ? 12 : 13} className={sending ? 'animate-spin' : ''} />
      {text}
    </button>
  )
}
