import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api.js'

/** Poll a JSON endpoint on an interval. Returns {data,error,loading,refresh,lastUpdated}. */
export function usePoll(path, { interval = 5000, enabled = true, immediate = true } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(immediate && enabled)
  const [lastUpdated, setLastUpdated] = useState(null)
  const pathRef = useRef(path)
  pathRef.current = path

  const refresh = useCallback(async () => {
    try {
      const res = await api(pathRef.current)
      setData(res)
      setError(null)
      setLastUpdated(new Date())
      return res
    } catch (e) {
      setError(e.message)
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return undefined
    refresh()
    const id = setInterval(refresh, interval)
    return () => clearInterval(id)
  }, [enabled, interval, refresh, path])

  return { data, error, loading, refresh, lastUpdated }
}

/** Fire a POST and refresh a poll afterwards. */
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const run = useCallback(async (fn) => {
    setBusy(true)
    setError(null)
    try {
      return await fn()
    } catch (e) {
      setError(e.message)
      throw e
    } finally {
      setBusy(false)
    }
  }, [])
  return { busy, error, run, setError }
}

const seenAlerts = new Set()

/** Watch /alerts and report newly appearing ids exactly once (duplicate-safe toasts). */
export function useNewAlerts(onNew, interval = 5000) {
  const cb = useRef(onNew)
  cb.current = onNew
  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const res = await api('/alerts?limit=50')
        const fresh = []
        for (const a of res.alerts || []) {
          if (!seenAlerts.has(a.id)) {
            seenAlerts.add(a.id)
            fresh.push(a)
          }
        }
        if (alive && fresh.length && seenAlerts.size !== fresh.length) cb.current?.(fresh)
      } catch { /* polling failures are non-fatal */ }
    }
    tick()
    const id = setInterval(tick, interval)
    return () => { alive = false; clearInterval(id) }
  }, [interval])
}
