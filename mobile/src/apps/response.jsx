import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AuthProvider, useAuth } from '../shared/auth.jsx'
import { Badge, Shell, Toast, timeAgo, usePoll } from '../shared/ui.jsx'
import { api } from '../shared/api.js'
import '../shared/styles.css'

const NEXT = {
  reported: ['verifying', 'acknowledged', 'closed'],
  verifying: ['acknowledged', 'resolved', 'closed'],
  acknowledged: ['dispatched', 'responding', 'resolved'],
  dispatched: ['responding', 'resolved'],
  responding: ['resolved'],
  resolved: ['closed'],
  closed: [],
}

function ResponseApp() {
  const { can } = useAuth()
  const [tab, setTab] = useState('alerts')
  const [toast, setToast] = useState('')
  const [note, setNote] = useState('')
  const [detail, setDetail] = useState(null)
  const [team, setTeam] = useState('SDRF Team A')
  const [eta, setEta] = useState(30)
  const [busy, setBusy] = useState(false)

  const { data: alerts, refresh: refreshAlerts } = usePoll('/alerts?limit=40', { interval: 5000 })
  const { data: incidents, refresh: refreshIncidents } = usePoll('/incidents?limit=40', { interval: 6000 })

  const load = async (id) => setDetail(await api(`/incidents/${id}`))

  const ack = async (a) => {
    setBusy(true)
    try {
      const res = await api(`/alerts/${a.id}/ack`, { method: 'POST', body: { note: note || 'response team acknowledged' } })
      setToast(`Acknowledged ${a.alert_code} → incident ${res.incident.incident_code}`)
      refreshAlerts(); refreshIncidents()
      if (res.incident) await load(res.incident.id)
    } catch (e) { setToast(e.message) } finally { setBusy(false) }
  }

  const resolveAlert = async (a) => {
    setBusy(true)
    try {
      await api(`/alerts/${a.id}/resolve`, { method: 'POST', body: { note: note || 'resolved' } })
      setToast(`Alert ${a.alert_code} resolved`); refreshAlerts(); refreshIncidents()
    } catch (e) { setToast(e.message) } finally { setBusy(false) }
  }

  const move = async (to) => {
    setBusy(true)
    try {
      await api(`/incidents/${detail.incident.id}/events`, { method: 'POST', body: { to_status: to, note } })
      setToast(`Incident moved to ${to}`); refreshIncidents(); await load(detail.incident.id)
    } catch (e) { setToast(e.message) } finally { setBusy(false) }
  }

  const doDispatch = async () => {
    setBusy(true)
    try {
      const res = await api(`/incidents/${detail.incident.id}/dispatch`, {
        method: 'POST', body: { team_name: team, personnel: 6, eta_minutes: Number(eta), note },
      })
      setToast(`${team} dispatched, ETA ${eta} min`); refreshIncidents(); refreshAlerts()
      await load(res.incident.id)
    } catch (e) { setToast(e.message) } finally { setBusy(false) }
  }

  const patchDispatch = async (id, status) => {
    setBusy(true)
    try {
      await api(`/dispatches/${id}`, { method: 'PATCH', body: { status } })
      refreshIncidents(); await load(detail.incident.id)
    } catch (e) { setToast(e.message) } finally { setBusy(false) }
  }

  return (
    <Shell title="Response" blurb="Acknowledge alerts, open incidents, dispatch teams and update status."
      roles={['response_operator']} active={tab} onSelect={setTab}
      tabs={[{ key: 'alerts', label: 'Alerts', icon: '⚠' },
        { key: 'incidents', label: 'Incidents', icon: '⛑' }]}>
      <Toast message={toast} kind="alert" onClose={() => setToast('')} />

      {tab === 'alerts' && (
        <>
          <label className="field"><span>Note recorded on your action</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="team alerted, road blocked…" /></label>
          {(alerts?.alerts || []).map((a) => (
            <div className="list-item" key={a.id}>
              <div className="between"><div className="title">{a.title}</div><Badge value={a.band} /></div>
              <div className="small muted">{a.message}</div>
              <div className="small muted mono">{a.alert_code} · {a.slope_code} · {timeAgo(a.created_at)}</div>
              <div className="row mt">
                <Badge value={a.status} />
                {can('alerts:ack') && a.status === 'open' &&
                  <button className="btn sm primary" disabled={busy} onClick={() => ack(a)}>Acknowledge</button>}
                {can('alerts:resolve') && a.status !== 'resolved' &&
                  <button className="btn sm danger" disabled={busy} onClick={() => resolveAlert(a)}>Resolve</button>}
              </div>
            </div>
          ))}
          {alerts && alerts.alerts.length === 0 && <div className="muted">No alerts.</div>}
        </>
      )}

      {tab === 'incidents' && (
        <>
          {!detail && (incidents?.incidents || []).map((i) => (
            <div className="list-item" key={i.id}>
              <div className="between"><div className="title">{i.title}</div><Badge value={i.status} /></div>
              <div className="small muted mono">{i.incident_code} · slope {i.slope_code || '—'} · {i.severity}</div>
              <div className="small muted">{i.dispatch_count} dispatch · {i.event_count} events · updated {timeAgo(i.updated_at)}</div>
              <button className="btn sm primary mt" onClick={() => load(i.id)}>Open incident</button>
            </div>
          ))}
          {detail && (
            <div className="card">
              <div className="between">
                <div><div className="title">{detail.incident.title}</div>
                  <div className="small muted mono">{detail.incident.incident_code}</div></div>
                <button className="btn sm" onClick={() => setDetail(null)}>back</button>
              </div>
              <div className="row mt"><Badge value={detail.incident.status} />
                <span className="small muted">{detail.incident.severity} · slope {detail.incident.slope_code || '—'}</span></div>

              <h3>Timeline</h3>
              {detail.events.map((e) => (
                <div key={e.id} className="small" style={{ borderBottom: '1px solid var(--line)', padding: '7px 0' }}>
                  <strong>{e.event_type}</strong>{e.to_status ? ` → ${e.to_status}` : ''}
                  <div className="muted">{e.username || 'system'} · {timeAgo(e.created_at)}{e.note ? ` · ${e.note}` : ''}</div>
                </div>
              ))}

              <h3>Dispatch</h3>
              {detail.dispatches.length === 0 && <div className="small muted">No team dispatched.</div>}
              {detail.dispatches.map((d) => (
                <div key={d.id} className="list-item">
                  <div className="between"><strong>{d.team_name}</strong><Badge value={d.status} /></div>
                  <div className="small muted">{d.personnel} personnel{d.eta_minutes ? ` · ETA ${d.eta_minutes} min` : ''} · {timeAgo(d.created_at)}</div>
                  <div className="row mt">
                    {['enroute', 'on_scene', 'completed'].map((s) => (
                      <button key={s} className="btn sm" disabled={busy || d.status === s}
                        onClick={() => patchDispatch(d.id, s)}>{s.replace('_', ' ')}</button>
                    ))}
                  </div>
                </div>
              ))}
              {can('dispatch:create') && (
                <div className="mt">
                  <label className="field"><span>Team name</span>
                    <input value={team} onChange={(e) => setTeam(e.target.value)} /></label>
                  <label className="field"><span>ETA (minutes)</span>
                    <input type="number" value={eta} onChange={(e) => setEta(e.target.value)} /></label>
                  <button className="btn primary" disabled={busy} onClick={doDispatch}>Dispatch team</button>
                </div>
              )}

              <h3>Move status</h3>
              <label className="field"><span>Note</span>
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="what is happening on the ground" /></label>
              <div className="row">
                {(NEXT[detail.incident.status] || []).map((s) => (
                  <button key={s} className="btn sm" disabled={busy} onClick={() => move(s)}>{s}</button>
                ))}
                {(NEXT[detail.incident.status] || []).length === 0 && <span className="small muted">terminal state</span>}
              </div>
            </div>
          )}
        </>
      )}
    </Shell>
  )
}

createRoot(document.getElementById('root')).render(
  <AuthProvider allowedRoles={['response_operator']}><ResponseApp /></AuthProvider>,
)
