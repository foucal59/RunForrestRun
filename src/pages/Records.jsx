import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { Trophy, Medal, ExternalLink, ChevronDown, ChevronUp } from 'lucide-react'
import { useActivities } from '../contexts/ActivityContext'
import { fmtTime, paceForDist } from '../lib/compute'
import { COLORS } from '../lib/chartTheme'
import Loader from '../components/Loader'

function rankBadge(rank) {
  if (rank === 1) return '🥇'
  if (rank === 2) return '🥈'
  if (rank === 3) return '🥉'
  return `#${rank}`
}

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtDate(d) {
  if (!d) return ''
  return d.slice(0, 10)
}

// Dénivelé net de la fenêtre du record (m, positif = ça monte). null quand le
// run n'a pas de stream d'altitude — on n'affiche alors rien plutôt qu'un 0
// trompeur. Les fenêtres trop descendantes sont écartées côté serveur
// (database_pg.MAX_NET_DROP_PER_KM), elles n'arrivent jamais jusqu'ici.
function fmtElev(delta) {
  if (delta == null) return null
  const rounded = Math.round(delta)
  if (rounded === 0) return 'D± 0 m'
  return `${rounded > 0 ? 'D+' : 'D−'} ${Math.abs(rounded)} m`
}

function timeDiff(t, best) {
  if (!t || !best || t === best) return null
  const diff = t - best
  const s = diff % 60
  const m = Math.floor(diff / 60)
  return `+${m > 0 ? m + 'min ' : ''}${s}s`
}

// ── Personal Distance Records ──────────────────────────────────────────────────

const DIST_CONFIG = [
  { key: '5k',   label: '5 km',          km: 5,       color: COLORS.brand },
  { key: '10k',  label: '10 km',         km: 10,      color: '#3b82f6' },
  { key: 'semi', label: 'Semi-marathon', km: 21.0975, color: '#10b981' },
]

function PRCard({ config, bests }) {
  const [open, setOpen] = useState(false)
  const best = bests?.[0]
  const history = bests?.slice(0, 10) ?? []
  const cardName = `records_card_${config.key}`

  return (
    <div className={`card overflow-hidden ${cardName} records_card`} data-name={cardName}>
      {/* Header */}
      <div className="flex items-start justify-between mb-1 records_card_section" data-name="records_card_section">
        <div>
          <div className="text-xs text-txt-muted uppercase tracking-wider font-medium records_card_section_label" data-name="records_card_section_label">{config.label}</div>
          <div className="text-3xl font-mono font-bold text-txt mt-1 records_card_section_time_seconds_value" data-name="records_card_section_time_seconds_value">
            {best ? fmtTime(Math.round(best.timeSeconds)) : '–'}
          </div>
          {best && (
            <div className="flex items-center gap-2 mt-1 flex-wrap records_card_section_activity_id_section" data-name="records_card_section_activity_id_section">
              <span className="text-xs text-txt-muted records_card_section_activity_id_section_time_seconds_meta" data-name="records_card_section_activity_id_section_time_seconds_meta">{paceForDist(best.timeSeconds, config.key)}</span>
              <span className="text-xs text-txt-muted records_card_section_activity_id_section_meta" data-name="records_card_section_activity_id_section_meta">·</span>
              <span className="text-xs text-txt-muted records_card_section_activity_id_section_start_date_meta" data-name="records_card_section_activity_id_section_start_date_meta">{fmtDate(best.startDate)}</span>
              {fmtElev(best.elevationDelta) && (
                <>
                  <span className="text-xs text-txt-muted records_card_section_activity_id_section_meta" data-name="records_card_section_activity_id_section_meta">·</span>
                  <span className="text-xs text-txt-muted records_card_section_elevation_delta_meta" data-name="records_card_section_elevation_delta_meta" title="Dénivelé net sur la distance du record">
                    {fmtElev(best.elevationDelta)}
                  </span>
                </>
              )}
              {best.activityId && (
                <Link to={`/activity/${best.activityId}`} className="text-txt-muted hover:text-brand ouvrir_dans_l_app_action_link" data-name="ouvrir_dans_l_app_action_link" title="Ouvrir dans l'app">
                  <ExternalLink size={11} />
                </Link>
              )}
            </div>
          )}
        </div>
        <div className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 records_card_trophy_badge" data-name="records_card_trophy_badge"
          style={{ backgroundColor: config.color + '20' }}>
          <Trophy size={18} style={{ color: config.color }} />
        </div>
      </div>

      {/* Color bar */}
      <div className="h-0.5 rounded-full mt-3 mb-3 records_card_color_divider" data-name="records_card_color_divider" style={{ backgroundColor: config.color }} />

      {/* Top 3 */}
      {history.length > 1 && (
        <div className="space-y-1.5 records_card_list" data-name="records_card_list">
          {history.slice(0, open ? undefined : 3).map((b, i) => (
            <div key={b.activityId ?? i} className="flex items-center gap-2 text-xs records_card_list_i_activity_id_section" data-name="records_card_list_i_activity_id_section">
              <span className="w-5 text-center font-semibold text-txt-muted records_card_list_i_activity_id_section_i_meta" data-name="records_card_list_i_activity_id_section_i_meta">
                {rankBadge(i + 1)}
              </span>
              <span className="font-mono text-txt font-medium records_card_list_i_activity_id_section_time_seconds_value" data-name="records_card_list_i_activity_id_section_time_seconds_value">{fmtTime(Math.round(b.timeSeconds))}</span>
              <span className="text-txt-muted records_card_list_i_activity_id_section_time_seconds_meta" data-name="records_card_list_i_activity_id_section_time_seconds_meta">{paceForDist(b.timeSeconds, config.key)}</span>
              {i > 0 && (
                <span className="text-red-400 text-[10px] records_card_list_i_activity_id_section_time_seconds_text" data-name="records_card_list_i_activity_id_section_time_seconds_text">
                  {timeDiff(Math.round(b.timeSeconds), Math.round(history[0].timeSeconds))}
                </span>
              )}
              {fmtElev(b.elevationDelta) && (
                <span className="ml-auto text-txt-muted text-[10px] records_card_list_i_elevation_delta_meta" data-name="records_card_list_i_elevation_delta_meta" title="Dénivelé net sur la distance du record">
                  {fmtElev(b.elevationDelta)}
                </span>
              )}
              <span className={`${fmtElev(b.elevationDelta) ? '' : 'ml-auto '}text-txt-muted records_card_list_i_activity_id_section_start_date_meta`} data-name="records_card_list_i_activity_id_section_start_date_meta">{fmtDate(b.startDate)}</span>
              {b.activityId && (
                <Link to={`/activity/${b.activityId}`} className="text-txt-muted hover:text-brand app_action_link" data-name="app_action_link" title="App">
                  <ExternalLink size={10} />
                </Link>
              )}
            </div>
          ))}
          {history.length > 3 && (
            <button onClick={() => setOpen(o => !o)}
              className="flex items-center gap-1 text-[11px] text-txt-muted hover:text-txt mt-1 transition-colors records_card_list_open_moins_button" data-name="records_card_list_open_moins_button">
              {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              {open ? 'Moins' : `Voir les ${history.length - 3} autres`}
            </button>
          )}
        </div>
      )}

      {!best && (
        <div className="text-xs text-txt-muted text-center py-2 records_card_aucun_record_calcule_meta" data-name="records_card_aucun_record_calcule_meta">Aucun record calculé</div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Records() {
  const { computedPRs, loading: activitiesLoading } = useActivities()

  if (activitiesLoading) return <Loader />

  return (
    <div className="space-y-6 page_records" data-name="page_records">
      <div className="flex items-center justify-between gap-3 flex-wrap page_records_header" data-name="page_records_header">
        <h2 className="text-xl font-semibold text-txt records_header_title" data-name="records_header_title">Records</h2>
      </div>

      {/* ── Personal Distance Records ── */}
      <section className="records_section_distance" data-name="records_section_distance">
        <div className="flex items-center gap-2 mb-3 records_section_distance_header" data-name="records_section_distance_header">
          <Medal size={16} className="text-brand records_section_distance_header_medal" data-name="records_section_distance_header_medal" />
          <h3 className="text-sm font-semibold text-txt-secondary uppercase tracking-wide records_section_distance_header_meilleurs_temps_title" data-name="records_section_distance_header_meilleurs_temps_title">Meilleurs temps</h3>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 records_distance_grid" data-name="records_distance_grid">
          {DIST_CONFIG.map(cfg => (
            <PRCard
              key={cfg.key}
              config={cfg}
              bests={computedPRs[cfg.key] ?? []}
            />
          ))}
        </div>
        <p className="text-[11px] text-txt-muted mt-3 records_section_distance_footnote" data-name="records_section_distance_footnote">
          Les portions trop descendantes (plus de 5 m de perte d'altitude par km)
          sont écartées : un chrono aidé par la pente n'entre pas au palmarès.
        </p>
      </section>
    </div>
  )
}
