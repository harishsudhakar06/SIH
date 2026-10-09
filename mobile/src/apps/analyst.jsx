import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { AuthProvider } from '../shared/auth.jsx'
import { Badge, Shell, Toast, timeAgo, usePoll } from '../shared/ui.jsx'
import { api } from '../shared/api.js'
import '../shared/styles.css'

function EvidenceRows({ rows }) {
  if (!rows?.length) return <div className="small muted">No evidence rows.</div>
  return (
    <div className="stack" style={{ gap: 6 }}>
      {rows.map((e) => (
        <div key={e.id} className="list-item" style={{ marginBottom: 0, borderLeft: `3px solid ${{ accepted: '#3fb950', rejected: '#f85149', missing: '#8b98a9', duplicate: '#d4a72c' }[e.status]}` }}>
          <div className="between">
            <strong className="small">{e.label}</strong>
            <span className="badge tag">{e.status}</span>
          </div>
          <div className="small muted mono">
            {e.source_code} · sw {e.source_weight} · w {e.effective_weight != null ? Number(e.effective_weight).toFixed(3) : '—'}
            {e.probability != null ? ` · p ${Number(e.probability).toFixed(2)}` : ''}
            {e.freshness_factor != null ? ` · fresh ${Number(e.freshness_factor).toFixed(2)}` : ''}
          </div>
          {(e.reason || e.dependency_adjustment) && (
            <div className="small">{e.reason || e.dependency_adjustment}</div>
          )}
        </div>
      ))}
    </div>
  )
}

function AnalystApp() {
  const [tab, setTab] = useState('run')
  const [toast, setToast] = useState('')
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState(null)
  const [trace, setTrace] = useState(null)
  const [error, setError] = useState('')

  const { data: slopes, refresh: refreshSlopes } = usePoll('/slopes?limit=200', { interval: 10000 })
  const { data: model } = usePoll('/ml/model-info', { interval: 30000 })
  const { data: dq } = usePoll('/data-quality', { interval: 20000 })
  const { data: graph } = usePoll('/graph', { interval: 30000 })

  const runAll = async () => {
    setBusy(true); setError('')
    try {
      const res = await api('/assessments/run', { method: 'POST', body: {}, timeout: 120000 })
      setToast(`Assessed ${res.count} slopes — ${Object.entries(res.bands).map(([k, v]) => `${k} ${v}`).join(', ')}`)
      refreshSlopes()
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const runOne = async (id) => {
    setBusy(true); setError('')
    try {
      const res = await api('/assessments/run', { method: 'POST', body: { slope_id: id } })
      const r = res.results[0]
      setToast(`${r.band} · score ${Math.round(r.final_score * 100)}% · ${r.alert_note || r.recommendation}`)
      refreshSlopes(); if (selected) await open(selected)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const open = async (s) => {
    setSelected(s)
    setTrace(null)
    try {
      const detail = await api(`/slopes/${s.id}`)
      setSelected({ ...s, detail })
      if (detail.assessment) {
        setTrace(await api(`/assessments/${detail.assessment.id}/trace`))
      }
    } catch (e) { setError(e.message) }
  }

  const bands = (slopes?.slopes || []).reduce((acc, s) => {
    const b = s.risk_band || 'UNASSESSED'
    acc[b] = (acc[b] || 0) + 1
    return acc
  }, {})

  return (
    <Shell title="Analyst" blurb="Run the fusion pipeline, inspect evidence, traces, model and data quality."
      roles={['analyst']} active={tab} onSelect={setTab}
      tabs={[{ key: 'run', label: 'Run', icon: '▶' },
        { key: 'slopes', label: 'Evidence', icon: '☰' },
        { key: 'model', label: 'Model', icon: '⚙' },
        { key: 'data', label: 'Data', icon: '◈' }]}>
      <Toast message={toast} onClose={() => setToast('')} />

      {tab === 'run' && (
        <>
          <div className="card">
            <h2>Assessment pipeline</h2>
            <div className="grid2">
              {['LOW', 'MODERATE', 'HIGH', 'VERY_HIGH', 'CONFIRMED', 'UNASSESSED'].filter((b) => bands[b]).map((b) => (
                <div className="stat" key={b}><b>{bands[b]}</b><span>{b.replace('_', ' ')}</span></div>
              ))}
            </div>
            <button className="btn primary mt" disabled={busy} onClick={runAll}>
              {busy ? 'Running fusion over all slopes…' : 'Run assessment for all slopes'}
            </button>
            {error && <div className="banner error">{error}</div>}
            <div className="small muted">Pipeline: evidence collection → freshness/quality/dependency weighting →
              log-odds fusion → one-hop neighbour context → controlled transition → dedup-safe alerting.</div>
          </div>
          <div className="card">
            <h2>Highest scores</h2>
            {(slopes?.slopes || []).slice(0, 10).map((s) => (
              <div className="between" key={s.id} style={{ padding: '7px 0', borderBottom: '1px solid var(--line)' }}>
                <div><strong>{s.code}</strong> <span className="small muted">{s.name}</span></div>
                <div className="row">
                  <span className="mono">{s.final_score != null ? Math.round(s.final_score * 100) + '%' : '—'}</span>
                  <Badge value={s.risk_band || 'UNASSESSED'} />
                  <button className="btn sm" disabled={busy} onClick={() => runOne(s.id)}>run</button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {tab === 'slopes' && (
        <>
          {!selected && (slopes?.slopes || []).map((s) => (
            <div className="list-item" key={s.id}>
              <div className="between">
                <div><strong>{s.code}</strong> <span className="small muted">{s.name}</span>
                  <div className="small muted">{s.district} · {s.lithology} · {s.slope_angle_deg}°</div></div>
                <div className="row">
                  <span className="mono">{s.final_score != null ? Math.round(s.final_score * 100) + '%' : '—'}</span>
                  <Badge value={s.risk_band || 'UNASSESSED'} />
                </div>
              </div>
              <button className="btn sm primary mt" onClick={() => open(s)}>Inspect evidence</button>
            </div>
          ))}

          {selected && (
            <div className="card">
              <div className="between">
                <div><strong>{selected.code} — {selected.name}</strong>
                  <div className="small muted">{selected.detail?.slope?.cluster} · {selected.detail?.slope?.district}</div></div>
                <button className="btn sm" onClick={() => { setSelected(null); setTrace(null) }}>back</button>
              </div>
              {selected.detail?.assessment && (
                <div className="grid2 mt">
                  <div className="stat"><b>{Math.round(selected.detail.assessment.final_score * 100)}%</b>
                    <span>final score · {selected.detail.assessment.risk_band}</span></div>
                  <div className="stat"><b>{Math.round(selected.detail.assessment.confidence * 100)}%</b>
                    <span>confidence</span></div>
                  <div className="stat"><b>{Math.round(selected.detail.assessment.inherited_influence * 100)}%</b>
                    <span>inherited (one-hop)</span></div>
                  <div className="stat"><b>{Math.round(selected.detail.assessment.uplift * 100)}%</b>
                    <span>contextual uplift (cap 15%)</span></div>
                </div>
              )}
              <div className="small muted mt">missing: {(JSON.parse(selected.detail?.assessment?.missing_inputs || '[]')).join('; ') || 'none'}</div>
              <h3>Accepted / rejected evidence</h3>
              <EvidenceRows rows={selected.detail?.evidence} />
              <button className="btn mt" disabled={busy} onClick={() => runOne(selected.id)}>Re-run this slope</button>
              {trace && (
                <>
                  <h3>Audit trace</h3>
                  <pre className="small" style={{ whiteSpace: 'pre-wrap', background: '#0b1017', border: '1px solid var(--line)',
                    borderRadius: 8, padding: 10, maxHeight: 320, overflow: 'auto' }}>
                    {JSON.stringify(trace, null, 2)}
                  </pre>
                </>
              )}
            </div>
          )}
        </>
      )}

      {tab === 'model' && (
        <div className="card">
          <h2>Local ML model</h2>
          {model ? (
            <dl className="kv">
              <dt>Name</dt><dd className="mono">{model.model.model_name}</dd>
              <dt>Version</dt><dd className="mono">{model.model.version}</dd>
              <dt>Trained</dt><dd>{timeAgo(model.model.trained_at)}</dd>
              <dt>Data mode</dt><dd><Badge value={model.model.data_mode} kind="tag" /></dd>
              <dt>Accuracy</dt><dd className="mono">{model.model.metrics.accuracy}</dd>
              <dt>ROC AUC</dt><dd className="mono">{model.model.metrics.roc_auc} (CV {model.model.metrics.cv_roc_auc_mean} ± {model.model.metrics.cv_roc_auc_std})</dd>
              <dt>Features</dt><dd className="small">{model.model.features.join(', ')}</dd>
            </dl>
          ) : <div className="small muted">Model not trained yet.</div>}
          <div className="banner info mt">Metrics are measured on a synthetic physically-informed hold-out set —
            prototype only, not field-validated. Inference runs locally in Python.</div>
        </div>
      )}

      {tab === 'data' && (
        <>
          <div className="card">
            <h2>Data quality</h2>
            {dq && (
              <div className="grid2">
                <div className="stat"><b>{dq.feeds.rainfall.rows}</b><span>rainfall fixture rows</span></div>
                <div className="stat"><b>{dq.feeds.sensors.rows}</b><span>sensor fixture rows</span></div>
                <div className="stat"><b>{dq.feeds.satellite.available}/{dq.feeds.satellite.rows}</b><span>satellite available</span></div>
                <div className="stat"><b>{dq.feeds.citizen_reports.rows}</b><span>live reports</span></div>
              </div>
            )}
            <div className="banner mt">{dq?.honesty}</div>
          </div>
          <div className="card">
            <h2>Connected-slope graph</h2>
            <div className="grid2">
              <div className="stat"><b>{graph?.nodes?.length || 0}</b><span>nodes</span></div>
              <div className="stat"><b>{graph?.edges?.length || 0}</b><span>documented edges</span></div>
            </div>
            <div className="small muted mt">
              {['spatial_adjacency', 'rainfall_similarity', 'geology_similarity', 'hydrology_downslope'].map((t) => (
                <div key={t}>{t}: {graph?.edges?.filter((e) => e.rel_type === t).length || 0} edges</div>
              ))}
            </div>
            <div className="banner info mt">Hydrology edges are elevation-gradient approximations (validated = false);
              no physical landslide propagation is claimed.</div>
          </div>
        </>
      )}
    </Shell>
  )
}

createRoot(document.getElementById('root')).render(
  <AuthProvider allowedRoles={['analyst']}><AnalystApp /></AuthProvider>,
)
