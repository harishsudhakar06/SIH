// Geolocation helper for the mobile apps.
//
// Uses the device GPS when permission is granted. If it is unavailable the
// caller must either request a manual fix or explicitly label the configured
// fallback - nothing is silently invented.
import { useCallback, useState } from 'react'

export function useGeolocation() {
  const [state, setState] = useState({ status: 'idle', lat: null, lng: null, source: null, message: '' })

  const request = useCallback(() => {
    if (!navigator.geolocation) {
      setState((s) => ({ ...s, status: 'unsupported', message: 'This device does not provide geolocation' }))
      return
    }
    setState((s) => ({ ...s, status: 'locating', message: 'Getting GPS fix…' }))
    navigator.geolocation.getCurrentPosition(
      (pos) => setState({
        status: 'ok', lat: pos.coords.latitude, lng: pos.coords.longitude,
        source: 'device', message: `GPS ±${Math.round(pos.coords.accuracy)} m`,
      }),
      (err) => setState({
        status: 'error', lat: null, lng: null, source: null,
        message: err.code === 1 ? 'Location permission denied' : 'Could not get a GPS fix',
      }),
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 30000 },
    )
  }, [])

  return { ...state, request }
}

/** Configured fallback = the signed-in user's stored coordinates (always labelled). */
export function fallbackLocation(user) {
  if (user && user.lat != null && user.lng != null) {
    return { lat: user.lat, lng: user.lng, source: 'fallback_configured' }
  }
  return null
}
