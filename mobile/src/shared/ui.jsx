import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import { useAuth } from './auth.jsx'

export function usePoll(path, { interval = 6000, enabled = true } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const ref = useRef(path)
  ref.current = path
  const refresh = useCallback(async () => {
    try {
      setData(await api(ref.current))
      setError(null)
    } catch (e) { setError(e.message) }
  }, [])
  useEffect(() => {
    if (!enabled) return undefined
    refresh()
    const id = setInterval(refresh, interval)
    return () => clearInterval(id)
  }, [enabled, interval, refresh])
  return { data, error, refresh }
}

export function Badge({ value, kind }) {
  return <span className={`badge ${kind || value || 'tag'}`}>{String(value || kind || '').replace('_', ' ')}</span>
}

export function timeAgo(iso) {
  if (!iso) return '—'
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return `${Math.floor(secs)}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export function Login({ appTitle, blurb, roles }) {
  const { login, user, roleOk } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (user) return null

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError('')
    try { await login(username, password) } catch (err) { setError(err.message) } finally { setBusy(false) }
  }

  return (
    <div className="login">
      <div className="card">
        <div className="brand" style={{ fontSize: 22, fontWeight: 700 }}>Bhu<span style={{ color: 'var(--accent)' }}>Raksha</span></div>
        <div className="muted small">{appTitle} · SIH26001 Connected Slope Intelligence</div>
        <p className="small muted">{blurb}</p>
        {user && !roleOk && (
          <div className="banner error">Signed in as {user.role}, but this app is for: {roles.join(', ')}. Ask an admin for the right account.</div>
        )}
        <form onSubmit={submit}>
          <label className="field"><span>Username</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" /></label>
          <label className="field"><span>Password</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></label>
          {error && <div className="banner error">{error}</div>}
          <button className="btn primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
        <div className="small muted mt">Server: {localStorage.getItem('bhuraksha_server') || 'same origin / API configured at build time'}</div>
      </div>
    </div>
  )
}

export function Shell({ title, blurb, roles = [], tabs, active, onSelect, children }) {
  const { user, logout, roleOk } = useAuth()
  if (!user) {
    return <Login appTitle={title} blurb={blurb} roles={roles} />
  }
  return (
    <>
      <div className="topbar">
        <div className="brand">Bhu<span>Raksha</span> · {title}</div>
        <span className="spacer" />
        <span className="role">{user.role}</span>
        <button className="btn sm ghost" onClick={logout}>Exit</button>
      </div>
      <div className="app">
        {!roleOk && <div className="banner error">Your role does not match this app; sensitive actions are blocked.</div>}
        {children}
      </div>
      <div className="tabs">
        {tabs.map((t) => (
          <button key={t.key} className={`tab ${active === t.key ? 'active' : ''}`} onClick={() => onSelect(t.key)}>
            {t.icon}<div>{t.label}</div>
          </button>
        ))}
      </div>
    </>
  )
}

export function Toast({ message, kind, onClose }) {
  useEffect(() => { const id = setTimeout(onClose, 7000); return () => clearTimeout(id) }, [message, onClose])
  if (!message) return null
  return <div className={`toast ${kind || ''}`}>{message}</div>
}
