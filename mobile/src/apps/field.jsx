import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AuthProvider, useAuth } from '../shared/auth.jsx'
import { Badge, Login, Shell, Toast, timeAgo, usePoll } from '../shared/ui.jsx'
import { api } from '../shared/api.js'
import { fallbackLocation, useGeolocation } from '../shared/geo.js'
import '../shared/styles.css'

function ObservationForm({ slopeId, onDone, presetReportId }) {
  const { user } = useAuth()
  const geo = useGeolocation()
  const [status, setStatus] = useState('ground_truth')
  const [crack, setCrack] = useState('')
  const [seepage, setSeepage] = useState(false)
  const [scarp, setScarp] = useState(false)
  const [displacement, setDisplacement] = useState('')
  const [notes, setNotes] = useState('')
  const [photo, setPhoto] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      let loc = geo.status === 'ok' ? { lat: geo.lat, lng: geo.lng, source: 'device' } : null
      if (!loc) loc = await fallbackLocation(user)

      if (presetReportId) {
        const form = new FormData()
        form.append('verification_status', status)
        if (crack) form.append('crack_mm', crack)
        form.append('seepage', seepage)
        form.append('fresh_scarp', scarp)
        if (displacement) form.append('displacement_m', displacement)
        form.append('notes', notes)
        if (loc) { form.append('lat', loc.lat); form.append('lng', loc.lng); form.append('gps_source', loc.source) }
        if (photo) form.append('photo', photo, photo.name || 'field.jpg')
        const res = await api(`/reports/${presetReportId}/verify`, { method: 'POST', formData: form, timeout: 40000 })
        onDone(`Verification saved for report #${presetReportId} → slope assessed as ${res.assessment?.band || 'n/a'}`)
      } else {
        const res = await api('/observations', {
          method: 'POST',
          body: {
            slope_id: slopeId, verification_status: status,
            crack_mm: crack ? Number(crack) : null, seepage, fresh_scarp: scarp,
            displacement_m: displacement ? Number(displacement) : null, notes,
            lat: loc?.lat, lng: loc?.lng, gps_source: loc?.source || 'unavailable',
            observed_at: new Date().toISOString(),
          },
        })
        onDone(`Observation saved for slope #${slopeId} → ${res.assessment?.band || 'n/a'} (${Math.round((res.assessment?.final_score || 0) * 100)}%)`)
      }
      setNotes(''); setPhoto(null)
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  return (
    <form onSubmit={submit} className="card">
      <label className="field"><span>Verification outcome</span>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="ground_truth">ground truth — movement confirmed</option>
          <option value="inconclusive">inconclusive — signs but no movement</option>
          <option value="not_verified">not verified — nothing observed</option>
        </select></label>
      <div className="grid2">
        <label className="field"><span>Crack width (mm)</span>
          <input type="number" step="0.1" value={crack} onChange={(e) => setCrack(e.target.value)} /></label>
        <label className="field"><span>Displacement (m)</span>
          <input type="number" step="0.01" value={displacement} onChange={(e) => setDisplacement(e.target.value)} /></label>
      </div>
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="row small"><input type="checkbox" style={{ width: 'auto' }} checked={seepage}
          onChange={(e) => setSeepage(e.target.checked)} /> water seepage</label>
        <label className="row small"><input type="checkbox" style={{ width: 'auto' }} checked={scarp}
          onChange={(e) => setScarp(e.target.checked)} /> fresh scarp</label>
      </div>
      <label className="field"><span>Field notes</span>
        <textarea rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>
      <label className="field"><span>Field photograph</span>
        <input type="file" accept="image/*" capture="environment"
          onChange={(e) => setPhoto(e.target.files?.[0] || null)} /></label>
      <button type="button" className="btn" onClick={geo.request}>
        {geo.status === 'locating' ? 'Getting GPS…' : 'Capture GPS'}
      </button>
      <div className="small muted">
        {geo.status === 'ok' ? `${geo.lat.toFixed(5)}, ${geo.lng.toFixed(5)} · ${geo.message}`
          : 'No device fix yet — profile fallback will be used and labelled.'}
      </div>
      {error && <div className="banner error mt">{error}</div>}
      <button className="btn primary mt" disabled={busy}>
        {busy ? 'Submitting…' : 'Submit field verification'}
      </button>
    </form>
  )
}

function FieldApp() {
  const [tab, setTab] = useState('tasks')
  const [selected, setSelected] = useState(null)
  const [toast, setToast] = useState('')
  const { data: tasks, refresh } = usePoll('/tasks/verification', { interval: 8000 })
  const { data: reports } = usePoll('/reports?scope=all&limit=30', { interval: 10000 })
  const { data: mine } = usePoll('/observations?mine=1&limit=30', { interval: 12000 })
  const { data: slopes } = usePoll('/slopes?limit=200', { interval: 25000 })

  const done = (msg) => { setToast(msg); setSelected(null); refresh() }

  return (
    <Shell title="Field Verification" blurb="Confirm or refute reported slope movement with a field observation."
      roles={['field_volunteer', 'analyst']} active={tab} onSelect={setTab}
      tabs={[{ key: 'tasks', label: 'Tasks', icon: '☰' },
        { key: 'submit', label: 'New obs', icon: '✎' },
        { key: 'reports', label: 'Verify report', icon: '⚑' },
        { key: 'mine', label: 'Mine', icon: '✓' }]}>
      <Toast message={toast} onClose={() => setToast('')} />

      {tab === 'tasks' && (
        <>
          <div className="banner info">Uncertainty-driven queue: low fusion confidence, weak evidence or
            elevated bands are prioritised for a human check.</div>
          {(tasks?.tasks || []).map((t) => (
            <div className="list-item" key={t.slope_id}>
              <div className="between">
                <div className="title">{t.code} — {t.name}</div>
                <Badge value={t.risk_band} />
              </div>
              <div className="small muted">{t.district}, {t.state} · score {Math.round(t.final_score * 100)}%
                · confidence {Math.round(t.confidence * 100)}%</div>
              <div className="small muted">reasons: {t.reasons.join(', ')}</div>
              <div className="row mt">
                <button className="btn sm primary" onClick={() => { setSelected(t); setTab('submit') }}>Verify now</button>
                <span className="small muted">assessed {timeAgo(t.assessed_at)}</span>
              </div>
            </div>
          ))}
          {tasks && tasks.tasks.length === 0 && <div className="muted">Queue is empty.</div>}
        </>
      )}

      {tab === 'submit' && (
        <>
          {!selected && (
            <label className="field"><span>Choose a slope</span>
              <select value="" onChange={(e) => setSelected({ slope_id: Number(e.target.value), code: '', name: '' })}>
                <option value="">— select slope —</option>
                {(slopes?.slopes || []).map((s) => (
                  <option key={s.id} value={s.id}>{s.code} · {s.name} · {s.risk_band || 'UNASSESSED'}</option>
                ))}
              </select></label>
          )}
          {selected && (
            <div className="card" style={{ paddingBottom: 4 }}>
              <div className="between"><strong>{selected.code || ''} {selected.name || ''}</strong>
                <button className="btn sm" onClick={() => setSelected(null)}>change</button></div>
            </div>
          )}
          {selected && <ObservationForm slopeId={selected.slope_id} onDone={done} />}
        </>
      )}

      {tab === 'reports' && (
        <>
          {(reports?.reports || []).filter((r) => r.status === 'received').map((r) => (
            <div className="list-item" key={r.id}>
              <div className="between"><div className="title">{r.title}</div><span className="badge tag">{r.category}</span></div>
              <div className="small muted mono">{r.report_code} · slope {r.slope_code || 'unmatched'} · {timeAgo(r.created_at)}</div>
              <button className="btn sm primary mt" onClick={() => setSelected({ reportId: r.id, title: r.title })}>
                Verify this report
              </button>
              {selected?.reportId === r.id && (
                <div className="mt">
                  <ObservationForm presetReportId={r.id} onDone={done} />
                </div>
              )}
            </div>
          ))}
          {reports && reports.reports.filter((r) => r.status === 'received').length === 0 &&
            <div className="muted">No pending reports to verify.</div>}
        </>
      )}

      {tab === 'mine' && (
        (mine?.observations || []).map((o) => (
          <div className="list-item" key={o.id}>
            <div className="between">
              <div className="title">{o.slope_code} — {o.slope_name}</div>
              <Badge value={o.verification_status} kind={o.verification_status === 'ground_truth' ? 'CONFIRMED' : 'tag'} />
            </div>
            <div className="small muted">crack {o.crack_mm ?? '—'} mm · seepage {o.seepage ? 'yes' : 'no'} ·
              scarp {o.fresh_scarp ? 'yes' : 'no'} · {timeAgo(o.observed_at)}</div>
            {o.notes && <div className="small">{o.notes}</div>}
          </div>
        ))
      )}
    </Shell>
  )
}

createRoot(document.getElementById('root')).render(
  <AuthProvider allowedRoles={['field_volunteer', 'analyst']}><FieldApp /></AuthProvider>,
)
