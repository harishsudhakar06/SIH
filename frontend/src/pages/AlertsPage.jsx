import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { useAction, usePoll } from '../hooks.js'
import { StatusBadge, timeAgo, Empty } from '../components/ui.jsx'
import { useAuth } from '../auth.jsx'

export default function AlertsPage() {
  const { can } = useAuth()
  const [status, setStatus] = useState('')
  const query = status ? `?status=${status}` : ''
  const { data: alerts, refresh } = usePoll(`/alerts${query}`, { interval: 5000 })
  const { data: summary, refresh: refreshSummary } = usePoll('/alerts/summary', { interval: 5000 })
  const [note, setNote] = useState('')
  const [detail, setDetail] = useState(null)
  const { busy, run, error } = useAction()

  const act = (path) => run(() => api(`/alerts/${detail.id}/${path}`, { method: 'POST', body: { note } }))
    .then(() => { refresh(); refreshSummary(); loadDetail(detail.id) })

  const loadDetail = async (id) => {
    const res = await api(`/alerts/${id}`)
    setDetail(res)
  }

  return (
    <div className="stack">
      <div className="grid cols-3">
        <div className="card"><h3>By status</h3>
          <div className="row">{Object.entries(summary?.by_status || {}).map(([k, v]) => <span key={k} className="pill">{k}: {v}</span>)}</div>
        </div>
        <div className="card"><h3>By severity</h3>
          <div className="row">{Object.entries(summary?.by_severity || {}).map(([k, v]) => <span key={k} className="pill">{k}: {v}</span>)}</div>
        </div>
        <div className="card"><h3>Duplicate prevention</h3>
          <div className="small muted">Alerts are keyed by slope|band|hour and enforced with a UNIQUE constraint —
            refreshing this page or re-running the pipeline never mints a duplicate alert.</div>
        </div>
      </div>

      <div className="row">
        <select className="input" style={{ width: 180 }} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {['open', 'acknowledged', 'dispatched', 'resolved'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button className="btn" onClick={() => { refresh(); refreshSummary() }}>Refresh now</button>
      </div>

      <div className="card">
        {(alerts?.alerts || []).length === 0 && <Empty>No alerts match.</Empty>}
        <table>
          <thead><tr><th>Alert</th><th>Slope</th><th>Severity</th><th>Status</th><th>Created</th><th></th></tr></thead>
          <tbody>
            {(alerts?.alerts || []).map((a) => (
              <tr key={a.id}>
                <td><strong>{a.title}</strong><div className="small muted mono">{a.alert_code}</div></td>
                <td><Link to={`/slopes/${a.slope_id}`}>{a.slope_code}</Link><div className="small muted">{a.slope_name}</div></td>
                <td><span className={`badge ${a.band}`}>{a.severity.replace('_', ' ')}</span></td>
                <td><StatusBadge status={a.status} />
                  {a.acknowledged_by_name && <div className="small muted">by {a.acknowledged_by_name}</div>}</td>
                <td className="small muted">{timeAgo(a.created_at)}</td>
                <td><button className="btn" onClick={() => loadDetail(a.id)}>Open</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detail && (
        <div className="card">
          <div className="between">
            <div>
              <strong>{detail.alert.title}</strong>
              <div className="small muted">{detail.alert.message}</div>
              <div className="small muted mono">{detail.alert.alert_code} · slope {detail.alert.slope_code}</div>
            </div>
            <button className="btn" onClick={() => setDetail(null)}>Close</button>
          </div>
          <div className="grid cols-2 mt">
            <div>
              <h3>Event log</h3>
              <div className="timeline">
                {(detail.events || []).map((e) => (
                  <div className="timeline-item" key={e.id}>
                    <strong>{e.action}</strong> <span className="small muted">by {e.username || 'system'} · {timeAgo(e.created_at)}</span>
                    {e.note && <div className="small">{e.note}</div>}
                  </div>
                ))}
              </div>
              {detail.incident && (
                <div className="banner info mt">
                  Linked incident <Link to="/incidents">{detail.incident.incident_code}</Link> — status {detail.incident.status}
                </div>
              )}
            </div>
            <div>
              <label className="field"><span>Note for the event log</span>
                <textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>
              <div className="btn-row">
                {can('alerts:ack') && <button className="btn primary" disabled={busy || detail.alert.status !== 'open'}
                  onClick={() => act('ack')}>Acknowledge</button>}
                {can('alerts:ack') && <button className="btn" disabled={busy || detail.alert.status === 'open'}
                  onClick={() => act('ack')}>Add acknowledgement note</button>}
                {can('alerts:resolve') && <button className="btn danger" disabled={busy || detail.alert.status === 'resolved'}
                  onClick={() => act('resolve')}>Resolve</button>}
              </div>
              {error && <div className="banner mt">{error}</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
