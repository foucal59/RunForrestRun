import React, { useState } from 'react'
import BrandLogo from '../components/BrandLogo'

export default function Setup({ onConfigured }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    console.log('[Setup] submitting credentials')
    try {
      const resp = await fetch('/api/setup/configure', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        throw new Error(data.detail || 'Erreur lors de la sauvegarde')
      }
      console.log('[Setup] local configuration initialized — redirecting to login')
      onConfigured()
    } catch (err) {
      console.log('[Setup] error:', err.message)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4 page_setup" data-name="page_setup">
      <div className="w-full max-w-md setup_container" data-name="setup_container">
        <div className="text-center mb-8 setup_header" data-name="setup_header">
          <h1 className="text-3xl font-bold tracking-tight mb-2 setup_brand_title" data-name="setup_brand_title">
            <BrandLogo />
          </h1>
          <p className="text-txt-muted setup_header_subtitle" data-name="setup_header_subtitle">Configuration initiale</p>
        </div>

        <div className="bg-surface border border-border rounded-xl p-6 mb-4 setup_step_garmin_auth" data-name="setup_step_garmin_auth">
          <h2 className="font-medium mb-1 setup_step_title" data-name="setup_step_title">Préparer la connexion Garmin</h2>
          <p className="text-sm text-txt-muted mb-3 setup_step_description" data-name="setup_step_description">
            L'application lit vos activités depuis Garmin Connect et la base configurée côté serveur.
          </p>
          <p className="text-sm text-txt-muted setup_step_hint" data-name="setup_step_hint">
            Si la base n'est pas encore reliée, renseignez `DATABASE_URL` dans l'environnement local ou Vercel, puis relancez le serveur.
          </p>
          <p className="text-sm text-txt-muted mt-3 setup_step_link_text" data-name="setup_step_link_text">
            Accès Garmin :{' '}
            <a
              href="https://connect.garmin.com"
              target="_blank"
              rel="noopener noreferrer"
              className="text-brand hover:underline setup_garmin_link"
              data-name="setup_garmin_link"
            >
              connect.garmin.com
            </a>
          </p>
        </div>

        <form onSubmit={handleSubmit} className="bg-surface border border-border rounded-xl p-6 space-y-4 setup_form" data-name="setup_form">
          {error && (
            <p className="text-red-400 text-sm setup_error" data-name="setup_error">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-brand hover:bg-accent-light disabled:opacity-50 disabled:cursor-not-allowed text-white font-medium rounded-lg px-4 py-2.5 transition-colors text-sm setup_button_submit"
            data-name="setup_button_submit"
          >
            {loading ? 'Initialisation…' : 'Initialiser et continuer'}
          </button>
        </form>
      </div>
    </div>
  )
}
