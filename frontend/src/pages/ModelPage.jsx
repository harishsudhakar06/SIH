import React, { useState } from 'react'
import { api } from '../api.js'
import { useAction, usePoll } from '../hooks.js'
import { timeAgo, Empty, pct } from '../components/ui.jsx'
import { useAuth } from '../auth.jsx'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

export default function ModelPage() {
  const { can } = useAuth()
  const { data: model, refresh: refreshModel, error: modelError } = usePoll('/ml/model-info', { interval: 30000 })
  const { data: runs } = usePoll('/ml/runs', { interval: 30000 })
  const { data: audit } = usePoll('/audit?limit=60', { interval: 20000, enabled: can('audit:view') })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const train = async () => {
    setBusy(true); setMsg('')
    try {
      const res = await api('/ml/train', { method: 'POST', timeout: 180000 })
      setMsg(`Trained ${res.model.version} — ROC AUC ${res.model.metrics.roc_auc} (synthetic hold-out)`)
      refreshModel()
    } catch (e) {
      setMsg(e.message)
    } finally { setBusy(false) }
  }

  const metrics = model?.model?.metrics
  const metricData = metrics ? [
    { k: 'accuracy', v: metrics.accuracy }, { k: 'precision', v: metrics.precision },
    { k: 'recall', v: metrics.recall }, { k: 'f1', v: metrics.f1 },
    { k: 'roc_auc', v: metrics.roc_auc }, { k: 'cv_roc_auc', v: metrics.cv_roc_auc_mean },
  ] : []

  return (
    <div className="stack">
      <div className="banner">
        All inference runs locally in Python (scikit-learn + joblib). No OpenAI/Claude/Gemini or external model API
        is used. Training data is a synthetic, physically-informed prototype dataset — metrics below are measured on
        that synthetic hold-out set and are NOT field-validated accuracy claims.
      </div>

      <div className="grid cols-2">
        <div className="card">
          <h3>Susceptibility model</h3>
          {modelError && <div className="banner">{modelError}</div>}
          {model ? (
            <>
              <dl className="kv">
                <dt>Model</dt><dd className="mono">{model.model.model_name}</dd>
                <dt>Version</dt><dd className="mono">{model.model.version}</dd>
                <dt>Trained</dt><dd>{timeAgo(model.model.trained_at)}</dd>
                <dt>Data mode</dt><dd><span className="badge tag">{model.model.data_mode}</span></dd>
                <dt>Features</dt><dd className="small">{model.model.features.join(', ')}</dd>
              </dl>
              <ResponsiveContainer width="100%" height={200} className="mt">
                <BarChart data={metricData}>
                  <XAxis dataKey="k" stroke="#8b98a9" fontSize={11} />
                  <YAxis domain={[0, 1]} stroke="#8b98a9" fontSize={11} />
                  <Tooltip contentStyle={{ background: '#141b25', border: '1px solid #26303f' }} />
                  <Bar dataKey="v" fill="#2f81f7" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
              <div className="small muted">{model.model.notes}</div>
            </>
          ) : <Empty>Model not trained yet.</Empty>}
          {can('ml:train') && (
            <div className="btn-row mt">
              <button className="btn primary" disabled={busy} onClick={train}>
                {busy ? 'Training…' : 'Retrain model locally'}
              </button>
              {msg && <span className="small muted">{msg}</span>}
            </div>
          )}
        </div>

        <div className="card">
          <h3>Training runs</h3>
          <table>
            <thead><tr><th>Version</th><th>ROC AUC</th><th>Accuracy</th><th>Rows</th><th>Trained</th></tr></thead>
            <tbody>
              {(runs?.runs || []).map((r) => (
                <tr key={r.id}>
                  <td className="mono small">{r.version}</td>
                  <td className="mono">{r.metrics.roc_auc ?? '—'}</td>
                  <td className="mono">{r.metrics.accuracy ?? '—'}</td>
                  <td className="mono">{r.n_samples}</td>
                  <td className="small muted">{timeAgo(r.trained_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(runs?.runs || []).length === 0 && <Empty>No training runs recorded.</Empty>}
        </div>
      </div>

      <div className="card">
        <h3>Audit log (analyst/admin)</h3>
        {!can('audit:view') && <Empty>Requires analyst or admin role.</Empty>}
        {can('audit:view') && (
          <table>
            <thead><tr><th>When</th><th>User</th><th>Action</th><th>Entity</th><th>Detail</th></tr></thead>
            <tbody>
              {(audit?.entries || []).map((e) => (
                <tr key={e.id}>
                  <td className="small muted">{timeAgo(e.created_at)}</td>
                  <td className="small">{e.username || 'system'}</td>
                  <td className="mono small">{e.action}</td>
                  <td className="small muted">{e.entity} #{e.entity_id}</td>
                  <td className="small mono" style={{ maxWidth: 380, overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.detail_json || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
