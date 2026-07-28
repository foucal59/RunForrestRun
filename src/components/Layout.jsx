import React, { useState, useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import { Activity, BarChart3, CalendarDays, Trophy, TrendingUp, LogOut, RefreshCw, Trash2, Footprints, Sun, Moon, AlertTriangle } from 'lucide-react'
import { useActivities } from '../contexts/ActivityContext'
import { clearAllCache } from '../api'
import DateRangeFilter from './DateRangeFilter'
import BrandLogo from './BrandLogo'

const navItems = [
  { to: '/', icon: Activity, label: 'Cockpit' },
  { to: '/plan', icon: CalendarDays, label: 'Plan' },
  { to: '/volume', icon: BarChart3, label: 'Volume' },
  { to: '/progress', icon: TrendingUp, label: 'Progression', shortLabel: 'Progrès' },
  { to: '/performance', icon: Trophy, label: 'Records' },
  { to: '/gear', icon: Footprints, label: 'Matériel' },
]
console.log('[Layout] navItems count:', navItems.length)

function formatAgo(date) {
  const diff = Date.now() - date.getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "a l'instant"
  if (mins < 60) return `il y a ${mins}min`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `il y a ${hours}h`
  return `il y a ${Math.floor(hours / 24)}j`
}

function formatRate(rate) {
  if (!rate) return null
  const endpoint = rate.endpoint ? `${rate.endpoint} ` : ''
  const retry = rate.retry ? ` (retry after ${rate.retry}s)` : ''
  const raw = rate.raw || `${rate.usage || ''}/${rate.limit || ''}`
  const [usagePart = '', limitPart = ''] = raw.split('/')
  const [u15, uDay] = usagePart.split(',')
  const [l15, lDay] = limitPart.split(',')
  if (u15 && l15 && uDay && lDay) {
    const u15n = parseInt(u15, 10)
    const l15n = parseInt(l15, 10)
    const uDayn = parseInt(uDay, 10)
    const lDayn = parseInt(lDay, 10)

    if (!Number.isNaN(u15n) && !Number.isNaN(l15n) && u15n >= l15n) {
      return `${endpoint}Limite 15min atteinte (${u15}/${l15})${retry}`
    }
    if (!Number.isNaN(uDayn) && !Number.isNaN(lDayn) && uDayn >= lDayn) {
      return `${endpoint}Limite journalière atteinte (${uDay}/${lDay})${retry}`
    }

    return `${endpoint}rate: ${u15}/${l15} (15m) ${uDay}/${lDay} (daily)${retry}`
  }
  return `${endpoint}rate: ${raw}${retry}`
}

function useDarkMode() {
  const [dark, setDark] = useState(() => {
    const stored = localStorage.getItem('darkMode')
    if (stored !== null) return stored === 'true'
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  useEffect(() => {
    if (dark) {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    localStorage.setItem('darkMode', String(dark))
    console.log('[Layout] dark mode:', dark)
  }, [dark])

  return [dark, setDark]
}

export default function Layout({ children, athlete, onLogout }) {
  const { syncing, refresh, allActivities, backfillStatus, loadingMore, syncWarning } = useActivities()
  const [dark, setDark] = useDarkMode()

  function handleClearCache() {
    if (!window.confirm('Vider tout le cache ? Les données seront re-téléchargées depuis Garmin.')) return
    clearAllCache()
    window.location.reload()
  }

  const bs = backfillStatus
  // Missing fields mean the lightweight status endpoint returned its safe
  // fallback, not that a historical backfill is actually pending.
  const listDone = bs?.listComplete !== false
  const activityCount = Number(bs?.activityCount) || 0
  const detailsCount = Number(bs?.detailsCount) || 0
  const rawDetailsRemaining = Math.max(0, Number(bs?.detailsRemaining) || 0)
  const detailsFetchedForAllRuns = activityCount > 0 && detailsCount >= activityCount
  const detailsDone = bs?.detailsComplete !== false || detailsFetchedForAllRuns
  const detailsRemaining = detailsDone ? 0 : rawDetailsRemaining
  const allDone = listDone && detailsDone
  const countLabel = loadingMore
    ? `${allActivities.length} runs…`
    : `${allActivities.length} runs${allDone ? '' : ' (partiel)'}`

  return (
    <div className="min-h-screen flex flex-col layout_root" data-name="layout_root">
      {/* ── Desktop header (hidden on mobile) ── */}
      <header className="hidden lg:block border-b border-surface-border bg-white/80 backdrop-blur-sm sticky top-0 z-50 layout_header_desktop" data-name="layout_header_desktop">
        <div className="max-w-screen-2xl mx-auto px-6 h-14 flex items-center justify-between layout_header_desktop_inner" data-name="layout_header_desktop_inner">
          <div className="flex items-center gap-8 layout_header_desktop_brand_nav" data-name="layout_header_desktop_brand_nav">
            <h1 className="text-lg font-bold tracking-tight text-txt layout_brand_desktop" data-name="layout_brand_desktop">
              <BrandLogo />
            </h1>
            <nav className="flex items-center gap-1 layout_nav_desktop" data-name="layout_nav_desktop">
              {navItems.map(({ to, icon: Icon, label }) => (
                <NavLink key={to} to={to} end={to === '/'}
                  className={({ isActive }) => `nav-link flex items-center gap-2 layout_nav_desktop_item layout_nav_desktop_item_${label.toLowerCase()} ${isActive ? 'active' : ''}`}
                  data-name={`layout_nav_desktop_item_${label.toLowerCase()}`}>
                  <Icon size={16} />{label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-4 layout_header_desktop_actions" data-name="layout_header_desktop_actions">
            <button
              onClick={() => setDark(d => !d)}
              className="flex items-center px-2 py-1.5 rounded-lg text-xs text-txt-muted hover:text-txt bg-surface-muted hover:bg-surface-hover transition-colors layout_dark_mode_button_desktop"
              data-name="layout_dark_mode_button_desktop"
              title={dark ? 'Passer en mode clair' : 'Passer en mode sombre'}
            >
              {dark ? <Sun size={14} /> : <Moon size={14} />}
            </button>
            <button onClick={refresh} disabled={syncing}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all duration-200 layout_refresh_button_desktop ${syncing ? 'bg-brand/10 text-brand' : allDone ? 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100' : 'bg-surface-muted text-txt-secondary hover:text-txt hover:bg-surface-hover'}`}
              data-name="layout_refresh_button_desktop"
              title="Forcer la synchronisation">
              <RefreshCw
                size={14}
                className={`layout_refresh_button_desktop_icon ${syncing ? 'animate-spin' : ''}`.trim()}
                data-name="layout_refresh_button_desktop_icon"
              />
              <span>{syncing ? 'Sync...' : countLabel}</span>
            </button>
            <button onClick={handleClearCache}
              className="flex items-center px-2 py-1.5 rounded-lg text-xs text-txt-muted hover:text-red-500 bg-surface-muted hover:bg-red-50 transition-colors layout_clear_cache_button"
              data-name="layout_clear_cache_button"
              title="Vider le cache et re-synchroniser">
              <Trash2 size={14} />
            </button>
            {athlete && (
              <div className="flex items-center gap-2 layout_athlete_desktop" data-name="layout_athlete_desktop">
                {athlete.profile_pic && <img src={athlete.profile_pic} alt="" className="w-7 h-7 rounded-full layout_athlete_desktop_avatar" data-name="layout_athlete_desktop_avatar" />}
                <span className="text-sm text-txt-secondary layout_athlete_desktop_name" data-name="layout_athlete_desktop_name">{athlete.firstname}</span>
              </div>
            )}
            {onLogout && (
              <button onClick={onLogout}
                className="flex items-center px-3 py-1.5 rounded-lg text-sm text-txt-muted hover:text-txt bg-surface-muted hover:bg-surface-hover transition-colors layout_logout_button"
                data-name="layout_logout_button">
                <LogOut size={14} />
              </button>
            )}
          </div>
        </div>
        {syncing && (
          <div className="h-0.5 bg-surface-muted layout_sync_progress_desktop" data-name="layout_sync_progress_desktop">
            <div className="h-full bg-brand animate-pulse rounded-full layout_sync_progress_bar" data-name="layout_sync_progress_bar" style={{ width: '100%' }} />
          </div>
        )}
        {bs && !allDone && !syncing && (
          <div className="h-5 bg-amber-50 flex items-center justify-center text-[10px] text-amber-700 layout_backfill_banner_desktop" data-name="layout_backfill_banner_desktop">
            {bs.rateLimited && <>Garmin rate limit{bs.rate?.endpoint ? ` (${bs.rate.endpoint})` : ''} — nouvelle tentative dans 15 min</>}
            {!bs.rateLimited && !bs.listComplete && bs.maxed && 'Limite Garmin atteinte'}
            {!bs.rateLimited && !bs.listComplete && !bs.maxed && allActivities.length > 0 &&
              `${bs.activityCount || allActivities.length} courses — les plus anciennes seront ajoutées toutes les 15 min`}
            {bs.listComplete && detailsRemaining > 0 && `Détails : ${detailsRemaining} restants`}
            {bs.rate && <span className="ml-2 layout_backfill_banner_desktop_rate_text" data-name="layout_backfill_banner_desktop_rate_text">{formatRate(bs.rate)}</span>}
          </div>
        )}
      </header>

      {/* ── Mobile top safe-area + status bar (iOS only) ── */}
      <div className="lg:hidden safe-top bg-surface layout_safe_top_mobile" data-name="layout_safe_top_mobile" />

      {/* ── Mobile top bar ── */}
      <header className="lg:hidden sticky top-[env(safe-area-inset-top,0px)] z-50 bg-white/85 backdrop-blur-xl border-b border-surface-border layout_header_mobile" data-name="layout_header_mobile">
        <div className="h-11 px-4 flex items-center justify-between layout_header_mobile_inner" data-name="layout_header_mobile_inner">
          <h1 className="text-[17px] font-bold tracking-tight text-txt layout_brand_mobile" data-name="layout_brand_mobile">
            <BrandLogo compact />
          </h1>
          <div className="flex items-center gap-2 layout_header_mobile_actions" data-name="layout_header_mobile_actions">
            <button
              onClick={() => setDark(d => !d)}
              className="w-10 h-10 flex items-center justify-center rounded-full text-txt-muted bg-surface-muted ios-press layout_dark_mode_button_mobile"
              data-name="layout_dark_mode_button_mobile"
            >
              {dark ? <Sun size={15} /> : <Moon size={15} />}
            </button>
            <button onClick={refresh} disabled={syncing}
              className={`w-10 h-10 flex items-center justify-center rounded-full ios-press layout_refresh_button_mobile ${syncing ? 'text-brand bg-brand/10' : allDone ? 'text-emerald-600 bg-emerald-50' : 'text-txt-muted bg-surface-muted'}`}
              data-name="layout_refresh_button_mobile"
              title="Synchroniser">
              <RefreshCw
                size={15}
                className={`layout_refresh_button_mobile_icon ${syncing ? 'animate-spin' : ''}`.trim()}
                data-name="layout_refresh_button_mobile_icon"
              />
            </button>
            {athlete?.profile_pic && (
              <img src={athlete.profile_pic} alt="" className="w-7 h-7 rounded-full layout_athlete_mobile_avatar" data-name="layout_athlete_mobile_avatar" />
            )}
          </div>
        </div>
        {syncing && (
          <div className="h-0.5 bg-surface-muted layout_sync_progress_mobile" data-name="layout_sync_progress_mobile">
            <div className="h-full bg-brand animate-pulse rounded-full layout_sync_progress_bar_mobile" data-name="layout_sync_progress_bar_mobile" style={{ width: '100%' }} />
          </div>
        )}
        {bs && !allDone && !syncing && (
          <div className="h-5 bg-amber-50 flex items-center justify-center text-[10px] text-amber-700 px-3 layout_backfill_banner_mobile" data-name="layout_backfill_banner_mobile">
            {bs.rateLimited && 'Rate limit — retry dans 15 min'}
            {!bs.rateLimited && !bs.listComplete && !bs.maxed && allActivities.length > 0 &&
              `Chargement des anciennes courses…`}
            {bs.listComplete && detailsRemaining > 0 && `${detailsRemaining} détails restants`}
          </div>
        )}
      </header>

      <DateRangeFilter />

      {syncWarning && (
        <div className="border-b border-amber-200 bg-amber-50 layout_sync_warning" data-name="layout_sync_warning">
          <div className="max-w-screen-2xl mx-auto px-3 sm:px-6 py-2 flex flex-col sm:flex-row sm:items-center gap-2 text-sm text-amber-800 layout_sync_warning_inner" data-name="layout_sync_warning_inner">
            <div className="flex items-center gap-2 min-w-0 layout_sync_warning_message" data-name="layout_sync_warning_message">
              <AlertTriangle size={16} className="flex-shrink-0 layout_sync_warning_icon" data-name="layout_sync_warning_icon" />
              <span>{syncWarning}</span>
            </div>
            {onLogout && (
              <button
                type="button"
                onClick={onLogout}
                className="sm:ml-auto inline-flex items-center justify-center px-3 py-1.5 rounded-lg bg-amber-100 hover:bg-amber-200 text-amber-900 font-medium transition-colors layout_sync_warning_reconnect"
                data-name="layout_sync_warning_reconnect"
              >
                Reconnecter Garmin
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── iOS bottom tab bar ── */}
      <nav className="lg:hidden fixed bottom-0 left-0 right-0 z-50 bg-white/85 dark:bg-opacity-90 backdrop-blur-xl border-t border-surface-border safe-bottom layout_nav_mobile" data-name="layout_nav_mobile">
        <div className="flex items-stretch layout_nav_mobile_inner" data-name="layout_nav_mobile_inner">
          {navItems.map(({ to, icon: Icon, label, shortLabel }) => (
            <NavLink key={to} to={to} end={to === '/'}
              className={({ isActive }) =>
                `flex flex-col items-center justify-center flex-1 min-w-0 pt-2.5 pb-2 gap-1 ios-press layout_nav_mobile_item layout_nav_mobile_item_${label.toLowerCase()} ${
                  isActive ? 'text-brand' : 'text-txt-muted'
                }`
              }
              data-name={`layout_nav_mobile_item_${label.toLowerCase()}`}>
              {({ isActive }) => (
                <>
                  <Icon size={22} strokeWidth={isActive ? 2.2 : 1.6} />
                  <span className={`text-[11px] leading-tight max-w-full truncate ${isActive ? 'font-semibold' : 'font-normal'} layout_nav_mobile_item_label_text`} data-name="layout_nav_mobile_item_label_text">
                    {shortLabel || label}
                  </span>
                </>
              )}
            </NavLink>
          ))}
        </div>
      </nav>

      <main className="flex-1 max-w-screen-2xl mx-auto w-full px-3 sm:px-6 py-4 sm:py-6 pb-24 lg:pb-6 layout_main" data-name="layout_main">
        {children}
      </main>

      <footer className="hidden lg:block border-t border-surface-border py-3 px-6 layout_footer" data-name="layout_footer">
        <div className="max-w-screen-2xl mx-auto flex items-center justify-center gap-1.5 text-xs text-txt-muted layout_footer_inner" data-name="layout_footer_inner">
          <span>Powered by</span>
          <span className="text-brand font-semibold layout_footer_garmin_link" data-name="layout_footer_garmin_link">
            Garmin Connect
          </span>
        </div>
      </footer>
    </div>
  )
}
