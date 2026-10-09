import React, { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth.jsx'

const DEMO = [
  ['citizen', 'citizen123', 'Citizen report app'],
  ['volunteer', 'volunteer123', 'Field verification'],
  ['leader', 'leader123', 'Community leader'],
  ['response', 'response123', 'Emergency response'],
  ['analyst', 'analyst123', 'Risk analyst'],
  ['admin', 'admin123', 'Administrator'],
]

export default function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('analyst')
  const [password, setPassword] = useState('analyst123')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/" replace />

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await login(username, password)
      navigate('/')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="brand">Bhu<span>Raksha</span></div>
        <div className="brand-sub">Connected Slope Intelligence · SIH26001 · Northeast India</div>
        <form onSubmit={submit}>
          <label className="field"><span>Username</span>
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
          </label>
          <label className="field"><span>Password</span>
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>
          {error && <div className="banner" style={{ marginBottom: 10 }}>{error}</div>}
          <button className="btn primary" style={{ width: '100%' }} disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in to command center'}
          </button>
        </form>
        <div className="mt">
          <div className="small muted" style={{ marginBottom: 6 }}>Demo accounts (click to fill):</div>
          <div className="grid cols-3" style={{ gap: 6 }}>
            {DEMO.map(([u, p, role]) => (
              <div key={u} className="demo-chip"
                onClick={() => { setUsername(u); setPassword(p) }}
                title={role}>
                {u}
              </div>
            ))}
          </div>
        </div>
        <div className="small muted mt">
          Phones connect to this laptop over Wi-Fi using its LAN IP — never localhost.
        </div>
      </div>
    </div>
  )
}
