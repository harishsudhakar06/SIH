import React from 'react'
import { Link } from 'react-router-dom'
import { api, photoUrl } from '../api.js'
import { useAction } from '../hooks.js'
import { BAND_COLORS, RiskBadge, StatusBadge, pct, timeAgo } from './ui.jsx'

function Section({ title, children }) {
  return (
    <div className="section">
      <h4>{title}</h4>
      {children}
    </div>
  )
}

function KVR({ k, v }) {
  return (<><dt>{k}</dt><dd>{v ?? '—'}</dd></>)
}

function interpLines(assessment, evidence, trace, missing) {
  const lines = []
  const accepted = (evidence || []).filter((e) => e.status === 'accepted')
  const rain = accepted.find((e) => String(e.evidence_type).includes('rain'))
  if (rain) {
    lines.push(`Rainfall evidence accepted (probability ${pct(rain.probability)}, weight ${Number(rain.effective_weight || 0).toFixed(2)}) — water infiltration is treated as the primary activation pathway for this slope.`)
  }
  const visual = accepted.filter((e) => String(e.evidence_type).includes('visual') || String(e.evidence_type).includes('report'))
  if (visual.length) {
    lines.push(`${visual.length} visual/report evidence item(s) accepted — observed surface changes contribute to the fused probability.`)
  }
  const sensor = accepted.filter((e) => String(e.evidence_type).includes('sensor'))
  if (sensor.length) {
    lines.push('On-slope sensor readings accepted — tilt/moisture anomalies raise the fused estimate.')
  }
  if (Number(assessment?.inherited_influence) > 0) {
    lines.push(`Connected-slope uplift of +${(assessment.inherited_influence * 100).toFixed(1)} percentage points applied (bounded, from neighbouring assessments).`)
  }
  for (const cav of trace?.caveats || []) lines.push(`Caveat: ${cav}`)
  if (missing?.length) {
    lines.push(`Missing inputs (${missing.join(', ')}) — without them the estimate leans on fewer sources and confidence is reduced.`)
  }
  if (!lines.length) lines.push('No accepted evidence beyond the prior at this time — interpret the score as a baseline state, not an active event.')
  return lines
}

function recLines(assessment, missing) {
  const lines = []
  if (assessment?.recommendation) lines.push(assessment.recommendation)
  if (assessment?.next_action) lines.push(`Next action: ${assessment.next_action}`)
  const band = assessment?.risk_band
  if (band === 'VERY_HIGH') lines.push('Verify in the field within 24 h; keep response teams on standby and pre-position evacuation routes.')
  if (band === 'HIGH') lines.push('Schedule field verification within 48 h; increase monitoring frequency.')
  if (band === 'MODERATE') lines.push('Re-check after the next heavy-rain window; keep citizen reporting enabled.')
  if (band === 'LOW') lines.push('Routine monitoring is sufficient; re-assess when rainfall or new reports arrive.')
  for (const m of missing || []) {
    if (m.includes('satellite')) lines.push('Satellite input unavailable — do not treat vegetation/InSAR change as confirmed.')
    if (m.includes('sensor')) lines.push('No live sensor on this slope — rely on rainfall + reports until one is installed.')
  }
  return lines
}

export default function MapPanel({ sel, detail, loading, error, onClose, onOpenSlope }) {
  const ack = useAction()
  if (!sel) return null

  const header = (
    <div className="between" style={{ alignItems: 'flex-start' }}>
      <div>
        <h2>{detail?.title || (sel.kind === 'slope' ? `${sel.entity.code} — ${sel.entity.name}` : sel.kind === 'report' ? sel.entity.title || sel.entity.report_code : sel.kind === 'alert' ? sel.entity.title : `Incident #${sel.entity.id}`)}</h2>
        <div className="small muted">
          {sel.kind === 'slope' && <>{sel.entity.district}, {sel.entity.state} · <span className="mono">{sel.entity.lat?.toFixed?.(5)}, {sel.entity.lng?.toFixed?.(5)}</span></>}
          {sel.kind === 'report' && <><span className="mono">{sel.entity.report_code}</span> · {timeAgo(sel.entity.created_at)}</>}
          {sel.kind === 'alert' && <><span className="mono">{sel.entity.alert_code}</span> · {sel.entity.status}</>}
          {sel.kind === 'incident' && <><span className="mono">{sel.entity.incident_code || `#${sel.entity.id}`}</span> · {sel.entity.status}</>}
        </div>
      </div>
      <button className="btn" onClick={onClose}>Close</button>
    </div>
  )

  if (loading) return <div className="map-panel">{header}<div className="muted small mt">Loading assessment…</div></div>
  if (error) return <div className="map-panel">{header}<div className="banner mt">{error}</div></div>
  if (!detail) return <div className="map-panel">{header}</div>

  /* ---------------- Report panel ---------------- */
  if (sel.kind === 'report') {
    const { report, media, job } = detail
    return (
      <div className="map-panel">
        {header}
        <Section title="Report">
          <dl className="kv">
            <KVR k="Category" v={<span className="badge tag">{report.category}</span>} />
            <KVR k="Status" v={<StatusBadge status={report.status} />} />
            <KVR k="Description" v={report.description} />
            <KVR k="Coordinates" v={<span className="mono">{report.lat?.toFixed?.(5)}, {report.lng?.toFixed?.(5)} ({report.gps_source})</span>} />
            <KVR k="Observed" v={timeAgo(report.observed_at)} />
            <KVR k="Duplicate" v={report.is_duplicate ? `yes → report #${report.duplicate_of}` : 'no'} />
            <KVR k="Matched slope" v={report.slope_id ? <Link to={`/slopes/${report.slope_id}`}>{report.slope_code} {report.slope_name}</Link> : 'unmatched'} />
          </dl>
        </Section>
        <Section title={`Media (${media?.length || 0})`}>
          {!media?.length && <div className="muted small">No media attached.</div>}
          <div className="panel-media-grid">
            {(media || []).map((m) => (m.kind === 'video'
              ? <video key={m.id} src={photoUrl(m.path)} controls />
              : <img key={m.id} src={photoUrl(m.path)} alt="" loading="lazy" />))}
          </div>
        </Section>
        {job && (
          <Section title="Staged analysis">
            {(job.stages || []).map((s) => (
              <div className="panel-stage" key={s.stage || s.id}>
                <span>{String(s.stage || '').replace(/_/g, ' ').toLowerCase()}</span>
                <span className={s.status}>{s.status}{s.detail ? ` · ${s.detail}` : ''}</span>
              </div>
            ))}
            {job.data_mode && <div className="small muted mt">data_mode: {job.data_mode}</div>}
          </Section>
        )}
        <Section title="Open full record">
          <Link to={`/reports`}>Reports page →</Link>
        </Section>
      </div>
    )
  }

  /* ---------------- Alert panel ---------------- */
  if (sel.kind === 'alert') {
    const { alert, events, incident } = detail
    return (
      <div className="map-panel">
        {header}
        <Section title="Alert">
          <dl className="kv">
            <KVR k="Severity" v={<span className="badge tag" style={{ color: SEVTextColor(alert.severity) }}>{alert.severity}</span>} />
            <KVR k="Band" v={<RiskBadge band={alert.band} />} />
            <KVR k="Status" v={<StatusBadge status={alert.status} />} />
            <KVR k="Message" v={alert.message} />
            <KVR k="Slope" v={alert.slope_id ? <Link to={`/slopes/${alert.slope_id}`}>{alert.slope_code} — {alert.slope_name}</Link> : '—'} />
            <KVR k="Raised" v={timeAgo(alert.created_at)} />
            <KVR k="Acknowledged" v={alert.acknowledged_at ? `${timeAgo(alert.acknowledged_at)} by ${alert.acknowledged_by_name || alert.acknowledged_by}` : '—'} />
          </dl>
          {alert.status === 'open' && (
            <button
              className="btn primary mt"
              disabled={ack.busy}
              onClick={() => ack.run(() => api(`/alerts/${alert.id}/ack`, { method: 'POST', body: {} })).then(onClose)}
            >
              Acknowledge
            </button>
          )}
          {ack.error && <div className="banner mt">{ack.error}</div>}
        </Section>
        {incident && (
          <Section title="Linked incident">
            <Link to="/incidents">{incident.incident_code || `#${incident.id}`} — {incident.title} ({incident.status}) →</Link>
          </Section>
        )}
        <Section title="Event timeline">
          {!events?.length && <div className="muted small">No events yet.</div>}
          <div className="timeline">
            {(events || []).map((e) => (
              <div className="timeline-item" key={e.id}>
                <div className="small"><strong>{e.event_type}</strong> — {e.note || e.details || ''}</div>
                <div className="small muted">{timeAgo(e.created_at)}{e.actor_name ? ` · ${e.actor_name}` : ''}</div>
              </div>
            ))}
          </div>
        </Section>
      </div>
    )
  }

  /* ---------------- Incident panel ---------------- */
  if (sel.kind === 'incident') {
    const { incident, events, dispatches } = detail
    return (
      <div className="map-panel">
        {header}
        <Section title="Incident">
          <dl className="kv">
            <KVR k="Status" v={<StatusBadge status={incident.status} />} />
            <KVR k="Severity" v={incident.severity} />
            <KVR k="Title" v={incident.title} />
            <KVR k="Slope" v={incident.slope_id ? <Link to={`/slopes/${incident.slope_id}`}>{incident.slope_code || `slope #${incident.slope_id}`}</Link> : '—'} />
            <KVR k="Opened" v={timeAgo(incident.created_at)} />
          </dl>
        </Section>
        <Section title="Dispatches">
          {!dispatches?.length && <div className="muted small">No teams dispatched.</div>}
          {(dispatches || []).map((d) => (
            <div key={d.id} className="small" style={{ padding: '4px 0', borderBottom: '1px solid #1c2430' }}>
              <strong>{d.team_name || d.team}</strong> · <StatusBadge status={d.status} /> · {d.role || ''}
            </div>
          ))}
        </Section>
        <Section title="Event timeline">
          <div className="timeline">
            {(events || []).map((e) => (
              <div className="timeline-item" key={e.id}>
                <div className="small"><strong>{e.event_type}</strong> — {e.note || ''}</div>
                <div className="small muted">{timeAgo(e.created_at)}</div>
              </div>
            ))}
          </div>
        </Section>
        <Section title="Manage"><Link to="/incidents">Incidents page →</Link></Section>
      </div>
    )
  }

  /* ---------------- Slope panel: sections A–G ---------------- */
  const { risk, slope: full, trace } = detail
  const a = risk?.assessment || {}
  const evidence = risk?.evidence || []
  const band = a.risk_band || 'UNASSESSED'
  const color = BAND_COLORS[band] || BAND_COLORS.UNASSESSED
  const missing = risk?.missing_inputs || a.missing_inputs || []
  const openAlert = risk?.open_alert
  const neighbors = full?.neighbors || []
  const reports = full?.reports || []
  const fieldObs = full?.field_observations || []
  const history = risk?.history || []
  const connected = trace?.connected_slope

  return (
    <div className="map-panel">
      {header}
      <Section title="A · Location">
        <dl className="kv">
          <KVR k="Slope" v={<><strong>{risk?.slope?.code || sel.entity.code}</strong> {risk?.slope?.name || sel.entity.name}</>} />
          <KVR k="District / State" v={`${risk?.slope?.district || sel.entity.district}, ${risk?.slope?.state || sel.entity.state}`} />
          <KVR k="Coordinates" v={<span className="mono">{(risk?.slope?.lat ?? sel.entity.lat)?.toFixed?.(5)}, {(risk?.slope?.lng ?? sel.entity.lng)?.toFixed?.(5)}</span>} />
          <KVR k="Cluster" v={risk?.slope?.cluster} />
          <KVR k="Slope angle" v={risk?.slope?.slope_angle_deg != null ? `${risk.slope.slope_angle_deg}°` : '—'} />
          <KVR k="Lithology" v={risk?.slope?.lithology} />
          <KVR k="Condition" v={risk?.slope?.condition_status} />
        </dl>
      </Section>

      <Section title="B · Current assessment">
        <div className="row">
          <span className="badge" style={{ background: `${color}22`, color, border: `1px solid ${color}66`, fontSize: 13, padding: '4px 10px' }}>{band.replace('_', ' ')}</span>
          <span className="mono" style={{ fontSize: 16, fontWeight: 700 }}>{pct(a.final_score)}</span>
          <span className="small muted">confidence {pct(a.confidence)}</span>
        </div>
        <dl className="kv mt">
          <KVR k="Method" v={a.method} />
          <KVR k="Engine" v={a.engine_version} />
          <KVR k="Assessed" v={timeAgo(a.created_at)} />
          <KVR k="Uplift" v={a.uplift != null ? `+${(a.uplift * 100).toFixed(1)} pp` : '—'} />
          <KVR k="Ground truth" v={a.ground_truth ? <StatusBadge status="ground_truth" /> : 'no field confirmation yet'} />
        </dl>
      </Section>

      <Section title="C · Why this score (stored evidence)">
        {evidence.length === 0 && <div className="muted small">No evidence rows stored for this assessment.</div>}
        {evidence.map((e, i) => (
          <div key={`${e.evidence_type}-${i}`} className={`evidence-row ${e.status}`} style={{ padding: '6px 8px', marginBottom: 6, background: '#0e141d', borderRadius: 6 }}>
            <div className="between">
              <strong className="small">{e.label || e.evidence_type}</strong>
              <span className="badge tag">{e.status}</span>
            </div>
            <div className="small muted">
              p={pct(e.probability)} · weight {Number(e.effective_weight || 0).toFixed(2)} · {e.source_code}
              {e.observed_at ? ` · ${timeAgo(e.observed_at)}` : ''}
            </div>
            {e.reason && <div className="small" style={{ marginTop: 2 }}>{e.reason}</div>}
          </div>
        ))}
      </Section>

      <Section title="D · Conditional interpretation">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {interpLines(a, evidence, trace, missing).map((l, i) => (
            <li key={i} className="small" style={{ marginBottom: 4 }}>{l}</li>
          ))}
        </ul>
        <div className="small muted mt">Fusion method: {trace?.fusion?.method || a.method || 'belief_fusion'} · prior {pct(trace?.prior?.probability ?? trace?.prior)} → fused {pct(trace?.fusion?.fused_probability ?? a.fused_probability)}</div>
      </Section>

      <Section title="E · Recommendations">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {recLines(a, missing).map((l, i) => (
            <li key={i} style={{ marginBottom: 4 }}>{l}</li>
          ))}
        </ul>
      </Section>

      <Section title="F · Connected-slope context">
        {connected && (
          <div className="small" style={{ marginBottom: 8 }}>
            Inherited influence <strong>+{(Number(connected.influence ?? connected.uplift ?? 0) * 100).toFixed(1)} pp</strong>
            {connected.contributors?.length
              ? <> from {connected.contributors.map((c) => c.code || c.slope_code || c.id).join(', ')}</>
              : connected.basis ? <> — {connected.basis}</> : null}
          </div>
        )}
        {!neighbors.length && <div className="muted small">No graph edges for this slope yet.</div>}
        {neighbors.map((nb) => (
          <div key={nb.id} className="small" style={{ padding: '5px 0', borderBottom: '1px solid #1c2430' }}>
            <Link to={`/slopes/${nb.dst_slope_id === sel.entity.id ? nb.src_slope_id : nb.dst_slope_id}`}>
              <strong>{nb.neighbor_code}</strong> {nb.neighbor_name}
            </Link>
            <div className="muted">
              {nb.rel_type} · strength {Number(nb.strength).toFixed(2)} · {Number(nb.distance_km).toFixed(1)} km
              {nb.validated ? '' : ' · unvalidated'}{nb.directionality ? ` · ${nb.directionality}` : ''}
            </div>
            {nb.basis && <div className="muted" style={{ fontSize: 11 }}>{nb.basis}</div>}
          </div>
        ))}
      </Section>

      <Section title="G · Evidence, history & gap warnings">
        {openAlert && (
          <div className="banner" style={{ marginBottom: 8 }}>
            Open alert <strong>{openAlert.alert_code}</strong> — {openAlert.title} ({openAlert.status})
          </div>
        )}
        <div className="small" style={{ marginBottom: 6 }}><strong>Recent reports ({reports.length})</strong></div>
        {reports.slice(0, 4).map((r) => (
          <div key={r.id} className="row" style={{ marginBottom: 6, gap: 8 }}>
            {r.photo_path && <img className="thumb" src={photoUrl(r.photo_path)} alt="" loading="lazy" />}
            <div className="small">
              <div><strong>{r.title}</strong> {r.is_duplicate ? <span className="muted">(dup)</span> : null}</div>
              <div className="muted">{r.report_code} · {r.category} · {r.status} · {timeAgo(r.created_at)}</div>
            </div>
          </div>
        ))}
        {!reports.length && <div className="muted small">No citizen reports linked.</div>}
        {fieldObs.length > 0 && (
          <>
            <div className="small mt" style={{ marginBottom: 4 }}><strong>Field observations ({fieldObs.length})</strong></div>
            {fieldObs.slice(0, 3).map((o) => (
              <div key={o.id} className="small muted" style={{ padding: '3px 0' }}>
                {o.verification_status} · {timeAgo(o.observed_at)}{o.crack_mm ? ` · crack ${o.crack_mm} mm` : ''}{o.notes ? ` — ${o.notes}` : ''}
              </div>
            ))}
          </>
        )}
        <div className="small mt" style={{ marginBottom: 4 }}><strong>Assessment history</strong></div>
        {history.length === 0 && <div className="muted small">No history yet.</div>}
        {history.slice(0, 5).map((h) => (
          <div key={h.id} className="panel-stage">
            <span>{timeAgo(h.created_at)} · {h.method}</span>
            <span><RiskBadge band={h.risk_band} /> <span className="mono">{pct(h.final_score)}</span></span>
          </div>
        ))}
        {missing.length > 0 && (
          <div className="banner mt">Missing inputs: {missing.join(', ')}. Satellite data is never inferred — treat absence as unknown, not zero.</div>
        )}
        <div className="small muted mt">
          Rainfall & sensor readings are prototype fixtures (data_mode=prototype_fixture) unless marked live. Satellite source: unavailable.
        </div>
      </Section>

      <Section title="Open full record">
        <button className="btn primary" onClick={() => onOpenSlope?.(sel.entity.id)}>Open slope intelligence →</button>
      </Section>
    </div>
  )
}

function SEVTextColor(sev) {
  return SEV_COLORS_MAP[sev] || '#e6edf3'
}
const SEV_COLORS_MAP = { critical: '#c43a3a', high: '#d4723c', moderate: '#d4a843', low: '#4a9f6a' }
