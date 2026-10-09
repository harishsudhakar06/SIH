import React from 'react'
import { usePoll } from '../hooks.js'
import { timeAgo, Empty, pct } from '../components/ui.jsx'
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export default function DataPage() {
  const { data: dq } = usePoll('/data-quality', { interval: 15000 })
  const { data: stations } = usePoll('/rainfall/stations', { interval: 20000 })
  const { data: rain } = usePoll('/rainfall?limit=120', { interval: 20000 })
  const { data: sensors } = usePoll('/sensors?limit=150', { interval: 20000 })
  const { data: sat } = usePoll('/satellite', { interval: 30000 })
  const { data: sources } = usePoll('/sources', { interval: 60000 })
  const { data: tiles } = usePoll('/tiles/status', { interval: 60000 })
  const { data: geo } = usePoll('/geo/status', { interval: 60000 })

  const seriesByStation = {}
  for (const o of rain?.observations || []) {
    seriesByStation[o.station_code] ||= []
    seriesByStation[o.station_code].push({ t: new Date(o.observed_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }), mm24: o.mm_24h, mm72: o.mm_72h })
  }
  for (const k of Object.keys(seriesByStation)) seriesByStation[k].reverse()

  return (
    <div className="stack">
      <div className="banner">
        Honesty panel — rainfall and sensor values are PROTOTYPE FIXTURES (data_mode=prototype_fixture),
        not live measurements. The satellite feed is wired into the pipeline but has no provider:
        every row is status=unavailable and is reported as a missing input in every assessment.
      </div>

      <div className="grid cols-4">
        {dq ? (
          <>
            <div className="card"><h3>Rainfall feed</h3>
              <div className="stat-value">{dq.feeds.rainfall.rows}</div>
              <div className="stat-label">observations · latest {timeAgo(dq.feeds.rainfall.latest)}</div></div>
            <div className="card"><h3>Sensor feed</h3>
              <div className="stat-value">{dq.feeds.sensors.rows}</div>
              <div className="stat-label">readings · latest {timeAgo(dq.feeds.sensors.latest)}</div></div>
            <div className="card"><h3>Satellite feed</h3>
              <div className="stat-value">{dq.feeds.satellite.available}/{dq.feeds.satellite.rows}</div>
              <div className="stat-label">available rows</div></div>
            <div className="card"><h3>Assessments</h3>
              <div className="stat-value">{dq.feeds.assessments.rows}</div>
              <div className="stat-label">latest {timeAgo(dq.feeds.assessments.latest)}</div></div>
          </>
        ) : <div className="muted">Loading quality summary…</div>}
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h3>Local basemap tiles</h3>
          {tiles ? (
            <dl className="kv">
              <dt>Mode</dt><dd><span className="badge tag">{tiles.mode}</span></dd>
              <dt>Cached tiles</dt><dd className="mono">{tiles.cache_tiles} (zooms {tiles.cache_zooms?.join(', ') || '—'})</dd>
              <dt>Provider</dt><dd className="small">{tiles.provider} — no paid map API</dd>
              <dt>Note</dt><dd className="small muted">{tiles.note}</dd>
            </dl>
          ) : <div className="muted small">Loading tile status…</div>}
        </div>
        <div className="card">
          <h3>State boundary layer</h3>
          {geo ? (
            <dl className="kv">
              <dt>Layer</dt><dd><span className="badge tag">{geo.states_layer}</span></dd>
              <dt>States</dt><dd className="mono">{geo.states_count} · {geo.bytes?.toLocaleString()} bytes</dd>
              <dt>Mode</dt><dd className="small">{geo.data_mode}</dd>
              <dt>Source</dt><dd className="small muted">{geo.source}</dd>
            </dl>
          ) : <div className="muted small">Loading geo status…</div>}
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h3>Gauge stations (24h rainfall trend)</h3>
          {(stations?.stations || []).slice(0, 4).map((s) => (
            <div key={s.id} style={{ marginBottom: 10 }}>
              <div className="between small">
                <strong>{s.station_name}</strong>
                <span className="mono muted">24h {s.mm_24h} mm · 72h {s.mm_72h} mm</span>
              </div>
              <ResponsiveContainer width="100%" height={70}>
                <LineChart data={seriesByStation[s.station_code] || []}>
                  <XAxis dataKey="t" hide />
                  <YAxis hide domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ background: '#141b25', border: '1px solid #26303f' }} />
                  <Line type="monotone" dataKey="mm24" stroke="#58a6ff" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ))}
          <div className="small muted">Source weight {sources?.sources?.find((x) => x.code === 'rainfall_gauge')?.source_weight} · half-life {sources?.sources?.find((x) => x.code === 'rainfall_gauge')?.half_life_h}h</div>
        </div>

        <div className="card">
          <h3>Satellite observations</h3>
          <div className="banner info" style={{ marginBottom: 10 }}>
            Pipeline is wired end-to-end but reports "provider not connected" — never fabricated values.
          </div>
          <table>
            <thead><tr><th>Slope</th><th>Product</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              {(sat?.observations || []).slice(0, 8).map((o) => (
                <tr key={o.id}>
                  <td className="mono">#{o.slope_id}</td>
                  <td className="small">{o.product}</td>
                  <td><span className="badge tag">{o.status}</span></td>
                  <td className="small muted">{o.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(sat?.observations || []).length === 0 && <Empty>No satellite rows.</Empty>}
        </div>
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h3>Recent sensor readings (prototype)</h3>
          <table>
            <thead><tr><th>Slope</th><th>Sensor</th><th>Type</th><th>Value</th><th>Observed</th></tr></thead>
            <tbody>
              {(sensors?.readings || []).slice(0, 12).map((s) => (
                <tr key={s.id}>
                  <td>{s.slope_code}</td>
                  <td className="mono small">{s.sensor_uid}</td>
                  <td className="small">{s.sensor_type}</td>
                  <td className="mono">{s.value} {s.unit}</td>
                  <td className="small muted">{timeAgo(s.observed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(sensors?.readings || []).length === 0 && <Empty>No sensor readings.</Empty>}
        </div>

        <div className="card">
          <h3>Evidence source registry</h3>
          <table>
            <thead><tr><th>Source</th><th>Kind</th><th>Weight</th><th>Half-life</th><th>Mode</th></tr></thead>
            <tbody>
              {(sources?.sources || []).map((s) => (
                <tr key={s.code}>
                  <td>{s.name}<div className="small muted">{s.notes}</div></td>
                  <td className="small">{s.kind}</td>
                  <td className="mono">{s.source_weight}</td>
                  <td className="mono small">{s.freshness_half_life_h}h</td>
                  <td><span className="badge tag">{s.data_mode}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          {sources && (
            <div className="small muted mt">
              <div><strong>effective weight</strong> = {sources.fusion_formula.effective_weight}</div>
              <div><strong>fusion</strong> = {sources.fusion_formula.fusion}</div>
              <div><strong>dependency discount ρ</strong> = {sources.fusion_formula.dependency_rho}</div>
            </div>
          )}
        </div>
      </div>

      {dq && (
        <div className="card">
          <h3>Data-quality flags</h3>
          <ul className="small">
            {dq.flags.filter(Boolean).map((f, i) => <li key={i}>{f}</li>)}
            <li>{dq.honesty}</li>
          </ul>
        </div>
      )}
    </div>
  )
}
