import React, { useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api.js'
import { useAction, usePoll } from '../hooks.js'
import { RiskBadge, Stat, pct, timeAgo, Empty } from '../components/ui.jsx'
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

function EvidenceTable({ rows }) {
  if (!rows?.length) return <Empty>No evidence rows stored.</Empty>
  return (
    <table>
      <thead>
        <tr><th>Evidence</th><th>Status</th><th>Weight</th><th>P</th><th>Age</th><th>Reason / adjustment</th></tr>
      </thead>
      <tbody>
        {rows.map((e) => (
          <tr key={e.id} className={`evidence-row ${e.status}`}>
            <td>
              <strong>{e.label || e.evidence_type}</strong>
              <div className="small muted mono">{e.source_code} · sw {e.source_weight}</div>
              <div className="small muted">{e.provenance}</div>
            </td>
            <td><span className={`badge tag`}>{e.status}</span></td>
            <td className="mono">{e.effective_weight != null ? Number(e.effective_weight).toFixed(3) : '—'}
              <div className="small muted mono">c{Number(e.confidence).toFixed(2)} f{e.freshness_factor != null ? Number(e.freshness_factor).toFixed(2) : '—'}</div>
            </td>
            <td className="mono">{e.probability != null ? Number(e.probability).toFixed(3) : '—'}</td>
            <td className="small muted">{e.observed_at ? timeAgo(e.observed_at) : 'static'}</td>
            <td className="small">{e.reason || e.dependency_adjustment || <span className="muted">accepted</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function SlopeDetailPage() {
  const { id } = useParams()
  const { data, refresh } = usePoll(`/slopes/${id}`, { interval: 7000 })
  const { data: history } = usePoll(`/slopes/${id}/history`, { interval: 15000 })
  const [trace, setTrace] = useState(null)
  const [tab, setTab] = useState('evidence')
  const { busy, run, error } = useAction()

  const loadTrace = async () => {
    if (trace) { setTrace(null); return }
    const res = await api(`/assessments/${data.assessment.id}/trace`)
    setTrace(res)
  }

  const runAssessment = () => run(() => api('/assessments/run', { method: 'POST', body: { slope_id: Number(id) } }))
    .then(() => { refresh() })

  if (!data) return <div className="muted">Loading slope…</div>
  const { slope, assessment, evidence, neighbors, reports, field_observations, nearest_rainfall, sensors } = data
  const series = (history?.assessments || []).slice().reverse().map((a) => ({
    t: new Date(a.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    fused: Number(a.fused_probability),
    final: Number(a.final_score),
    inherited: Number(a.inherited_influence),
  }))

  return (
    <div className="stack">
      <div className="card">
        <div className="between">
          <div>
            <div className="row">
              <strong style={{ fontSize: 17 }}>{slope.code} — {slope.name}</strong>
              <RiskBadge band={assessment?.risk_band} />
            </div>
            <div className="small muted">
              {slope.cluster} · {slope.district}, {slope.state} · {slope.lat.toFixed(4)}, {slope.lng.toFixed(4)}
              · {slope.slope_angle_deg}° slope · {slope.lithology} · {slope.land_use}
              {slope.road_cut ? ' · road cut' : ''} · {slope.historical_incidents} historical incidents
            </div>
          </div>
          <div className="btn-row">
            <button className="btn" disabled={busy} onClick={runAssessment}>Run assessment</button>
            {assessment && <button className="btn primary" onClick={loadTrace}>{trace ? 'Hide' : 'View'} full audit trace</button>}
          </div>
        </div>
        {error && <div className="banner mt">{error}</div>}
        {assessment && (
          <div className="grid cols-4 mt">
            <Stat label="Final score (fusion + context)" value={pct(assessment.final_score)}
              sub={`direct ${pct(assessment.direct_score)} · inherited ${pct(assessment.inherited_influence)} · uplift ${pct(assessment.uplift)}`} />
            <Stat label="Fused probability" value={pct(assessment.fused_probability)}
              sub={`prior ${pct(assessment.prior_probability)} · ${assessment.method}`} />
            <Stat label="Confidence" value={pct(assessment.confidence)}
              sub={`${assessment.accepted_count} accepted · ${assessment.rejected_count} rejected`} />
            <Stat label="Next action" value={<span style={{ fontSize: 14 }}>{assessment.next_action}</span>}
              sub={assessment.recommendation} />
          </div>
        )}
        {assessment && (
          <div className="row mt">
            <span className="pill">missing: {JSON.parse(assessment.missing_inputs || '[]').join('; ') || 'none'}</span>
            <span className="pill">flags: {JSON.parse(assessment.flags || '[]').join(', ') || 'none'}</span>
            <span className="pill warn">assessed {timeAgo(assessment.created_at)} · engine {assessment.engine_version}</span>
          </div>
        )}
      </div>

      {trace && (
        <div className="card">
          <h3>Audit trace — evidence considered, weights, dependencies, caveats</h3>
          <div className="trace-block"><pre>{JSON.stringify(trace, null, 2)}</pre></div>
        </div>
      )}

      <div className="card">
        <div className="tabs">
          {[['evidence', `Evidence (${evidence?.length || 0})`],
            ['connected', `Connected slopes (${neighbors?.length || 0})`],
            ['history', 'Risk history'],
            ['reports', `Reports & field (${reports?.length || 0}/${field_observations?.length || 0})`],
            ['context', 'Rainfall & sensors']].map(([k, label]) => (
              <button key={k} className={`tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>{label}</button>
            ))}
        </div>

        {tab === 'evidence' && <EvidenceTable rows={evidence} />}

        {tab === 'connected' && (
          <>
            <div className="banner info" style={{ marginBottom: 10 }}>
              Bounded one-hop context only: a risky neighbour raises this slope's score by at most 15% and can never
              confirm a landslide here. Dashed/unvalidated edges are documented hypotheses, not validated physics.
            </div>
            <table>
              <thead><tr><th>Neighbour</th><th>Relationship</th><th>Strength</th><th>Direction</th><th>Basis</th></tr></thead>
              <tbody>
                {(neighbors || []).map((n) => (
                  <tr key={n.id}>
                    <td>{n.neighbor_code} <span className="muted small">{n.neighbor_name}</span></td>
                    <td><span className="badge tag">{n.rel_type}</span></td>
                    <td className="mono">{Number(n.strength).toFixed(2)}</td>
                    <td className="small muted">{n.directionality} {n.directionality === 'directed' ? `(${n.src_slope_id === slope.id ? 'outflow' : 'inflow'})` : ''}</td>
                    <td className="small muted">{n.basis}{n.validated ? '' : ' · unvalidated'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {assessment && (
              <div className="small muted mt">
                Last propagation used {JSON.parse((history?.assessments || [])[0]?.flags || '[]').includes('no_fresh_neighbors') ? 'no fresh neighbours' : 'fresh neighbour assessments'}.
                Inherited influence = weighted average of one-hop neighbour scores, decayed by distance and freshness.
              </div>
            )}
          </>
        )}

        {tab === 'history' && (
          series.length === 0 ? <Empty>No assessment history yet.</Empty> : (
            <>
              <ResponsiveContainer width="100%" height={240}>
                <AreaChart data={series}>
                  <XAxis dataKey="t" stroke="#8b98a9" fontSize={11} />
                  <YAxis domain={[0, 1]} stroke="#8b98a9" fontSize={11} />
                  <Tooltip contentStyle={{ background: '#141b25', border: '1px solid #26303f' }} />
                  <Area type="monotone" dataKey="final" stroke="#f0883e" fill="#f0883e33" strokeWidth={2} name="final score" />
                  <Area type="monotone" dataKey="fused" stroke="#2f81f7" fill="none" strokeDasharray="4 4" name="fused (direct)" />
                  <Area type="monotone" dataKey="inherited" stroke="#a371f7" fill="none" name="inherited" />
                </AreaChart>
              </ResponsiveContainer>
              <div className="small muted">Solid = final (fusion + bounded uplift), dashed = direct fusion, purple = inherited context.</div>
            </>
          )
        )}

        {tab === 'reports' && (
          <div className="grid cols-2">
            <div>
              <h3>Citizen reports</h3>
              {(reports || []).map((r) => (
                <div key={r.id} className="feed-item">
                  <span className="feed-kind">{r.category}</span>
                  <div>
                    <strong>{r.title}</strong> {r.is_duplicate ? <span className="badge tag">duplicate</span> : null}
                    <div className="small muted">{r.report_code} · {timeAgo(r.created_at)} · gps {r.gps_source}{r.photo_path ? ' · photo attached' : ''}</div>
                  </div>
                </div>
              ))}
              {(!reports || reports.length === 0) && <Empty>No reports for this slope.</Empty>}
            </div>
            <div>
              <h3>Field verification</h3>
              {(field_observations || []).map((o) => (
                <div key={o.id} className="feed-item">
                  <span className="feed-kind">{o.verification_status}</span>
                  <div>
                    <div>{o.notes || '—'}</div>
                    <div className="small muted">
                      crack {o.crack_mm ?? '—'} mm · seepage {o.seepage ? 'yes' : 'no'} · fresh scarp {o.fresh_scarp ? 'yes' : 'no'}
                      · {timeAgo(o.observed_at)}
                    </div>
                  </div>
                </div>
              ))}
              {(!field_observations || field_observations.length === 0) && <Empty>No field observations yet.</Empty>}
            </div>
          </div>
        )}

        {tab === 'context' && (
          <div className="grid cols-2">
            <div>
              <h3>Nearest rainfall gauges (prototype fixture)</h3>
              <table>
                <thead><tr><th>Station</th><th>24h</th><th>72h</th><th>Dist</th><th>Observed</th></tr></thead>
                <tbody>
                  {(nearest_rainfall || []).map((r) => (
                    <tr key={r.id}>
                      <td>{r.station_name}<div className="small muted mono">{r.station_code}</div></td>
                      <td className="mono">{r.mm_24h} mm</td>
                      <td className="mono">{r.mm_72h} mm</td>
                      <td className="mono">{r.distance_km} km</td>
                      <td className="small muted">{timeAgo(r.observed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h3>Ground sensors (prototype fixture)</h3>
              {(sensors || []).length === 0 && <Empty>No sensors on this slope.</Empty>}
              <table>
                <thead><tr><th>Sensor</th><th>Type</th><th>Value</th><th>Observed</th></tr></thead>
                <tbody>
                  {(sensors || []).map((s) => (
                    <tr key={s.id}>
                      <td className="mono">{s.sensor_uid}</td>
                      <td>{s.sensor_type}</td>
                      <td className="mono">{s.value} {s.unit}</td>
                      <td className="small muted">{timeAgo(s.observed_at)} · {s.data_mode}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
