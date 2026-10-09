const RAW = import.meta.env.VITE_API_BASE_URL || '/api'
export const API_BASE = RAW.replace(/\/+$/, '')

const TOKEN_KEY = 'bhuraksha_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  constructor(status, data) {
    super(data?.detail || data?.error || `HTTP ${status}`)
    this.status = status
    this.data = data
  }
}

export async function api(path, { method = 'GET', body, formData, timeout = 20000 } = {}) {
  const headers = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  let payload = undefined
  if (formData) payload = formData
  else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  let res
  try {
    res = await fetch(API_BASE + path, { method, headers, body: payload, signal: controller.signal })
  } catch (e) {
    clearTimeout(timer)
    throw new ApiError(0, { error: 'network', detail: 'Cannot reach the BhuRaksha server' })
  }
  clearTimeout(timer)
  const type = res.headers.get('content-type') || ''
  const data = type.includes('application/json') ? await res.json() : await res.blob()
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/auth')) {
      window.dispatchEvent(new Event('bhuraksha:unauthorized'))
    }
    throw new ApiError(res.status, data)
  }
  return data
}

export function photoUrl(path) {
  if (!path) return null
  if (/^https?:/.test(path)) return path
  const origin = API_BASE.replace(/\/api$/, '')
  return `${origin}/uploads/${path}`
}
