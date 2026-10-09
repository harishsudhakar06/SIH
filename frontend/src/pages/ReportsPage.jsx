import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, photoUrl } from '../api.js'
import { useAction, usePoll } from '../hooks.js'
import { RiskBadge, StatusBadge, timeAgo, Empty } from '../components/ui.jsx'
import { useAuth } from '../auth.jsx'

const CATEGORIES = ['crack', 'erosion', 'debris_flow', 'subsidence', 'water_seepage', 'rockfall', 'other']

export default function ReportsPage() {
  const { user, can } = useAuth()
  const { data, refresh } = usePoll('/reports?scope=all&limit=100', { interval: 6000 })
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState('all')
  const { busy, run, error } = useAction()

  const reports = (data?.reports || []).filter((r) => filter === 'all' || r.status === filter)

  const verify = (status) => run(() => {
    const form = new FormData()
    form.append('verification_status', status)
    form.append('crack_mm', selected.crack_mm_estimate || '')
    form.append('notes', document.getElementById('verify_notes')?.value || '')
    form.append('gps_source', 'manual')
    return api(`/reports/${selected.id}/verify`, { method: 'POST', formData: form })
  }).then(() => { refresh(); setSelected(null) })

  return (
    <div className="stack">
      <div className="banner info">
        Citizen reports arrive here from the mobile apps over the LAN with the original photograph.
        Every report triggers the analysis pipeline for its slope.
      </div>

      <div className="row">
        <select className="input" style={{ width: 200 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">All statuses</option>
          {['received', 'verified'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="pill">{reports.length} reports</span>
        <span className="pill">{reports.filter((r) => r.photo_path).length} with photographs</span>
      </div>

      <div className="card">
        {reports.length === 0 && <Empty>No reports yet. Submit one from the Citizen app.</Empty>}
        <table>
          <thead>
            <tr><th></th><th>Report</th><th>Slope</th><th>Category</th><th>Status</th><th>GPS</th><th>Received</th></tr>
          </thead>
          <tbody>
            {reports.map((r) => (
              <tr key={r.id} className="clickable" onClick={() => setSelected(r)}>
                <td>{r.photo_path
                  ? <img className="thumb" src={photoUrl(r.photo_path)} alt="" />
                  : <span className="muted small">no photo</span>}</td>
                <td><strong>{r.title}</strong><div className="small muted mono">{r.report_code}</div></td>
                <td>{r.slope_code ? <Link to={`/slopes/${r.slope_id}`}>{r.slope_code}</Link> : <span className="muted">unmatched</span>}
                  <div className="small muted">{r.slope_name}</div></td>
                <td><span className="badge tag">{r.category}</span>
                  {r.is_duplicate ? <div className="small muted">duplicate of #{r.duplicate_of}</div> : null}</td>
                <td><StatusBadge status={r.status} /></td>
                <td className="small muted">{r.gps_source}</td>
                <td className="small muted">{timeAgo(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <div className="card">
          <div className="between">
            <div>
              <strong>{selected.title}</strong>
              <div className="small muted mono">{selected.report_code} · reported by {selected.reporter || 'citizen'} · {timeAgo(selected.created_at)}</div>
            </div>
            <button className="btn" onClick={() => setSelected(null)}>Close</button>
          </div>
          <div className="grid cols-2 mt">
            <div>
              {selected.photo_path
                ? <img className="photo" src={photoUrl(selected.photo_path)} alt="report" />
                : <div className="muted small">No photograph attached.</div>}
              <div className="small muted mt">photo_origin: {selected.photo_origin || 'user_upload'}</div>
            </div>
            <div>
              <dl className="kv">
                <dt>Description</dt><dd>{selected.description || '—'}</dd>
                <dt>Coordinates</dt><dd className="mono">{selected.lat}, {selected.lng} ({selected.gps_source})</dd>
                <dt>Observed</dt><dd>{timeAgo(selected.observed_at)}</dd>
                <dt>Slope</dt><dd>{selected.slope_code || 'not matched'} </dd>
                <dt>Duplicate</dt><dd>{selected.is_duplicate ? `yes → report #${selected.duplicate_of}` : 'no'}</dd>
                <dt>Status</dt><dd><StatusBadge status={selected.status} /></dd>
              </dl>
              {can('reports:verify') && (
                <div className="mt">
                  <label className="field"><span>Verification notes (field)</span>
                    <textarea id="verify_notes" className="input" rows={3} placeholder="crack measured, scarp visible, seepage…" />
                  </label>
                  <div className="btn-row">
                    <button className="btn primary" disabled={busy} onClick={() => verify('ground_truth')}>Confirm ground truth</button>
                    <button className="btn" disabled={busy} onClick={() => verify('inconclusive')}>Inconclusive</button>
                    <button className="btn" disabled={busy} onClick={() => verify('not_verified')}>Not verified</button>
                  </div>
                  {error && <div className="banner mt">{error}</div>}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
