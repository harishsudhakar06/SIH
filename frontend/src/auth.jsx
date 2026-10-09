import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api, getToken, setToken } from './api.js'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [booting, setBooting] = useState(!!getToken())

  const loadMe = useCallback(async () => {
    if (!getToken()) {
      setUser(null)
      return null
    }
    try {
      const { user: me } = await api('/auth/me')
      setUser(me)
      return me
    } catch {
      setToken('')
      setUser(null)
      return null
    }
  }, [])

  useEffect(() => {
    loadMe().finally(() => setBooting(false))
    const onUnauthorized = () => {
      setToken('')
      setUser(null)
    }
    window.addEventListener('bhuraksha:unauthorized', onUnauthorized)
    return () => window.removeEventListener('bhuraksha:unauthorized', onUnauthorized)
  }, [loadMe])

  const login = useCallback(async (username, password) => {
    const res = await api('/auth/login', { method: 'POST', body: { username, password } })
    setToken(res.token)
    setUser(res.user)
    return res.user
  }, [])

  const logout = useCallback(async () => {
    try {
      await api('/auth/logout', { method: 'POST' })
    } catch { /* ignore */ }
    setToken('')
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, booting, login, logout, reload: loadMe, can: (p) => (user?.permissions || []).includes(p) }),
    [user, booting, login, logout, loadMe],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
