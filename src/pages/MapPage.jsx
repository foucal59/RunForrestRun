import React, { useMemo } from 'react'
import { useActivities } from '../contexts/ActivityContext'
import RunMap from '../components/RunMap'
import Loader from '../components/Loader'
import { toMapRun } from '../lib/runMaps'

export default function MapPage() {
  const { activities, loading } = useActivities()

  const mapRuns = useMemo(() => {
    console.log('[MapPage] preparing map runs from', activities.length, 'activities')
    return activities
      .map(a => toMapRun(a, activities))
      .filter(Boolean)
      .sort((a, b) => b.date.localeCompare(a.date))
  }, [activities])

  if (loading) return <Loader />

  return (
    <div data-name="page_map">
      <h2 className="text-xl font-semibold mb-6 map_header" data-name="map_header">Carte</h2>
      <div className="card p-0 overflow-hidden map_canvas" data-name="map_canvas">
        <RunMap runs={mapRuns} height={600} />
      </div>
      <div className="mt-3 text-xs text-txt-muted text-right map_footer_count" data-name="map_footer_count">
        {mapRuns.length} sortie{mapRuns.length > 1 ? 's' : ''} avec trace GPS
      </div>
    </div>
  )
}
