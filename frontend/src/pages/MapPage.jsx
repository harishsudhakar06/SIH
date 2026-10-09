import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import MapView from '../components/MapView.jsx'
import MapPanel from '../components/MapPanel.jsx'
import { api } from '../api.js'
import { usePoll } from '../hooks.js'

export default function MapPage() {
  const navigate = useNavigate()
  const mapRef = useRef(null)
  const { data: slopeData, error } = usePoll('/map/slopes', { interval: 10000 })
  const { data: reportData } = usePoll('/map/reports', { interval: 10000 })
  const { data: alertData } = usePoll('/map/alerts', { interval: 6000 })
  const { data: graph } = usePoll('/graph', { interval: 15000 })
  const { data: incidentData } = usePoll('/incidents', { interval: 10000 })

  const [search, setSearch] = useState('')
  const [band, setBand] = useState('ALL')
  const [stateName, setStateName] = useState('ALL')
  const [district, setDistrict] = useState('ALL')
  const [alertStatus, setAlertStatus] = useState('ALL')
  const [showEdges, setShowEdges] = useState(true)
  const [showReports, setShowReports] = useState(true)
  const [showAlerts, setShowAlerts] = useState(true)
  const [showIncidents, setShowIncidents] = useState(true)
  const [sel, setSel] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(null)

  const slopes = slopeData?.slopes || []
  const states = useMemo(() => [...new Set(slopes.map((s) => s.state).filter(Boolean))].sort(), [slopes])
  const districts = useMemo(
    () => [...new Set(slopes.filter((s) => stateName === 'ALL' || s.state === stateName).map((s) => s.district).filter(Boolean))].sort(),
    [slopes, stateName],
  )

  const nodes = useMemo(() => slopes.filter((s) => {
    if (band !== 'ALL' && (s.risk_band || 'UNASSESSED') !== band) return false
    if (stateName !== 'ALL' && s.state !== stateName) return false
    if (district !== 'ALL' && s.district !== district) return false
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      const hay = `${s.code} ${s.name} ${s.district} ${s.state}`.toLowerCase()
      if (!hay.includes(q)) return false
    }
    return true
  }), [slopes, band, stateName, district, search])

  const reports = (reportData?.reports || []).filter((r) => r.lat != null && r.lng != null)
  const alerts = (alertData?.alerts || []).filter((a) => alertStatus === 'ALL' || a.status === alertStatus)
  const edges = graph?.edges || []
  const incidents = incidentData?.incidents || []

  useEffect(() => {
    if (!sel) { setDetail(null); setDetailError(null); return undefined }
    let alive = true
    setDetailLoading(true)
    setDetailError(null)
    const load = async () => {
      try {
        if (sel.kind === 'slope') {
          const risk = await api(`/slopes/${sel.entity.id}/risk`)
          const slope = await api(`/slopes/${sel.entity.id}`)
          let trace = null
          if (risk.assessment?.id) {
            trace = await api(`/assessments/${risk.assessment.id}/trace`).catch(() => null)
          }
          if (alive) setDetail({ risk, slope, trace })
        } else if (sel.kind === 'report') {
          const res = await api(`/reports/${sel.entity.id}/analysis`).catch(() => null)
          const fallback = res || { report: sel.entity, media: [], job: null }
          if (!res) {
            const basic = await api(`/reports/${sel.entity.id}`).catch(() => null)
            if (basic) fallback.report = { ...sel.entity, ...basic.report }
          }
          if (alive) setDetail(fallback)
        } else if (sel.kind === 'alert') {
          const res = await api(`/alerts/${sel.entity.id}`)
          if (alive) setDetail(res)
        } else if (sel.kind === 'incident') {
          const res = await api(`/incidents/${sel.entity.id}`)
          if (alive) setDetail(res)
        }
      } catch (e) {
        if (alive) setDetailError(e.message)
      } finally {
        if (alive) setDetailLoading(false)
      }
    }
    load()
    return () => { alive = false }
  }, [sel])

  const onSelect = (kind, entity) => {
    setSel({ kind, entity })
    const lat = entity.lat ?? entity.slope?.lat
    const lng = entity.lng ?? entity.slope?.lng
    if (lat != null && lng != null) mapRef.current?.focus(lat, lng, kind === 'slope' ? 16 : 15)
  }

  const reset = () => {
    setSearch(''); setBand('ALL'); setStateName('ALL'); setDistrict('ALL'); setAlertStatus('ALL')
    setSel(null)
    mapRef.current?.fitAll()
  }

  if (error) return <div className="banner">Map data unavailable: {error}</div>

  return (
    <div className="stack">
      <MapView
        ref={mapRef}
        nodes={nodes}
        edges={edges}
        reports={showReports ? reports : []}
        alerts={showAlerts ? alerts : []}
        incidents={incidents}
        showEdges={showEdges}
        showReports={showReports}
        showAlerts={showAlerts}
        showIncidents={showIncidents}
        selectedId={sel?.kind === 'slope' ? sel.entity.id : null}
        onSelect={onSelect}
      />

      <div className="map-toolbar" style={{ top: 62 }}>
        <input className="input search" placeholder="Search slope code, name, district…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select className="input" value={band} onChange={(e) => setBand(e.target.value)} aria-label="Risk band filter">
          <option value="ALL">All risk bands</option>
          {['LOW', 'MODERATE', 'HIGH', 'VERY_HIGH', 'CONFIRMED', 'UNASSESSED'].map((b) => <option key={b} value={b}>{b.replace('_', ' ')}</option>)}
        </select>
        <select className="input" value={stateName} onChange={(e) => { setStateName(e.target.value); setDistrict('ALL') }} aria-label="State filter">
          <option value="ALL">All states</option>
          {states.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select className="input" value={district} onChange={(e) => setDistrict(e.target.value)} aria-label="District filter">
          <option value="ALL">All districts</option>
          {districts.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <select className="input" value={alertStatus} onChange={(e) => setAlertStatus(e.target.value)} aria-label="Alert status filter">
          <option value="ALL">All alert statuses</option>
          {['open', 'acknowledged', 'dispatched', 'responding', 'resolved'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <label className="map-toggle"><input type="checkbox" checked={showEdges} onChange={(e) => setShowEdges(e.target.checked)} /> Edges</label>
        <label className="map-toggle"><input type="checkbox" checked={showReports} onChange={(e) => setShowReports(e.target.checked)} /> Reports</label>
        <label className="map-toggle"><input type="checkbox" checked={showAlerts} onChange={(e) => setShowAlerts(e.target.checked)} /> Alerts</label>
        <label className="map-toggle"><input type="checkbox" checked={showIncidents} onChange={(e) => setShowIncidents(e.target.checked)} /> Incidents</label>
        <button className="btn" onClick={reset}>Reset view</button>
        <span className="pill">{nodes.length} slopes · {reports.length} reports · {alerts.length} alerts</span>
      </div>

      {sel && (
        <MapPanel
          sel={sel}
          detail={detail}
          loading={detailLoading}
          error={detailError}
          onClose={() => setSel(null)}
          onOpenSlope={(id) => navigate(`/slopes/${id}`)}
        />
      )}
    </div>
  )
}
