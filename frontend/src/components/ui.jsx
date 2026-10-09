import React from 'react'

export const BAND_COLORS = {
  LOW: '#4a9f6a',
  MODERATE: '#d4a843',
  HIGH: '#d4723c',
  VERY_HIGH: '#c43a3a',
  CONFIRMED: '#8f2020',
  UNASSESSED: '#8b949e',
}

export function RiskBadge({ band }) {
  const b = band || 'UNASSESSED'
  return <span className={`badge ${b}`}>{b.replace('_', ' ')}</span>
}

export function StatusBadge({ status }) {
  return <span className={`badge ${status || 'tag'}`}>{String(status || '').replace('_', ' ')}</span>
}

export function Stat({ label, value, sub }) {
  return (
    <div className="card">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export function Empty({ children }) {
  return <div className="muted small" style={{ padding: '14px 4px' }}>{children}</div>
}

export function timeAgo(iso) {
  if (!iso) return '—'
  const then = new Date(iso)
  const secs = Math.max(0, (Date.now() - then.getTime()) / 1000)
  if (secs < 60) return `${Math.floor(secs)}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export function pct(v) {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toFixed(0)}%`
}
