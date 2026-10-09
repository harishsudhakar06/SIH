import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet.markercluster'
import 'leaflet.markercluster/dist/MarkerCluster.css'
import 'leaflet.markercluster/dist/MarkerCluster.Default.css'
import { API_BASE } from '../api.js'
import { BAND_COLORS, pct } from './ui.jsx'

const HOME = { center: [25.35, 93.55], zoom: 6 }
const SEV_COLORS = { critical: '#c43a3a', high: '#d4723c', moderate: '#d4a843', low: '#4a9f6a', info: '#2f81f7' }

function slopeIcon(band, score, selected) {
  const color = BAND_COLORS[band] || BAND_COLORS.UNASSESSED
  const size = selected ? 22 : score != null ? 14 + Math.round(score * 8) : 14
  const glyph = band === 'VERY_HIGH' || band === 'CONFIRMED' ? '!' : ''
  return L.divIcon({
    className: '',
    html: `<div class="bh-slope-marker${selected ? ' selected' : ''}" style="--c:${color};--sz:${size}px">${glyph ? `<span class="glyph">${glyph}</span>` : ''}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

const REPORT_ICON = L.divIcon({
  className: '',
  html: `<div class="bh-report-marker"><svg width="18" height="18" viewBox="0 0 18 18"><rect x="1" y="4.5" width="16" height="11" rx="2.5" fill="#2f81f7" stroke="#0d1117" stroke-width="1"/><rect x="5.5" y="2" width="7" height="3.2" rx="1.2" fill="#2f81f7" stroke="#0d1117" stroke-width="1"/><circle cx="9" cy="10" r="3.2" fill="none" stroke="#fff" stroke-width="1.5"/><circle cx="9" cy="10" r="1.2" fill="#fff"/></svg></div>`,
  iconSize: [18, 18],
  iconAnchor: [9, 9],
})

function alertIcon(severity) {
  const c = SEV_COLORS[severity] || SEV_COLORS.critical
  return L.divIcon({
    className: '',
    html: `<div class="bh-alert-marker"><svg width="20" height="18" viewBox="0 0 20 18"><path d="M10 1.2 L18.8 16.8 H1.2 Z" fill="${c}" stroke="#0d1117" stroke-width="1.2" stroke-linejoin="round"/><rect x="9.15" y="6" width="1.7" height="5.2" rx=".7" fill="#fff"/><circle cx="10" cy="13.6" r="1.05" fill="#fff"/></svg></div>`,
    iconSize: [20, 18],
    iconAnchor: [10, 17],
  })
}

const INCIDENT_ICON = L.divIcon({
  className: '',
  html: `<div class="bh-incident-marker"><svg width="16" height="19" viewBox="0 0 16 19"><rect x="2.5" y="1" width="2.2" height="17" rx="1" fill="#c9d7e8" stroke="#0d1117" stroke-width=".8"/><path d="M4.7 2 H14.5 L11.4 5.9 L14.5 9.8 H4.7 Z" fill="#d4723c" stroke="#0d1117" stroke-width=".9" stroke-linejoin="round"/></svg></div>`,
  iconSize: [16, 19],
  iconAnchor: [3.6, 18.5],
})

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))

const slopeTooltip = (n) => {
  const band = n.risk_band || 'UNASSESSED'
  const color = BAND_COLORS[band] || BAND_COLORS.UNASSESSED
  return `<div class="tt-title">${esc(n.code)} — ${esc(n.name)}</div>
    <span class="tt-band" style="background:${color}33;color:${color}">${esc(band.replace('_', ' '))}</span>
    ${n.final_score != null ? `<span class="tt-sub"> score ${pct(n.final_score)} · conf ${pct(n.confidence)}</span>` : '<span class="tt-sub"> not yet assessed</span>'}
    <div class="tt-sub">${esc(n.district)}, ${esc(n.state)}${n.edge_count ? ` · ${n.edge_count} connections` : ''}</div>`
}

const MapView = forwardRef(function MapView({
  nodes = [], edges = [], reports = [], alerts = [], incidents = [],
  showEdges = true, showReports = true, showAlerts = true, showIncidents = true,
  selectedId = null, onSelect = null, height,
}, ref) {
  const hostRef = useRef(null)
  const mapRef = useRef(null)
  const layersRef = useRef({})
  const didFitRef = useRef(false)
  const tileErrRef = useRef(0)
  const [tileWarn, setTileWarn] = useState(false)

  useImperativeHandle(ref, () => ({
    fitAll() {
      const map = mapRef.current
      if (!map) return
      didFitRef.current = true
      if (nodes.length) {
        map.fitBounds(L.latLngBounds(nodes.map((n) => [n.lat, n.lng])).pad(0.12))
      } else {
        map.setView(HOME.center, HOME.zoom)
      }
    },
    focus(lat, lng, zoom = 16) {
      const map = mapRef.current
      if (!map) return
      map.flyTo([lat, lng], zoom, { duration: 0.7 })
      const panelWidth = Math.min(420, window.innerWidth * 0.92)
      map.once('moveend', () => {
        if (window.innerWidth > 900) map.panBy([panelWidth / 2 - 30, 0], { animate: true })
      })
    },
  }), [nodes])

  useEffect(() => {
    if (!hostRef.current || mapRef.current) return undefined
    const map = L.map(hostRef.current, {
      center: HOME.center, zoom: HOME.zoom, zoomControl: false, preferCanvas: true,
      minZoom: 5, maxZoom: 18,
    })
    L.control.zoom({ position: 'topleft' }).addTo(map)
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map)

    const tiles = L.tileLayer(`${API_BASE}/tiles/{z}/{x}/{y}.png`, {
      maxZoom: 18, maxNativeZoom: 18,
      attribution: '© OpenStreetMap contributors · local tile cache',
    })
    tiles.on('tileerror', () => {
      tileErrRef.current += 1
      if (tileErrRef.current > 4) setTileWarn(true)
    })
    tiles.on('tileload', () => { tileErrRef.current = 0 })
    tiles.addTo(map)

    const edgesLayer = L.layerGroup().addTo(map)
    const slopeCluster = L.markerClusterGroup({
      showCoverageOnHover: false, spiderfyOnMaxZoom: true, maxClusterRadius: 44, chunkedLoading: true,
    }).addTo(map)
    const reportCluster = L.markerClusterGroup({
      showCoverageOnHover: false, spiderfyOnMaxZoom: true, maxClusterRadius: 40, chunkedLoading: true,
    })
    const alertCluster = L.markerClusterGroup({
      showCoverageOnHover: false, spiderfyOnMaxZoom: true, maxClusterRadius: 40, chunkedLoading: true,
    })
    const incidentLayer = L.layerGroup()

    layersRef.current = { edgesLayer, slopeCluster, reportCluster, alertCluster, incidentLayer }
    mapRef.current = map

    fetch(`${API_BASE}/geo/states`)
      .then((r) => (r.ok ? r.json() : null))
      .then((geo) => {
        if (!geo || !mapRef.current) return
        const layer = L.geoJSON(geo, {
          style: { color: '#8b949e', weight: 0.9, opacity: 0.5, fill: false, interactive: false },
        })
        layersRef.current.statesLayer = layer
        layer.addTo(mapRef.current)
        layer.bringToBack()
      })
      .catch(() => { /* boundaries are optional context */ })

    const ro = new ResizeObserver(() => map.invalidateSize())
    ro.observe(hostRef.current)
    setTimeout(() => map.invalidateSize(), 200)

    return () => {
      ro.disconnect()
      map.remove()
      mapRef.current = null
      layersRef.current = {}
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    const Ls = layersRef.current
    if (!map || !Ls.edgesLayer) return

    Ls.edgesLayer.clearLayers()
    if (showEdges) {
      const byId = new Map(nodes.map((n) => [n.id, n]))
      for (const e of edges) {
        const a = byId.get(e.src_slope_id)
        const b = byId.get(e.dst_slope_id)
        if (!a || !b) continue
        const color = e.rel_type === 'hydrology_downslope' ? '#a371f7'
          : e.rel_type === 'rainfall_similarity' ? '#58a6ff'
            : e.rel_type === 'geology_similarity' ? '#d4a72c' : '#3b475a'
        L.polyline([[a.lat, a.lng], [b.lat, b.lng]], {
          color, weight: 1 + e.strength * 2, opacity: 0.3 + e.strength * 0.3,
          dashArray: e.validated ? null : '4 6', interactive: false,
        })
          .bindTooltip(`${esc(e.rel_type)} · strength ${Number(e.strength).toFixed(2)}${e.validated ? '' : ' (unvalidated)'}`)
          .addTo(Ls.edgesLayer)
      }
    }

    Ls.slopeCluster.clearLayers()
    for (const n of nodes) {
      const band = n.risk_band || 'UNASSESSED'
      const m = L.marker([n.lat, n.lng], {
        icon: slopeIcon(band, n.final_score, selectedId === n.id && n.id != null),
        keyboard: true,
        title: `${n.code} ${band}`,
      })
      m.bindTooltip(() => slopeTooltip(n), { direction: 'top', sticky: true, className: 'bh-tooltip', opacity: 1 })
      m.on('click', () => onSelect?.('slope', n))
      Ls.slopeCluster.addLayer(m)
    }

    Ls.reportCluster.clearLayers()
    if (showReports) {
      for (const r of reports) {
        if (r.lat == null || r.lng == null) continue
        const m = L.marker([r.lat, r.lng], { icon: REPORT_ICON, title: r.report_code || `Report ${r.id}` })
        m.bindTooltip(
          `<div class="tt-title">${esc(r.report_code || `Report #${r.id}`)}</div>
           <div>${esc(r.title)}</div>
           <div class="tt-sub">${esc(r.category)} · ${esc(r.status)}${r.is_duplicate ? ' · duplicate' : ''}${r.photo_count ? ` · ${r.photo_count} photo(s)` : ''}</div>`,
          { direction: 'top', sticky: true, className: 'bh-tooltip', opacity: 1 },
        )
        m.on('click', () => onSelect?.('report', r))
        Ls.reportCluster.addLayer(m)
      }
    }

    Ls.alertCluster.clearLayers()
    if (showAlerts) {
      for (const a of alerts) {
        if (a.lat == null || a.lng == null) continue
        const m = L.marker([a.lat, a.lng], { icon: alertIcon(a.severity), title: a.alert_code || 'Alert' })
        m.bindTooltip(
          `<div class="tt-title">${esc((a.severity || '').toUpperCase())} · ${esc(a.title)}</div>
           <div class="tt-sub">${esc(a.alert_code || '')} · ${esc(a.status)} · ${esc(a.slope_code || '')}</div>`,
          { direction: 'top', sticky: true, className: 'bh-tooltip', opacity: 1 },
        )
        m.on('click', () => onSelect?.('alert', a))
        Ls.alertCluster.addLayer(m)
      }
      if (!map.hasLayer(Ls.alertCluster)) Ls.alertCluster.addTo(map)
    } else if (map.hasLayer(Ls.alertCluster)) {
      map.removeLayer(Ls.alertCluster)
    }

    Ls.incidentLayer.clearLayers()
    if (showIncidents) {
      const byId = new Map(nodes.map((n) => [n.id, n]))
      for (const inc of incidents) {
        const node = byId.get(inc.slope_id)
        if (!node) continue
        const m = L.marker([node.lat, node.lng], { icon: INCIDENT_ICON, title: `Incident ${inc.incident_code || inc.id}` })
        m.bindTooltip(
          `<div class="tt-title">Incident ${esc(inc.incident_code || `#${inc.id}`)}</div>
           <div>${esc(inc.title)}</div>
           <div class="tt-sub">${esc(inc.status)} · ${esc(node.code)}</div>`,
          { direction: 'top', sticky: true, className: 'bh-tooltip', opacity: 1 },
        )
        m.on('click', () => onSelect?.('incident', { ...inc, lat: node.lat, lng: node.lng, slope: node }))
        m.addTo(Ls.incidentLayer)
      }
      if (!map.hasLayer(Ls.incidentLayer)) Ls.incidentLayer.addTo(map)
    } else if (map.hasLayer(Ls.incidentLayer)) {
      map.removeLayer(Ls.incidentLayer)
    }

    if (showReports && !map.hasLayer(Ls.reportCluster)) Ls.reportCluster.addTo(map)
    if (!showReports && map.hasLayer(Ls.reportCluster)) map.removeLayer(Ls.reportCluster)

    if (nodes.length && !didFitRef.current) {
      didFitRef.current = true
      map.fitBounds(L.latLngBounds(nodes.map((n) => [n.lat, n.lng])).pad(0.12))
    }
  }, [nodes, edges, reports, alerts, incidents, showEdges, showReports, showAlerts, showIncidents, selectedId, onSelect])

  return (
    <div className="map-stage" style={height ? { height } : undefined}>
      <div className="map-wrap" ref={hostRef} />
      <div className="legend" style={{ left: 12, bottom: 34 }}>
        <div><span className="dot" style={{ background: BAND_COLORS.LOW }} /> LOW</div>
        <div><span className="dot" style={{ background: BAND_COLORS.MODERATE }} /> MODERATE</div>
        <div><span className="dot" style={{ background: BAND_COLORS.HIGH }} /> HIGH</div>
        <div><span className="dot" style={{ background: BAND_COLORS.VERY_HIGH }} /> VERY HIGH</div>
        <div><span className="dot" style={{ background: BAND_COLORS.CONFIRMED }} /> CONFIRMED</div>
        <div><span className="bh-report-marker" style={{ width: 14, height: 14 }}><svg width="12" height="12" viewBox="0 0 18 18"><rect x="1" y="4.5" width="16" height="11" rx="2.5" fill="#2f81f7"/><circle cx="9" cy="10" r="3" fill="none" stroke="#fff" stroke-width="1.5"/></svg></span> Report</div>
        <div><span className="bh-alert-marker" style={{ width: 14, height: 13 }}><svg width="13" height="12" viewBox="0 0 20 18"><path d="M10 1.2 L18.8 16.8 H1.2 Z" fill="#c43a3a"/></svg></span> Alert</div>
        <div><span className="bh-incident-marker" style={{ width: 12, height: 14 }}><svg width="11" height="13" viewBox="0 0 16 19"><rect x="2.5" y="1" width="2.2" height="17" fill="#c9d7e8"/><path d="M4.7 2 H14.5 L11.4 5.9 L14.5 9.8 H4.7 Z" fill="#d4723c"/></svg></span> Incident</div>
        <div className="muted" style={{ marginTop: 4 }}>— dashed = unvalidated edge</div>
      </div>
      {tileWarn && <div className="map-offline-note">Some basemap tiles offline — serving from local cache / placeholders</div>}
    </div>
  )
})

export default MapView
