import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { api, getToken, setToken } from './api.js'

const Ctx = createContext(null)

export function AuthProvider({ children, allowedRoles = [] }) {
  const [user, setUser] = useState(null)
  const [booting, setBooting] = useState(!!getToken())

  const load = useCallback(async () => {
    if (!getToken()) { setUser(null); return null }
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
    load().finally(() => setBooting(false))
  }, [load])

  const login = useCallback(async (username, password) => {
    const res = await api('/auth/login', { method: 'POST', body: { username, password } })
    setToken(res.token)
    setUser(res.user)
    return res.user
  }, [])

  const logout = useCallback(() => { setToken(''); setUser(null) }, [])

  const roleOk = !user || allowedRoles.length === 0 || allowedRoles.includes(user.role) || user.role === 'admin'

  const value = useMemo(() => ({ user, booting, login, logout, reload: load, roleOk, allowedRoles }),
    [user, booting, login, logout, load, roleOk])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
