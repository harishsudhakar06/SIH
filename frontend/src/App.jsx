import React from 'react'
import { NavLink, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './auth.jsx'
import { useNewAlerts, usePoll } from './hooks.js'
import LoginPage from './pages/LoginPage.jsx'
import DashboardPage from './pages/DashboardPage.jsx'
import MapPage from './pages/MapPage.jsx'
import SlopeDetailPage from './pages/SlopeDetailPage.jsx'
import ReportsPage from './pages/ReportsPage.jsx'
import AlertsPage from './pages/AlertsPage.jsx'
import IncidentsPage from './pages/IncidentsPage.jsx'
import DataPage from './pages/DataPage.jsx'
import ModelPage from './pages/ModelPage.jsx'

const NAV = [
  { to: '/', label: 'Dashboard', icon: '▣' },
  { to: '/map', label: 'Risk Map', icon: '◈' },
  { to: '/reports', label: 'Citizen Reports', icon: '✎' },
  { to: '/alerts', label: 'Alerts', icon: '⚠' },
  { to: '/incidents', label: 'Incidents & Dispatch', icon: '⛑' },
  { to: '/data', label: 'Data & Quality', icon: '☰' },
  { to: '/model', label: 'Model & Audit', icon: '⚙' },
]

function Toasts({ toasts, dismiss }) {
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind || ''}`}>
          <div className="between">
            <strong>{t.title}</strong>
            <button className="btn ghost" onClick={() => dismiss(t.id)}>✕</button>
          </div>
          <div className="small muted" style={{ marginTop: 4 }}>{t.body}</div>
        </div>
      ))}
    </div>
  )
}

function Layout({ children }) {
  const { user, logout, booting } = useAuth()
  const location = useLocation()
  const [toasts, setToasts] = React.useState([])

  const pushToast = React.useCallback((toast) => {
    const id = Math.random().toString(36).slice(2)
    setToasts((prev) => [...prev, { ...toast, id }])
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 12000)
  }, [])

  useNewAlerts((fresh) => {
    for (const a of fresh.slice(0, 3)) {
      pushToast({ title: `⚠ ${a.severity.replace('_', ' ').toUpperCase()} alert — ${a.slope_name}`, body: a.message })
    }
  })

  const { data: sys } = usePoll('/system/info', { interval: 30000 })
  const { data: dash } = usePoll('/dashboard', { interval: 5000, enabled: !!user })
  const openAlerts = dash?.open_alerts?.length ?? 0

  if (booting) return <div className="login-wrap"><div className="muted">Loading…</div></div>

  const title = React.useMemo(() => {
    const item = NAV.find((n) => n.to === location.pathname)
    if (location.pathname.startsWith('/slopes/')) return 'Slope Intelligence'
    return item?.label || 'BhuRaksha'
  }, [location.pathname])

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">Bhu<span>Raksha</span></div>
        <div className="brand-sub">Connected Slope Intelligence · SIH26001</div>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === '/'}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
            <span>{n.icon}</span> {n.label}
            {n.to === '/alerts' && openAlerts > 0 && <span className="nav-badge">{openAlerts}</span>}
          </NavLink>
        ))}
        <div className="side-footer">
          <div>Engine v{sys?.engine_version || '—'}</div>
          <div>LAN {sys?.server?.lan_ip || '—'}:{sys?.server?.port || 5000}</div>
          <div>Model {sys?.model_version ? sys.model_version.slice(-13) : 'not trained'}</div>
          <div style={{ marginTop: 6, color: '#e3b341' }}>{sys?.data_disclaimer}</div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h1>{title}</h1>
          <span className="pill live">● live · poll 5s</span>
          <span className="pill">{sys?.counts?.slopes ?? '—'} slopes</span>
          <span className="pill warn">fixtures: rainfall/sensors · satellite unavailable</span>
          <span className="spacer" />
          {user ? (
            <>
              <span className="pill">{user.display_name} · {user.role}</span>
              <button className="btn" onClick={logout}>Sign out</button>
            </>
          ) : (
            <NavLink to="/login" className="btn primary">Sign in</NavLink>
          )}
        </header>
        <main className="content">{children}</main>
      </div>
      <Toasts toasts={toasts} dismiss={(id) => setToasts((p) => p.filter((t) => t.id !== id))} />
    </div>
  )
}

function Protected({ children }) {
  const { user, booting } = useAuth()
  if (booting) return null
  if (!user) return <Navigate to="/login" replace />
  return <Layout>{children}</Layout>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<Protected><DashboardPage /></Protected>} />
      <Route path="/map" element={<Protected><MapPage /></Protected>} />
      <Route path="/slopes/:id" element={<Protected><SlopeDetailPage /></Protected>} />
      <Route path="/reports" element={<Protected><ReportsPage /></Protected>} />
      <Route path="/alerts" element={<Protected><AlertsPage /></Protected>} />
      <Route path="/incidents" element={<Protected><IncidentsPage /></Protected>} />
      <Route path="/data" element={<Protected><DataPage /></Protected>} />
      <Route path="/model" element={<Protected><ModelPage /></Protected>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
