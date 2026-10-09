import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import { useAction, usePoll } from '../hooks.js'
import { StatusBadge, timeAgo, Empty } from '../components/ui.jsx'
import { useAuth } from '../auth.jsx'

const NEXT = {
  reported: ['verifying', 'acknowledged', 'closed'],
  verifying: ['acknowledged', 'resolved', 'closed'],
  acknowledged: ['dispatched', 'responding', 'resolved'],
  dispatched: ['responding', 'resolved'],
  responding: ['resolved'],
  resolved: ['closed'],
  closed: [],
}

export default function IncidentsPage() {
  const { can } = useAuth()
  const { data, refresh } = usePoll('/incidents', { interval: 6000 })
  const [detail, setDetail] = useState(null)
  const [note, setNote] = useState('')
  const [team, setTeam] = useState('SDRF Team A')
  const [eta, setEta] = useState(30)
  const { busy, run, error } = useAction()

  const load = async (id) => setDetail(await api(`/incidents/${id}`))

  const transition = (to) => run(() => api(`/incidents/${detail.incident.id}/events`,
    { method: 'POST', body: { to_status: to, note } })).then(() => { refresh(); load(detail.incident.id) })

  const dispatch = () => run(() => api(`/incidents/${detail.incident.id}/dispatch`,
    { method: 'POST', body: { team_name: team, personnel: 4, eta_minutes: Number(eta), note } }))
    .then(() => { refresh(); load(detail.incident.id) })

  return (
    <div className="stack">
      <div className="row">
        <span className="pill">{data?.count ?? 0} incidents</span>
        <span className="pill">roles: community leader / response / analyst can transition · only response can dispatch</span>
        <button className="btn" onClick={refresh}>Refresh</button>
      </div>

      <div className="card">
        {(data?.incidents || []).length === 0 && <Empty>No incidents yet.</Empty>}
        <table>
          <thead><tr><th>Incident</th><th>Slope</th><th>Severity</th><th>Status</th><th>Dispatch</th><th>Updated</th><th></th></tr></thead>
          <tbody>
            {(data?.incidents || []).map((i) => (
              <tr key={i.id}>
                <td><strong>{i.title}</strong><div className="small muted mono">{i.incident_code}</div></td>
                <td>{i.slope_code ? <Link to={`/slopes/${i.slope_id}`}>{i.slope_code}</Link> : '—'}</td>
                <td><span className="badge tag">{i.severity}</span></td>
                <td><StatusBadge status={i.status} /></td>
                <td className="small muted">{i.dispatch_count} dispatch · {i.event_count} events</td>
                <td className="small muted">{timeAgo(i.updated_at)}</td>
                <td><button className="btn" onClick={() => load(i.id)}>Open</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detail && (
        <div className="card">
          <div className="between">
            <div>
              <div className="row">
                <strong>{detail.incident.title}</strong>
                <StatusBadge status={detail.incident.status} />
              </div>
              <div className="small muted mono">{detail.incident.incident_code}
                {detail.incident.slope_code && <> · <Link to={`/slopes/${detail.incident.slope_id}`}>{detail.incident.slope_code}</Link></>}
              </div>
              <div className="small muted">{detail.incident.description}</div>
            </div>
            <button className="btn" onClick={() => setDetail(null)}>Close</button>
          </div>

          <div className="grid cols-2 mt">
            <div>
              <h3>Timeline</h3>
              <div className="timeline">
                {(detail.events || []).map((e) => (
                  <div className="timeline-item" key={e.id}>
                    <strong>{e.event_type}</strong>
                    {e.to_status && <span className="small muted"> {e.from_status} → {e.to_status}</span>}
                    <div className="small muted">{e.username || 'system'} · {timeAgo(e.created_at)}</div>
                    {e.note && <div className="small">{e.note}</div>}
                  </div>
                ))}
              </div>

              <h3 className="mt">Dispatches</h3>
              {(detail.dispatches || []).length === 0 && <Empty>No team dispatched yet.</Empty>}
              {(detail.dispatches || []).map((d) => (
                <div className="feed-item" key={d.id}>
                  <span className="feed-kind">{d.status}</span>
                  <div>
                    <strong>{d.team_name}</strong> · {d.personnel} personnel
                    {d.eta_minutes ? ` · ETA ${d.eta_minutes} min` : ''}
                    <div className="small muted">{d.note} · requested by {d.requested_by_name || '—'} · {timeAgo(d.created_at)}</div>
                    <div className="btn-row mt">
                      {can('dispatch:create') && ['enroute', 'on_scene', 'completed'].map((s) => (
                        <button key={s} className="btn" disabled={busy}
                          onClick={() => run(() => api(`/dispatches/${d.id}`, { method: 'PATCH', body: { status: s } }))
                            .then(() => { refresh(); load(detail.incident.id) })}>{s.replace('_', ' ')}</button>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>

            <div>
              <label className="field"><span>Note</span>
                <textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
              </label>

              {can('incidents:transition') && (
                <div>
                  <h3>Move to</h3>
                  <div className="btn-row">
                    {(NEXT[detail.incident.status] || []).map((s) => (
                      <button key={s} className="btn" disabled={busy} onClick={() => transition(s)}>{s}</button>
                    ))}
                    {(NEXT[detail.incident.status] || []).length === 0 && <span className="muted small">terminal state</span>}
                  </div>
                </div>
              )}

              {can('dispatch:create') && (
                <div className="mt">
                  <h3>Dispatch a team</h3>
                  <div className="row">
                    <input className="input" style={{ width: 200 }} value={team} onChange={(e) => setTeam(e.target.value)} />
                    <input className="input" style={{ width: 110 }} type="number" value={eta} onChange={(e) => setEta(e.target.value)} />
                    <button className="btn primary" disabled={busy} onClick={dispatch}>Dispatch</button>
                  </div>
                </div>
              )}
              {error && <div className="banner mt">{error}</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
