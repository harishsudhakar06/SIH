import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AuthProvider, useAuth } from '../shared/auth.jsx'
import { Badge, Shell, Toast, timeAgo, usePoll } from '../shared/ui.jsx'
import { api } from '../shared/api.js'
import '../shared/styles.css'

function CommunityApp() {
  const { user } = useAuth()
  const [tab, setTab] = useState('board')
  const [toast, setToast] = useState('')
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState({ title: '', body: '' })
  const [busy, setBusy] = useState(false)

  const { data: slopes, refresh: refreshSlopes } = usePoll('/slopes?limit=200', { interval: 10000 })
  const { data: alerts, refresh: refreshAlerts } = usePoll('/alerts?limit=30', { interval: 5000 })

  const districtSlopes = (slopes?.slopes || []).filter((s) => !user?.district || s.district === user.district)
  const districtAlerts = (alerts?.alerts || []).filter((a) => !user?.district || a.slope_district === user.district)
  const counts = districtSlopes.reduce((acc, s) => {
    const b = s.risk_band || 'UNASSESSED'
    acc[b] = (acc[b] || 0) + 1
    return acc
  }, {})

  const ack = async (id) => {
    setBusy(true)
    try {
      await api(`/alerts/${id}/ack`, { method: 'POST', body: { note: note || 'community leader acknowledged' } })
      setToast('Alert acknowledged — an incident is now open for the response team')
      setNote(''); refreshAlerts()
    } catch (e) { setToast(e.message) } finally { setBusy(false) }
  }

  const broadcast = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const res = await api('/notifications/broadcast', { method: 'POST', body: { ...msg, role_scope: 'citizen', district: user?.district } })
      setToast(`Community warning sent to citizens in ${res.district || 'your district'}`)
      setMsg({ title: '', body: '' })
    } catch (err) { setToast(err.message) } finally { setBusy(false) }
  }

  return (
    <Shell title="Community" blurb="Village risk board, alert acknowledgement and community warnings."
      roles={['community_leader']} active={tab} onSelect={setTab}
      tabs={[{ key: 'board', label: 'Risk board', icon: '◈' },
        { key: 'alerts', label: 'Alerts', icon: '⚠' },
        { key: 'warn', label: 'Warn', icon: '📢' }]}>
      <Toast message={toast} kind={toast.toLowerCase().includes('alert') ? 'alert' : ''} onClose={() => setToast('')} />

      {tab === 'board' && (
        <>
          <div className="card">
            <h2>{user?.district || 'District'} risk board</h2>
            <div className="grid2">
              {['LOW', 'MODERATE', 'HIGH', 'VERY_HIGH', 'CONFIRMED', 'UNASSESSED'].filter((b) => counts[b]).map((b) => (
                <div className="stat" key={b}><b>{counts[b]}</b><span>{b.replace('_', ' ')}</span></div>
              ))}
            </div>
            <div className="small muted mt">Bands are produced by the laptop's Belief Fusion Engine from
              rainfall, terrain, reports, sensors and one-hop neighbour context.</div>
          </div>
          <div className="card">
            <h2>Slopes</h2>
            {districtSlopes.slice(0, 40).map((s) => (
              <div className="between" key={s.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--line)' }}>
                <div><strong>{s.code}</strong><div className="small muted">{s.name}</div></div>
                <div className="row">
                  <span className="mono">{s.final_score != null ? Math.round(s.final_score * 100) + '%' : '—'}</span>
                  <Badge value={s.risk_band || 'UNASSESSED'} />
                </div>
              </div>
            ))}
            <button className="btn mt" onClick={refreshSlopes}>Refresh board</button>
          </div>
        </>
      )}

      {tab === 'alerts' && (
        <>
          <label className="field"><span>Note saved with your acknowledgement</span>
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="village loudspeaker activated…" /></label>
          {districtAlerts.map((a) => (
            <div className="list-item" key={a.id}>
              <div className="between"><div className="title">{a.title}</div><Badge value={a.band} /></div>
              <div className="small muted">{a.message}</div>
              <div className="small muted mono">{a.alert_code} · {a.status} · {timeAgo(a.created_at)}</div>
              <div className="row mt">
                <button className="btn sm primary" disabled={busy || a.status !== 'open'}
                  onClick={() => ack(a.id)}>Acknowledge</button>
                <Badge value={a.status} />
              </div>
            </div>
          ))}
          {districtAlerts.length === 0 && <div className="muted">No alerts for your district.</div>}
        </>
      )}

      {tab === 'warn' && (
        <form onSubmit={broadcast} className="card">
          <h2>Send a community warning</h2>
          <div className="banner info">Delivered as an in-app notification to citizens in your district,
            persisted in the shared database.</div>
          <label className="field"><span>Headline</span>
            <input value={msg.title} onChange={(e) => setMsg({ ...msg, title: e.target.value })}
              placeholder="Heavy rain: avoid the Pynursla road" required /></label>
          <label className="field"><span>Message</span>
            <textarea rows={4} value={msg.body} onChange={(e) => setMsg({ ...msg, body: e.target.value })}
              placeholder="Move livestock to higher ground; keep the evacuation route clear." required /></label>
          <button className="btn primary" disabled={busy}>{busy ? 'Sending…' : 'Broadcast warning'}</button>
        </form>
      )}
    </Shell>
  )
}

createRoot(document.getElementById('root')).render(
  <AuthProvider allowedRoles={['community_leader']}><CommunityApp /></AuthProvider>,
)
