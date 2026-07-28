import React, { useState, useEffect, useRef, Suspense, lazy } from 'react'
import { Navigate, Routes, Route, useSearchParams } from 'react-router-dom'
import posthog from 'posthog-js'
import { clearTokens } from './api'
import { ActivityProvider } from './contexts/ActivityContext'
import Layout from './components/Layout'
import BrandLogo from './components/BrandLogo'
// Cockpit reste en import statique : c'est la page d'accueil, on veut qu'elle
// s'affiche sans round-trip réseau supplémentaire. Tout le reste est chargé à
// la demande — ça sort notamment recharts (lourd) du bundle initial, car seule
// une page à graphes le tire au moment où on la visite.
import Cockpit from './pages/Cockpit'
const Volume = lazy(() => import('./pages/Volume'))
const Performance = lazy(() => import('./pages/Performance'))
const Runs = lazy(() => import('./pages/Runs'))
const Training = lazy(() => import('./pages/Training'))
const PlanDetails = lazy(() => import('./pages/PlanDetails'))
const TrainingZones = lazy(() => import('./pages/TrainingZones'))
const ActivityDetail = lazy(() => import('./pages/ActivityDetail'))
const Progress = lazy(() => import('./pages/Progress'))
const Gear = lazy(() => import('./pages/Gear'))
const Records = lazy(() => import('./pages/Records'))
const Setup = lazy(() => import('./pages/Setup'))
const RunModal = lazy(() => import('./components/RunModal'))

const LOCAL_SESSION_HOSTS = new Set(['localhost', '127.0.0.1', '::1'])

// Fallback affiché le temps qu'un chunk de page chargé à la demande arrive.
function RouteFallback() {
  return (
    <div className="flex items-center justify-center py-20 route_fallback" data-name="route_fallback">
      <div className="w-8 h-8 border-2 border-brand/30 border-t-brand rounded-full animate-spin" />
    </div>
  )
}

function canUseLocalSessionRestore() {
  return LOCAL_SESSION_HOSTS.has(window.location.hostname)
}

function Login() {
  const [email, setEmail] = React.useState('')
  const [password, setPassword] = React.useState('')
  const [mfaCode, setMfaCode] = React.useState('')
  const [mfaRequired, setMfaRequired] = React.useState(false)
  const [error, setError] = React.useState('')
  const [loading, setLoading] = React.useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const resp = await fetch('/api/auth/garmin-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email, password, mfa_code: mfaCode }),
      })
      if (resp.ok) {
        const data = await resp.json().catch(() => ({}))
        const athleteId = data.athlete?.id
        if (athleteId) posthog.identify(String(athleteId))
        window.location.reload()
      } else {
        const data = await resp.json().catch(() => ({}))
        if (resp.status === 409) {
          setMfaRequired(true)
          posthog.capture('garmin_mfa_required')
        }
        posthog.capture('login_failed', { status: resp.status })
        setError(resp.status >= 500 ? 'Le serveur est indisponible' : data.detail || 'Connexion échouée')
      }
    } catch {
      posthog.capture('login_failed', { status: 'network_error' })
      setError('Erreur réseau')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center app_login" data-name="app_login">
      <div className="w-full max-w-sm px-6 app_login_card" data-name="app_login_card">
        <h1 className="text-3xl font-bold mb-2 tracking-tight text-center app_login_title" data-name="app_login_title">
          <BrandLogo />
        </h1>
        <p className="text-txt-muted mb-8 text-center app_login_subtitle" data-name="app_login_subtitle">
          Connectez votre compte Garmin
        </p>
        <form onSubmit={handleSubmit} className="space-y-4 app_login_form" data-name="app_login_form">
          <input
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="Email Garmin Connect"
            required
            autoComplete="username"
            className="w-full px-4 py-3 rounded-lg border border-border bg-card text-txt placeholder:text-txt-muted focus:outline-none focus:ring-2 focus:ring-accent app_login_input_email"
            data-name="app_login_input_email"
          />
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="Mot de passe"
            required
            autoComplete="current-password"
            className="w-full px-4 py-3 rounded-lg border border-border bg-card text-txt placeholder:text-txt-muted focus:outline-none focus:ring-2 focus:ring-accent app_login_input_password"
            data-name="app_login_input_password"
          />
          <input
            type="text"
            value={mfaCode}
            onChange={e => setMfaCode(e.target.value)}
            placeholder={mfaRequired ? 'Code MFA Garmin' : 'Code MFA (optionnel)'}
            autoComplete="one-time-code"
            className="w-full px-4 py-3 rounded-lg border border-border bg-card text-txt placeholder:text-txt-muted focus:outline-none focus:ring-2 focus:ring-accent app_login_input_mfa"
            data-name="app_login_input_mfa"
          />
          {mfaRequired && (
            <p className="text-amber-600 text-sm app_login_mfa_notice" data-name="app_login_mfa_notice">
              Garmin demande un code MFA pour terminer la connexion.
            </p>
          )}
          {error && <p className="text-red-500 text-sm app_login_error" data-name="app_login_error">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full px-6 py-3 bg-accent hover:bg-accent-light text-white font-medium rounded-lg transition-colors disabled:opacity-50 app_login_button"
            data-name="app_login_button"
          >
            {loading ? 'Connexion…' : 'Se connecter avec Garmin'}
          </button>
        </form>
      </div>
    </div>
  )
}

function BackendUnavailable({ message, onRetry }) {
  return (
    <div className="min-h-screen flex items-center justify-center px-6 app_backend_unavailable" data-name="app_backend_unavailable">
      <div className="card w-full max-w-md text-center">
        <h1 className="text-lg font-semibold text-txt mb-2">Serveur indisponible</h1>
        <p className="text-sm text-txt-secondary mb-5">{message}</p>
        <button
          type="button"
          onClick={onRetry}
          className="px-5 py-2.5 bg-accent hover:bg-accent-light text-white font-medium rounded-lg transition-colors"
        >
          Réessayer
        </button>
      </div>
    </div>
  )
}

export default function App() {
  const [authed, setAuthed] = useState(false)
  const [athlete, setAthlete] = useState(null)
  const [loading, setLoading] = useState(true)
  const [configured, setConfigured] = useState(true)
  const [bootstrapError, setBootstrapError] = useState('')
  const [bootstrapAttempt, setBootstrapAttempt] = useState(0)
  const bootstrapStartedRef = useRef(false)

  useEffect(() => {
    if (bootstrapStartedRef.current) return
    bootstrapStartedRef.current = true
    let cancelled = false

    // Defensive fetch wrapper: browser/extension/SW weirdness can leave a
    // fetch hanging forever, which locks the bootstrap at the "Chargement..."
    // spinner with no clue in the console. Aborting after 15s at least lets
    // the catch run and logs the failure.
    async function fetchWithTimeout(url, options = {}, ms = 15000) {
      const ctrl = new AbortController()
      const timer = setTimeout(() => {
        console.warn(`[App] fetch timeout after ${ms}ms:`, url)
        ctrl.abort()
      }, ms)
      try {
        return await fetch(url, { ...options, signal: ctrl.signal })
      } finally {
        clearTimeout(timer)
      }
    }

    async function loadMe() {
      setLoading(true)
      setBootstrapError('')
      try {
        // Fetch setup status and auth in parallel — saves one round-trip
        console.log('[App] bootstrap start — fetching setup + auth in parallel')
        const [setupResp, authResp] = await Promise.all([
          fetchWithTimeout('/api/setup/status'),
          fetchWithTimeout('/api/auth/me', { credentials: 'include' }),
        ])

        if (setupResp.status >= 500 || authResp.status >= 500) {
          throw new Error('backend_unavailable')
        }

        console.log('[App] /api/setup/status →', setupResp.status, setupResp.ok)
        if (!cancelled && setupResp.ok) {
          const setupData = await setupResp.json()
          console.log('[App] setup configured:', setupData.configured)
          if (!setupData.configured) {
            setConfigured(false)
            setLoading(false)
            return
          }
        }

        console.log('[App] /api/auth/me →', authResp.status, authResp.ok)
        if (authResp.status === 401) {
          if (canUseLocalSessionRestore()) {
            const localSessionResp = await fetchWithTimeout('/api/auth/local-session', {
              method: 'POST',
              credentials: 'include',
            })
            if (localSessionResp.ok) {
              const localData = await localSessionResp.json()
              if (!cancelled && localData.athlete) {
                setAuthed(true)
                setAthlete({
                  id: localData.athlete.id,
                  firstname: localData.athlete.firstname,
                  lastname: localData.athlete.lastname,
                  profile_pic: localData.athlete.profile,
                  shoes: localData.athlete.shoes || [],
                })
              }
              return
            }
          }
          if (!cancelled) {
            setAuthed(false)
            setAthlete(null)
          }
          return
        }
        if (!authResp.ok) throw new Error(`auth/me ${authResp.status}`)
        const data = await authResp.json()
        console.log('[App] /api/auth/me body:', { authenticated: data.authenticated })
        if (!cancelled && data.authenticated) {
          setAuthed(true)
          setAthlete({
            id: data.athlete.id,
            firstname: data.athlete.firstname,
            lastname: data.athlete.lastname,
            profile_pic: data.athlete.profile,
            shoes: data.athlete.shoes || [],
          })
        } else if (!cancelled) {
          setAuthed(false)
          setAthlete(null)
        }
      } catch (e) {
        console.error('[App] bootstrap error:', e?.message || e)
        if (!cancelled) {
          setAuthed(false)
          setAthlete(null)
          setBootstrapError(
            e?.message === 'backend_unavailable'
              ? 'Le serveur ne répond pas. Réessayez dans quelques secondes.'
              : 'Impossible de joindre le serveur. Vérifiez la connexion puis réessayez.'
          )
        }
      } finally {
        if (!cancelled) {
          console.log('[App] bootstrap done, loading=false')
          setLoading(false)
        }
      }
    }
    loadMe()
    return () => { cancelled = true }
  }, [bootstrapAttempt])

  const retryBootstrap = () => {
    bootstrapStartedRef.current = false
    setBootstrapAttempt(attempt => attempt + 1)
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center app_loading" data-name="app_loading">
        <div className="text-txt-muted app_loading_text" data-name="app_loading_text">Chargement...</div>
      </div>
    )
  }

  if (bootstrapError) {
    return <BackendUnavailable message={bootstrapError} onRetry={retryBootstrap} />
  }

  if (!configured) {
    return (
      <Suspense fallback={<RouteFallback />}>
        <Setup onConfigured={() => { setConfigured(true) }} />
      </Suspense>
    )
  }

  if (!authed) return <Login />

  const handleLogout = async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
    } catch {}
    posthog.reset()
    clearTokens()
    setAuthed(false)
    setAthlete(null)
  }

  return (
    <ActivityProvider initialShoes={athlete?.shoes || []}>
      <DashboardGate athlete={athlete} onLogout={handleLogout} />
    </ActivityProvider>
  )
}

function DashboardGate({ athlete, onLogout }) {
  // RunModal tire recharts + RunMap : on ne le monte (et donc ne charge son
  // chunk) que quand un run est ouvert via `?run=…`, pas au chargement du
  // dashboard. Il se referme en retirant le param, ce qui le démonte.
  const [searchParams] = useSearchParams()
  const runOpen = searchParams.has('run')
  return (
    <Layout athlete={athlete} onLogout={onLogout}>
      <Suspense fallback={<RouteFallback />}>
        <Routes>
          <Route path="/" element={<Cockpit />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/volume" element={<Volume />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/analysis" element={<Navigate to="/progress" replace />} />
          <Route path="/training" element={<Training />} />
          <Route path="/plan" element={<PlanDetails />} />
          <Route path="/training-zones" element={<TrainingZones />} />
          <Route path="/activity/:id" element={<ActivityDetail />} />
          <Route path="/progress" element={<Progress />} />
          <Route path="/gear" element={<Gear />} />
          <Route path="/analyse" element={<Navigate to="/progress" replace />} />
          <Route path="/records" element={<Records />} />
          <Route path="/vo2max" element={<Navigate to="/progress" replace />} />
        </Routes>
      </Suspense>
      {runOpen && (
        <Suspense fallback={null}>
          <RunModal />
        </Suspense>
      )}
    </Layout>
  )
}
