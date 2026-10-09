import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AuthProvider, useAuth } from '../shared/auth.jsx'
import { Badge, Shell, Toast, timeAgo, usePoll } from '../shared/ui.jsx'
import { api, photoUrl } from '../shared/api.js'
import { fallbackLocation, useGeolocation } from '../shared/geo.js'
import '../shared/styles.css'

const CATEGORIES = ['crack', 'erosion', 'debris_flow', 'subsidence', 'water_seepage', 'rockfall', 'other']

function ReportForm({ onDone }) {
  const { user } = useAuth()
  const geo = useGeolocation()
  const [title, setTitle] = useState('')
  const [category, setCategory] = useState('crack')
  const [description, setDescription] = useState('')
  const [photo, setPhoto] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [gpsLabel, setGpsLabel] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      let loc = geo.status === 'ok' ? { lat: geo.lat, lng: geo.lng, source: 'device' } : null
      let label = geo.status === 'ok' ? 'device GPS' : ''
      if (!loc) {
        const fb = await fallbackLocation(user)
        if (fb) { loc = fb; label = 'configured fallback location (no GPS fix)' }
      }
      if (!loc) throw new Error('No GPS fix and no configured fallback - grant location permission or set your profile coordinates')

      const form = new FormData()
      form.append('title', title)
      form.append('category', category)
      form.append('description', description)
      form.append('lat', loc.lat)
      form.append('lng', loc.lng)
      form.append('gps_source', loc.source)
      form.append('observed_at', new Date().toISOString())
      if (photo) form.append('photo', photo, photo.name || 'report.jpg')

      const res = await api('/reports', { method: 'POST', formData: form, timeout: 40000 })
      setGpsLabel(label)
      onDone(res)
      setTitle(''); setDescription(''); setPhoto(null)
    } catch (err) {
      setError(err.message)
    } finally { setBusy(false) }
  }

  return (
    <form onSubmit={submit} className="card">
      <h2>Report a slope problem</h2>
      <label className="field"><span>What did you see?</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. crack above the road" required /></label>
      <label className="field"><span>Type</span>
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {CATEGORIES.map((c) => <option key={c} value={c}>{c.replace('_', ' ')}</option>)}
        </select></label>
      <label className="field"><span>Details</span>
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)}
          placeholder="When did you notice it? Is it growing?" /></label>
      <label className="field"><span>Photograph (required for a credible report)</span>
        <input type="file" accept="image/*" capture="environment"
          onChange={(e) => setPhoto(e.target.files?.[0] || null)} /></label>
      {photo && <div className="small muted">Selected: {photo.name} ({Math.round(photo.size / 1024)} KB)</div>}

      <div className="mt">
        <button type="button" className="btn" onClick={geo.request}>
          {geo.status === 'locating' ? 'Getting GPS…' : 'Use my GPS location'}
        </button>
        <div className={`small ${geo.status === 'ok' ? '' : 'muted'}`}>
          {geo.status === 'ok'
            ? <span>✓ {geo.source} · {geo.lat.toFixed(5)}, {geo.lng.toFixed(5)} — {geo.message}</span>
            : <span>{geo.message || 'Location not captured yet'}</span>}
        </div>
        {geo.status === 'error' && (
          <div className="banner info mt">No GPS fix. You can retry, or the app will use your configured
            profile location and label it “fallback_configured”.</div>
        )}
      </div>

      {error && <div className="banner error mt">{error}</div>}
      <button className="btn primary mt" disabled={busy || !title}>
        {busy ? 'Uploading…' : 'Submit report to command center'}
      </button>
      {gpsLabel && <div className="small muted">Last submit used: {gpsLabel}</div>}
    </form>
  )
}

function CitizenApp() {
  const [tab, setTab] = useState('report')
  const [toast, setToast] = useState(null)
  const [result, setResult] = useState(null)
  const { data: mine, refresh } = usePoll('/reports?scope=mine&limit=30', { interval: 7000 })
  const { data: alerts } = usePoll('/alerts?limit=20', { interval: 6000 })
  const { data: slopes } = usePoll('/slopes?limit=60', { interval: 20000 })
  const { user } = useAuth()

  const onDone = (res) => {
    setResult(res)
    refresh()
    setToast(res.assessment
      ? `Report ${res.report.report_code} received. Analysis: ${res.assessment.band} (${Math.round(res.assessment.final_score * 100)}%)${res.assessment.alert_created ? ' — ALERT RAISED' : ''}`
      : `Report ${res.report.report_code} received (no slope matched nearby).`)
  }

  return (
    <Shell title="Citizen" blurb="Report slope problems with a real photograph and GPS location."
      roles={['citizen']} active={tab} onSelect={setTab}
      tabs={[{ key: 'report', label: 'Report', icon: '✎' },
        { key: 'mine', label: 'My reports', icon: '☰' },
        { key: 'risk', label: 'Local risk', icon: '⚠' }]}>
      <Toast message={toast} kind={toast && toast.includes('ALERT') ? 'alert' : ''} onClose={() => setToast(null)} />

      {tab === 'report' && (
        <>
          <div className="banner info">Reports go straight to the laptop over Wi-Fi and are analysed by the
            Belief Fusion Engine within seconds.</div>
          <ReportForm onDone={onDone} />
          {result && (
            <div className="card">
              <h2>What happened next</h2>
              <dl className="kv">
                <dt>Report</dt><dd className="mono">{result.report.report_code}</dd>
                <dt>Matched slope</dt><dd>{result.slope_matched ? `${result.slope_matched.code} — ${result.slope_matched.name} (${result.distance_to_slope_km} km)` : 'no slope within 3 km'}</dd>
                {result.duplicate_of && <><dt>Duplicate of</dt><dd>report #{result.duplicate_of}</dd></>}
                {result.assessment && (<>
                  <dt>Risk band</dt><dd><Badge value={result.assessment.band} /> {Math.round(result.assessment.final_score * 100)}%</dd>
                  <dt>Confidence</dt><dd>{Math.round(result.assessment.confidence * 100)}%</dd>
                  <dt>Next action</dt><dd>{result.assessment.recommendation}</dd>
                  <dt>Alert</dt><dd>{result.assessment.alert_created ? <Badge value="RAISED" kind="open" /> : result.assessment.alert_note}</dd>
                </>)}
                {result.incident && <><dt>Incident</dt><dd className="mono">{result.incident.incident_code}</dd></>}
              </dl>
            </div>
          )}
        </>
      )}

      {tab === 'mine' && (
        <>
          {(mine?.reports || []).length === 0 && <div className="muted">You have not submitted a report yet.</div>}
          {(mine?.reports || []).map((r) => (
            <div className="list-item" key={r.id}>
              <div className="between">
                <div className="title">{r.title}</div>
                <Badge value={r.is_duplicate ? 'duplicate' : r.status} kind={r.is_duplicate ? 'tag' : undefined} />
              </div>
              <div className="small muted mono">{r.report_code} · {r.category} · {timeAgo(r.created_at)}</div>
              <div className="small muted">slope {r.slope_code || 'unmatched'} · gps {r.gps_source}</div>
              {r.photo_path && <img className="photo" src={photoUrl(r.photo_path)} alt="report evidence" />}
            </div>
          ))}
        </>
      )}

      {tab === 'risk' && (
        <>
          <div className="card">
            <h2>Alerts near me ({user?.district || 'all'})</h2>
            {(alerts?.alerts || []).filter((a) => !user?.district || a.slope_district === user.district).slice(0, 10)
              .map((a) => (
                <div className="list-item" key={a.id}>
                  <div className="between"><div className="title">{a.title}</div><Badge value={a.band} /></div>
                  <div className="small muted">{a.message}</div>
                  <div className="small muted mono">{a.alert_code} · {a.status} · {timeAgo(a.created_at)}</div>
                </div>
              ))}
            {alerts && alerts.alerts.filter((a) => !user?.district || a.slope_district === user.district).length === 0 &&
              <div className="muted small">No alerts in your district.</div>}
          </div>
          <div className="card">
            <h2>Assessed slopes nearby</h2>
            {(slopes?.slopes || []).filter((s) => !user?.district || s.district === user.district).slice(0, 12)
              .map((s) => (
                <div className="between" key={s.id} style={{ padding: '7px 0', borderBottom: '1px solid var(--line)' }}>
                  <div><strong>{s.code}</strong> <span className="small muted">{s.name}</span></div>
                  <div className="row"><span className="mono">{s.final_score != null ? Math.round(s.final_score * 100) + '%' : '—'}</span>
                    <Badge value={s.risk_band || 'UNASSESSED'} /></div>
                </div>
              ))}
          </div>
        </>
      )}
    </Shell>
  )
}

createRoot(document.getElementById('root')).render(
  <AuthProvider allowedRoles={['citizen']}><CitizenApp /></AuthProvider>,
)
