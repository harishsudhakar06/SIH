#!/usr/bin/env python
"""Reproducible end-to-end demonstration of the BhuRaksha workflow.

Run the backend first (python backend/app.py), then:

    python scripts/demo_e2e.py --base http://127.0.0.1:5000

What it proves, over real HTTP:
  1. citizen signs in and submits a report WITH a photograph and a GPS fix
  2. the laptop receives the report + photo and runs the analysis pipeline
  3. the evidence trace, risk score and (if warranted) an alert are persisted
  4. the response operator acknowledges the alert -> an incident opens
  5. a team is dispatched and the incident moves through controlled states
  6. the incident is resolved; everything is re-read from the shared database

No step is simulated: every call is a real request against the running server.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import uuid
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
PHOTO = REPO / "backend" / "data" / "uploads" / "fixture_report_0001.jpg"


def step(label: str) -> None:
    print(f"\n=== {label} " + "=" * max(0, 66 - len(label)))


def show(payload, limit: int = 900) -> None:
    text = json.dumps(payload, indent=2, default=str)
    print(text if len(text) <= limit else text[:limit] + "\n  …")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--slope-id", type=int, default=None,
                    help="slope to target (default: highest scored slope)")
    args = ap.parse_args()
    base = args.base.rstrip("/")

    step("1. server health")
    info = requests.get(f"{base}/api/system/info", timeout=15).json()
    print(f"app={info['app']} engine={info['engine_version']} "
          f"lan={info['server']['lan_ip']}:{info['server']['port']} "
          f"slopes={info['counts']['slopes']} alerts={info['counts']['alerts']}")

    step("2. sign in (citizen, response operator)")
    citizen = requests.post(f"{base}/api/auth/login",
                            json={"username": "citizen", "password": "citizen123"}, timeout=15).json()
    response = requests.post(f"{base}/api/auth/login",
                             json={"username": "response", "password": "response123"}, timeout=15).json()
    analyst = requests.post(f"{base}/api/auth/login",
                            json={"username": "analyst", "password": "analyst123"}, timeout=15).json()
    h_c = {"Authorization": f"Bearer {citizen['token']}"}
    h_r = {"Authorization": f"Bearer {response['token']}"}
    h_a = {"Authorization": f"Bearer {analyst['token']}"}
    print(f"citizen={citizen['user']['role']} response={response['user']['role']} analyst={analyst['user']['role']}")

    step("3. pick a target slope (analyst runs the pipeline first)")
    requests.post(f"{base}/api/assessments/run", json={}, headers=h_a, timeout=180)
    slopes = requests.get(f"{base}/api/slopes?limit=200", timeout=20).json()["slopes"]
    if args.slope_id:
        target = next(s for s in slopes if s["id"] == args.slope_id)
    else:
        target = sorted(slopes, key=lambda s: -(s.get("final_score") or 0))[0]
    print(f"target slope {target['code']} — {target['name']} "
          f"({target['district']}, {target['state']}) band={target.get('risk_band')} "
          f"score={target.get('final_score')}")

    step("4. citizen submits a report WITH a photograph + GPS fix")
    if not PHOTO.exists():
        sys.path.insert(0, str(REPO / "backend"))
        from seed import make_fixture_photo

        make_fixture_photo(PHOTO, seed=11)
    report = {
        "title": "Widening tension crack above the hairpin bend",
        "description": "Observed after last night's rain; soil crumbling at the edge.",
        "category": "crack",
        "lat": str(target["lat"] + 0.0008),
        "lng": str(target["lng"] + 0.0008),
        "gps_source": "device",
        "observed_at": "2026-10-09T12:00:00+00:00",
    }
    with open(PHOTO, "rb") as fh:
        res = requests.post(f"{base}/api/reports", data=report,
                            files={"photo": ("crack.jpg", fh, "image/jpeg")},
                            headers=h_c, timeout=60)
    res.raise_for_status()
    body = res.json()
    show({
        "report_code": body["report"]["report_code"],
        "photo_path": body["report"]["photo_path"],
        "gps_source": body["report"]["gps_source"],
        "slope_matched": (body["slope_matched"] or {}).get("code"),
        "duplicate_of": body["duplicate_of"],
        "assessment": body["assessment"],
        "incident": (body["incident"] or {}).get("incident_code"),
        "pipeline_error": body["pipeline_error"],
    })

    step("5. read the audit trace the engine produced")
    aid = body["assessment"]["assessment_id"]
    trace = requests.get(f"{base}/api/assessments/{aid}/trace", headers=h_a, timeout=20).json()
    print("method:", trace["method"])
    print("prior:", trace["prior"])
    print("accepted evidence:")
    for e in trace["evidence_accepted"]:
        print(f"  - {e['label']:<42} w={e['effective_weight']:.3f} p={e['probability']:.3f} "
              f"conf={e['adjusted_confidence']:.2f} fresh={e.get('freshness', 1.0):.2f} "
              f"[{e['evidence_type']}/{e['status']}]")
    print("rejected evidence:")
    for e in trace["evidence_rejected"]:
        print(f"  - {e['label']:<42} {e['status']}: {e['reason']}")
    print("missing inputs:", trace["missing_inputs"])
    print("dependency groups:", json.dumps(trace["dependency_groups"], indent=2)[:500])
    print("connected-slope:", json.dumps({
        k: trace["connected_slope"][k] for k in
        ("mode", "direct_score", "inherited_influence", "uplift", "final_score", "neighbors_used")}))
    print("result:", json.dumps(trace["result"], indent=2))
    print("transition:", json.dumps(trace["transition"], indent=2))
    print("caveats:", json.dumps(trace["caveats"], indent=2))

    step("6. alerts (duplicate-safe)")
    alerts = requests.get(f"{base}/api/alerts?limit=10", headers=h_r, timeout=20).json()["alerts"]
    for a in alerts[:5]:
        print(f"  {a['alert_code']} {a['severity']:<10} {a['status']:<12} {a['title']}")
    relevant = [a for a in alerts if a["slope_id"] == target["id"]]
    if not relevant:
        print("  no alert for this slope (score/confidence below threshold) - "
              "pipeline behaviour is still demonstrated above")
        step("7. acknowledge + dispatch are exercised on the highest open alert")
        relevant = [a for a in alerts if a["status"] in ("open", "acknowledged")]
        if not relevant:
            print("  no open alerts to act on; demo complete")
            return 0
    alert = relevant[0]

    step("7. response operator acknowledges the alert -> incident opens")
    ack = requests.post(f"{base}/api/alerts/{alert['id']}/ack",
                        json={"note": "SDRF control room informed"}, headers=h_r, timeout=30).json()
    print(f"alert status = {ack['alert']['status']} · incident = {ack['incident']['incident_code']} "
          f"(created={ack['incident_created']})")

    step("8. dispatch a team and move the incident")
    incident_id = ack["incident"]["id"]
    disp = requests.post(f"{base}/api/incidents/{incident_id}/dispatch",
                         json={"team_name": "SDRF Team Alpha", "personnel": 6,
                               "eta_minutes": 25, "note": "rope kit + medical"},
                         headers=h_r, timeout=30).json()
    print(f"dispatch status={disp['dispatch']['status']} incident={disp['incident']['status']}")
    for status in ("responding", "resolved"):
        r = requests.post(f"{base}/api/incidents/{incident_id}/events",
                          json={"to_status": status, "note": f"field update: {status}"},
                          headers=h_r, timeout=30)
        if r.status_code != 200:
            print(f"  transition rejected: {r.status_code} {r.json()}")
            break
        print(f"  incident -> {r.json()['incident']['status']}")

    step("9. resolve the alert (closed loop)")
    r = requests.post(f"{base}/api/alerts/{alert['id']}/resolve",
                      json={"note": "slope fenced, monitoring continues"}, headers=h_r, timeout=30)
    print("alert:", r.json()["alert"]["status"])

    step("10. final state read back from the shared database")
    final = requests.get(f"{base}/api/system/info", timeout=15).json()["counts"]
    show(final, 400)
    dash = requests.get(f"{base}/api/dashboard", headers=h_a, timeout=20).json()
    print("risk bands:", dash["bands"])
    print("incidents by status:", dash["incidents_by_status"])
    print("\nDEMO COMPLETE — report, photograph, analysis, alert, acknowledgement, "
          "dispatch and resolution are all persisted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
