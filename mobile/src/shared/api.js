const RAW = import.meta.env.VITE_API_BASE_URL || '/api'
export const API_BASE = RAW.replace(/\/+$/, '')
const KEY = 'bhuraksha_token'

export const getToken = () => localStorage.getItem(KEY) || ''
export const setToken = (t) => (t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY))

export class ApiError extends Error {
  constructor(status, data) {
    super(data?.detail || data?.error || `HTTP ${status}`)
    this.status = status
    this.data = data
  }
}

export async function api(path, { method = 'GET', body, formData, timeout = 25000 } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  let payload
  if (formData) payload = formData
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeout)
  let res
  try {
    res = await fetch(API_BASE + path, { method, headers, body: payload, signal: ctrl.signal })
  } catch {
    clearTimeout(timer)
    throw new ApiError(0, { error: 'network', detail: 'Cannot reach the BhuRaksha server — check Wi-Fi and the laptop address' })
  }
  clearTimeout(timer)
  const type = res.headers.get('content-type') || ''
  const data = type.includes('application/json') ? await res.json() : await res.blob()
  if (!res.ok) throw new ApiError(res.status, data)
  return data
}

export const photoUrl = (p) => (p ? `${API_BASE.replace(/\/api$/, '')}/uploads/${p}` : null)
