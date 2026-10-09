import React from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { usePoll } from '../hooks.js'
import { BAND_COLORS, RiskBadge, Stat, timeAgo, pct, Empty } from '../components/ui.jsx'
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const BAND_ORDER = ['LOW', 'MODERATE', 'HIGH', 'VERY_HIGH', 'CONFIRMED', 'UNASSESSED']

export default function DashboardPage() {
  const { data, error, lastUpdated } = usePoll('/dashboard', { interval: 5000 })
  const navigate = useNavigate()
  if (error) return <div className="banner">Cannot load dashboard: {error}</div>
  if (!data) return <div className="muted">Loading live dashboard…</div>

  const bandData = BAND_ORDER.filter((b) => data.bands[b]).map((b) => ({ band: b, count: data.bands[b] }))
  const openAlerts = data.open_alerts || []
  const totalAlerts = Object.values(data.alerts_by_status || {}).reduce((a, b) => a + b, 0)

  return (
    <div className="stack">
      <div className="grid cols-4">
        <Stat label="Slopes by risk band" value={Object.values(data.bands).reduce((a, b) => a + b, 0)}
          sub={BAND_ORDER.filter((b) => data.bands[b]).map((b) => `${b.replace('_', ' ')} ${data.bands[b]}`).join(' · ')} />
        <Stat label="Open alerts" value={openAlerts.length}
          sub={`${totalAlerts} total · deduped by slope|band|hour`} />
        <Stat label="Incidents" value={Object.values(data.incidents_by_status || {}).reduce((a, b) => a + b, 0)}
          sub={Object.entries(data.incidents_by_status || {}).map(([k, v]) => `${k} ${v}`).join(' · ') || 'none yet'} />
        <Stat label="Activity (24h)" value={data.reports_24h}
          sub={`${data.assessments_24h} assessments · last update ${timeAgo(data.generated_at)}`} />
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h3>Slope risk distribution</h3>
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={bandData}>
              <XAxis dataKey="band" stroke="#8b98a9" fontSize={11} />
              <YAxis stroke="#8b98a9" fontSize={11} allowDecimals={false} />
              <Tooltip contentStyle={{ background: '#141b25', border: '1px solid #26303f' }} />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {bandData.map((entry) => (
                  <Cell key={entry.band} fill={BAND_COLORS[entry.band] || BAND_COLORS.UNASSESSED} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <div className="small muted">Bands come from Belief Fusion + bounded connected-slope uplift.</div>
        </div>

        <div className="card">
          <h3>Highest-risk slopes (latest assessment)</h3>
          <table>
            <thead><tr><th>Slope</th><th>Band</th><th>Score</th><th>Conf</th><th>Action</th></tr></thead>
            <tbody>
              {data.top_risks.slice(0, 8).map((r) => (
                <tr key={r.slope_id} className="clickable" onClick={() => navigate(`/slopes/${r.slope_id}`)}>
                  <td><strong>{r.code}</strong><div className="small muted">{r.name}</div></td>
                  <td><RiskBadge band={r.risk_band} /></td>
                  <td className="mono">{pct(r.final_score)}</td>
                  <td className="mono">{pct(r.confidence)}</td>
                  <td className="small muted" style={{ maxWidth: 240 }}>{r.recommendation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h3>Open alerts <Link to="/alerts" className="small">(manage)</Link></h3>
          {openAlerts.length === 0 && <Empty>No open alerts — pipelines are clear.</Empty>}
          {openAlerts.map((a) => (
            <div key={a.id} className="feed-item">
              <span className="feed-kind">{a.severity}</span>
              <div>
                <Link to={`/slopes/${a.slope_id}`}>{a.title}</Link>
                <div className="small muted">{a.status} · {timeAgo(a.created_at)} · {a.alert_code}</div>
              </div>
            </div>
          ))}
        </div>
        <div className="card">
          <h3>Live activity feed</h3>
          {data.activity.length === 0 && <Empty>No activity yet.</Empty>}
          {data.activity.slice(0, 14).map((a, i) => (
            <div className="feed-item" key={`${a.kind}-${a.id}-${i}`}>
              <span className="feed-kind">{a.kind}</span>
              <div>
                <div>#{a.id} — {a.title}</div>
                <div className="small muted">{a.status} · {timeAgo(a.created_at)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
