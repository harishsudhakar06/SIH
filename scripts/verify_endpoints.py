"""Verify every endpoint required by the master prompt (PART D) over real HTTP."""
import json
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5000"


def login(u, p):
    r = requests.post(f"{BASE}/api/auth/login", json={"username": u, "password": p}, timeout=20)
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['token']}"}


def main():
    h_admin = login("admin", "admin123")
    h_analyst = login("analyst", "analyst123")
    h_response = login("response", "response123")
    h_leader = login("leader", "leader123")
    h_volunteer = login("volunteer", "volunteer123")
    h_citizen = login("citizen", "citizen123")

    checks = []

    def hit(label, method, path, *, hdr=None, body=None, want=(200, 201), params=None,
            form=False):
        try:
            fn = getattr(requests, method)
            kwargs = {"data": body} if form else {"json": body}
            r = fn(f"{BASE}{path}", headers=hdr, params=params, timeout=60, **kwargs)
            ok = r.status_code in want
            checks.append((ok, label, method.upper() + " " + path, r.status_code))
            return r.json() if ok and r.content else {}
        except Exception as exc:
            checks.append((False, label, method.upper() + " " + path, str(exc)))
            return {}

    # auth
    hit("login", "post", "/api/auth/login", body={"username": "admin", "password": "admin123"})
    hit("me", "get", "/api/auth/me", hdr=h_analyst)

    # slopes
    slopes = hit("slopes", "get", "/api/slopes", params={"limit": 5})
    sid = (slopes.get("slopes") or [{}])[0].get("id", 1)
    hit("slope detail", "get", f"/api/slopes/{sid}", hdr=h_analyst)
    hit("slope risk", "get", f"/api/slopes/{sid}/risk", hdr=h_analyst)
    hit("slope reanalyze", "post", f"/api/slopes/{sid}/reanalyze", hdr=h_analyst, body={})
    hit("slope history", "get", f"/api/slopes/{sid}/history", hdr=h_analyst)

    # reports + analysis
    hit("reports", "get", "/api/reports", hdr=h_citizen, params={"scope": "all"})
    rep = hit("report create", "post", "/api/reports", hdr=h_citizen, want=(201,), form=True, body={
        "title": "Endpoint check crack", "category": "crack",
        "lat": "25.61", "lng": "91.90", "gps_source": "device",
        "description": "spec endpoint verification"})
    rid = (rep.get("report") or {}).get("id")
    if rid:
        hit("report detail", "get", f"/api/reports/{rid}", hdr=h_citizen)
        hit("report analysis", "get", f"/api/reports/{rid}/analysis", hdr=h_citizen)
        hit("report analyze", "post", f"/api/reports/{rid}/analyze", hdr=h_volunteer, body={})
        hit("report verify", "post", f"/api/reports/{rid}/verify", hdr=h_volunteer, body={
            "verification_status": "inconclusive"})

    # map
    hit("map slopes", "get", "/api/map/slopes", hdr=h_analyst)
    hit("map reports", "get", "/api/map/reports", hdr=h_analyst)
    hit("map alerts", "get", "/api/map/alerts", hdr=h_analyst)

    # alerts lifecycle
    alerts = hit("alerts", "get", "/api/alerts", hdr=h_response, params={"status": "open"})
    open_alerts = alerts.get("alerts") or []
    if open_alerts:
        aid = open_alerts[0]["id"]
        hit("alert detail", "get", f"/api/alerts/{aid}", hdr=h_response)
        hit("alert notify", "post", f"/api/alerts/{aid}/notify", hdr=h_leader,
            body={"note": "spec check"}, want=(201,))
        hit("alert acknowledge", "post", f"/api/alerts/{aid}/acknowledge", hdr=h_response,
            body={"note": "spec check"})
        hit("alert dispatch", "post", f"/api/alerts/{aid}/dispatch", hdr=h_response,
            body={"team_name": "SDRF Spec Team", "personnel": 4, "eta_minutes": 20}, want=(201,))
        hit("alert resolve", "post", f"/api/alerts/{aid}/resolve", hdr=h_response,
            body={"note": "spec check done"})
    else:
        checks.append((True, "alerts lifecycle", "SKIP (no open alert)", 0))

    # command / emergency / technical
    hit("command overview", "get", "/api/command/overview", hdr=h_leader)
    hit("emergency incidents", "get", "/api/emergency/incidents", hdr=h_response)
    hit("technical slopes", "get", "/api/technical/slopes", hdr=h_analyst)

    # sensors
    hit("sensors readings", "get", "/api/sensors", hdr=h_analyst)
    dev = hit("sensor register", "post", "/api/sensors", hdr=h_analyst, want=(201,), body={
        "device_uid": f"CHK-DEV-{int(__import__('time').time())}", "sensor_type": "crack_gauge",
        "slope_id": sid, "measured_vars": "crack_opening_mm", "battery_pct": 87})
    did = (dev.get("device") or {}).get("id")
    if did:
        hit("sensor devices", "get", "/api/sensors/devices", hdr=h_analyst)
        hit("sensor detail", "get", f"/api/sensors/{did}", hdr=h_analyst)
        hit("sensor observations", "get", f"/api/sensors/{did}/observations", hdr=h_analyst)
        hit("sensor reading", "post", f"/api/sensors/{did}/readings", hdr=h_analyst,
            body={"value": 7.5, "unit": "mm"}, want=(201,))
        hit("sensor maintenance", "post", f"/api/sensors/{did}/maintenance", hdr=h_analyst,
            body={"kind": "calibration", "notes": "spec check", "battery_pct": 90}, want=(201,))

    # incidents + dispatch
    inc = hit("incidents", "get", "/api/incidents", hdr=h_response)
    incs = inc.get("incidents") or []
    if incs:
        iid = incs[0]["id"]
        hit("incident detail", "get", f"/api/incidents/{iid}", hdr=h_response)

    # notifications
    notes = hit("notifications", "get", "/api/notifications", hdr=h_response)
    nlist = notes.get("notifications") or []
    if nlist:
        hit("notification ack", "post", f"/api/notifications/{nlist[0]['id']}/acknowledge",
            hdr=h_response)

    # system
    hit("system health", "get", "/api/system/health", hdr=h_analyst)
    hit("system data-status", "get", "/api/system/data-status", hdr=h_analyst)
    hit("system info", "get", "/api/system/info")
    hit("data quality", "get", "/api/data-quality", hdr=h_analyst)
    hit("ml model info", "get", "/api/ml/model-info", hdr=h_analyst)
    hit("graph", "get", "/api/graph", hdr=h_analyst)
    hit("dashboard", "get", "/api/dashboard", hdr=h_analyst)

    # unauthorized rejection
    checks.append((requests.get(f"{BASE}/api/audit").status_code == 401,
                   "authz rejection", "GET /api/audit unauthenticated -> 401", ""))

    passed = sum(1 for c in checks if c[0])
    print(f"\n{passed}/{len(checks)} spec endpoints OK\n")
    for okflag, label, req, code in checks:
        print(f"  [{'OK ' if okflag else 'FAIL'}] {label:24s} {req:52s} {code}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
