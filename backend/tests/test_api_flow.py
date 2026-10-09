"""End-to-end API tests covering the faculty demonstration workflow:

citizen submits a report + photo -> pipeline runs -> evidence/trace persisted ->
alert raised (duplicate-safe) -> response app acknowledges -> incident + dispatch
-> status transitions -> resolution.
"""
from __future__ import annotations

import io
import json

import db


def login(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_login_and_me(client, auth_headers):
    res = login(client, "analyst", "analyst123")
    assert res.status_code == 200
    token = res.get_json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.get_json()["user"]
    assert body["role"] == "analyst"
    assert "assessments:run" in body["permissions"]


def test_bad_login_rejected(client):
    assert login(client, "analyst", "wrong").status_code == 401


def test_register_citizen(client):
    res = client.post("/api/auth/register", json={
        "username": "newcitizen", "password": "secret123",
        "display_name": "Test Citizen", "village": "Test Village",
        "state": "Meghalaya", "district": "East Khasi Hills"})
    assert res.status_code == 201
    # non-citizen roles require admin
    res2 = client.post("/api/auth/register", json={
        "username": "sneaky", "password": "secret123", "role": "response_operator"})
    assert res2.status_code == 403


def test_rbac_requires_auth_and_permissions(client, auth_headers):
    assert client.get("/api/slopes").status_code == 200  # public read
    assert client.post("/api/assessments/run", json={}).status_code == 401
    citizen = client.post("/api/assessments/run", json={}, headers=auth_headers("citizen"))
    assert citizen.status_code == 403
    leader = client.post("/api/alerts/1/ack", json={}, headers=auth_headers("leader"))
    assert leader.status_code in (200, 404)  # leader may ack
    citizen_ack = client.post("/api/alerts/1/ack", json={}, headers=auth_headers("citizen"))
    assert citizen_ack.status_code == 403


def test_citizen_report_with_real_photo_runs_pipeline(client, auth_headers, tokens, fixture_photo):
    slope = db.query_one("SELECT * FROM slopes ORDER BY id LIMIT 1")
    photo_bytes = fixture_photo.read_bytes()

    data = {
        "title": "Fresh tension crack above the hairpin bend",
        "description": "Visible crack widening after last night's rain.",
        "category": "crack",
        "lat": str(slope["lat"]),
        "lng": str(slope["lng"]),
        "gps_source": "device",
        "observed_at": db.utcnow(),
    }
    res = client.post(
        "/api/reports", data={**data, "photo": (io.BytesIO(photo_bytes), "crack.jpg")},
        content_type="multipart/form-data",
        headers=auth_headers("citizen"),
    )
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    report = body["report"]
    assert report["photo_path"], "photo must be stored"
    assert report["gps_source"] == "device"
    assert body["slope_matched"], "nearest slope should match within radius"
    assert body["assessment"], "pipeline must run for the report"
    assert body["assessment"]["assessment_id"] > 0

    stored = db.query_one("SELECT photo_path FROM citizen_reports WHERE id = ?", (report["id"],))
    from config import config

    assert (config.UPLOAD_DIR / stored["photo_path"]).exists(), "photo file must exist on disk"

    # photo is served back by the uploads endpoint (authenticated read is enough)
    img = client.get(f"/uploads/{stored['photo_path']}")
    assert img.status_code == 200
    assert img.data[:3] in (b"\xff\xd8\xff", b"\x89PN")  # jpeg/png magic

    # duplicate submission at the same spot is clustered
    res2 = client.post(
        "/api/reports",
        data={**data, "title": "Same crack again", "photo": (io.BytesIO(photo_bytes), "crack2.jpg")},
        content_type="multipart/form-data",
        headers=auth_headers("citizen"),
    )
    assert res2.status_code == 201
    assert res2.get_json()["duplicate_of"] == report["id"]


def test_report_requires_location_without_fallback(client, auth_headers):
    res = client.post("/api/reports", data={
        "title": "No location", "category": "crack",
        "lat": "", "lng": "", "gps_source": "device"},
        content_type="multipart/form-data", headers=auth_headers("citizen"))
    assert res.status_code == 400
    assert "lat/lng" in res.get_json()["detail"]


def test_assessment_trace_is_auditable(client, auth_headers):
    assessment = db.query_one(
        """SELECT a.* FROM assessments a
           WHERE EXISTS (SELECT 1 FROM assessment_evidence e WHERE e.assessment_id = a.id)
           ORDER BY a.id DESC LIMIT 1""")
    assert assessment is not None

    res = client.get(f"/api/assessments/{assessment['id']}/trace",
                     headers=auth_headers("analyst"))
    assert res.status_code == 200
    trace = res.get_json()
    assert trace["method"] == "normalized_weighted_logodds_pool"
    assert "prior" in trace and trace["prior"]["probability"] > 0
    types = {e["evidence_type"] for e in trace["evidence_accepted"]}
    assert types, "accepted evidence must be listed"
    assert trace["missing_inputs"], "satellite (at least) must be listed as missing"
    assert trace["fusion"]["effective_weight_formula"].startswith("source_weight")
    assert trace["connected_slope"]["mode"] == "bounded_one_hop"
    assert trace["result"]["next_action"]
    assert trace["caveats"]

    detail = client.get(f"/api/assessments/{assessment['id']}", headers=auth_headers("analyst"))
    assert detail.status_code == 200
    rows = detail.get_json()["evidence"]
    statuses = {r["status"] for r in rows}
    assert "accepted" in statuses
    assert any(r["status"] == "missing" for r in rows), "missing inputs recorded per evidence row"
    for r in rows:
        assert r["source_code"]
        assert r["provenance"] is not None


def test_run_assessment_twice_does_not_duplicate_alerts(client, auth_headers):
    slope = db.query_one("SELECT * FROM slopes ORDER BY RANDOM() LIMIT 1")
    before = db.query_one("SELECT COUNT(*) AS n FROM alerts WHERE slope_id = ?", (slope["id"],))["n"]

    first = client.post("/api/assessments/run", json={"slope_id": slope["id"]},
                        headers=auth_headers("analyst"))
    assert first.status_code == 200
    second = client.post("/api/assessments/run", json={"slope_id": slope["id"]},
                         headers=auth_headers("analyst"))
    assert second.status_code == 200

    after = db.query_one("SELECT COUNT(*) AS n FROM alerts WHERE slope_id = ?", (slope["id"],))["n"]
    assert after == before or after == before + 1, "at most one new alert"
    if second.get_json()["results"][0]["band"] in ("HIGH", "VERY_HIGH", "CONFIRMED"):
        assert second.get_json()["results"][0]["alert_created"] is False
        assert "duplicate" in second.get_json()["results"][0]["alert_note"] or \
               "already exists" in second.get_json()["results"][0]["alert_note"]


def test_alert_duplicate_key_is_unique_in_db():
    import services.risk_state as rs

    slope = db.query_one("SELECT id FROM slopes ORDER BY id LIMIT 1")
    a1, created1 = rs.create_alert(slope["id"], 1, "HIGH", "t", "m", "D", "S")
    a2, created2 = rs.create_alert(slope["id"], 1, "HIGH", "t", "m", "D", "S")
    assert created1 is True
    assert created2 is False
    assert a2["id"] == a1["id"]


def test_verification_task_queue(client, auth_headers):
    res = client.get("/api/tasks/verification", headers=auth_headers("volunteer"))
    assert res.status_code == 200
    body = res.get_json()
    assert "tasks" in body
    for task in body["tasks"]:
        assert task["reasons"]


def test_field_verification_updates_assessment(client, auth_headers):
    report = db.query_one(
        """SELECT * FROM citizen_reports WHERE slope_id IS NOT NULL
           ORDER BY id DESC LIMIT 1""")
    assert report is not None, "seed fixtures must include a report with a slope"
    res = client.post(f"/api/reports/{report['id']}/verify", json={
        "verification_status": "ground_truth", "crack_mm": 35, "seepage": True,
        "fresh_scarp": True, "displacement_m": 0.2, "notes": "verified on site",
        "gps_source": "device",
    }, headers=auth_headers("volunteer"))
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    assert body["assessment"] is not None

    latest = db.query_one(
        "SELECT * FROM assessments WHERE slope_id = ? ORDER BY id DESC LIMIT 1",
        (report["slope_id"],))
    assert latest["ground_truth"] == 1
    assert latest["risk_band"] == "CONFIRMED"


def test_ack_creates_incident_and_dispatch_flow(client, auth_headers):
    # produce an alert deterministically on one slope
    import services.risk_state as rs
    from services.pipeline import run_assessment

    slope = db.query_one("SELECT * FROM slopes ORDER BY id LIMIT 1")
    assessment = run_assessment(slope["id"])
    if assessment["band"] not in ("HIGH", "VERY_HIGH", "CONFIRMED"):
        alert, _ = rs.create_alert(slope["id"], assessment["assessment_id"], "HIGH",
                                   "Test alert", "test message", slope["district"], slope["state"])
    else:
        alert = assessment["alert"]
    assert alert

    ack = client.post(f"/api/alerts/{alert['id']}/ack",
                      json={"note": "team notified"}, headers=auth_headers("response"))
    assert ack.status_code == 200, ack.get_json()
    body = ack.get_json()
    assert body["alert"]["status"] == "acknowledged"
    incident = body["incident"]
    assert incident and incident["status"] == "acknowledged"

    # duplicate ack does not create a second incident
    ack2 = client.post(f"/api/alerts/{alert['id']}/ack", json={"note": "again"},
                       headers=auth_headers("leader"))
    assert ack2.status_code == 200
    assert ack2.get_json()["incident"]["id"] == incident["id"]

    dispatch = client.post(f"/api/incidents/{incident['id']}/dispatch", json={
        "team_name": "SDRF Team A", "personnel": 6, "eta_minutes": 25,
        "note": "rope and medical kit"}, headers=auth_headers("response"))
    assert dispatch.status_code == 201, dispatch.get_json()
    assert dispatch.get_json()["incident"]["status"] == "dispatched"

    moved = client.post(f"/api/incidents/{incident['id']}/events",
                        json={"to_status": "responding", "note": "team on site"},
                        headers=auth_headers("response"))
    assert moved.status_code == 200
    assert moved.get_json()["incident"]["status"] == "responding"

    # invalid transition rejected by the state machine
    bad = client.post(f"/api/incidents/{incident['id']}/events",
                      json={"to_status": "reported"}, headers=auth_headers("response"))
    assert bad.status_code == 409

    resolved = client.post(f"/api/alerts/{alert['id']}/resolve",
                           json={"note": "slope stabilised"}, headers=auth_headers("response"))
    assert resolved.status_code == 200
    assert resolved.get_json()["alert"]["status"] == "resolved"


def test_dispatch_requires_response_role(client, auth_headers):
    incident = db.query_one("SELECT * FROM incidents ORDER BY id LIMIT 1")
    res = client.post(f"/api/incidents/{incident['id']}/dispatch",
                      json={"team_name": "x"}, headers=auth_headers("citizen"))
    assert res.status_code == 403


def test_dashboard_and_system_info(client, auth_headers):
    dash = client.get("/api/dashboard", headers=auth_headers("analyst"))
    assert dash.status_code == 200
    body = dash.get_json()
    assert sum(body["bands"].values()) >= 70
    assert body["top_risks"] and body["activity"]

    info = client.get("/api/system/info").get_json()
    assert info["app"] == "BhuRaksha"
    assert info["problem_statement"].startswith("SIH26001")
    assert info["counts"]["slopes"] > 0
    assert info["server"]["lan_ip"]


def test_data_quality_labels_prototype_and_unavailable_feeds(client, auth_headers):
    dq = client.get("/api/data-quality", headers=auth_headers("analyst")).get_json()
    feeds = dq["feeds"]
    assert feeds["satellite"]["unavailable"] >= 0
    assert feeds["satellite"]["data_mode"] == "unavailable"
    assert feeds["rainfall"]["data_mode"] == "prototype_fixture"
    assert any("satellite" in f for f in dq["flags"] if f)

    rain = client.get("/api/rainfall").get_json()
    assert rain["data_mode"] == "prototype_fixture"
    assert "not live" in rain["disclaimer"] or "no live" in rain["disclaimer"]


def test_notifications_are_role_scoped(client, auth_headers):
    res = client.get("/api/notifications", headers=auth_headers("response"))
    assert res.status_code == 200
    body = res.get_json()
    assert all(n["role_scope"] in ("response_operator", None) for n in body["notifications"])
    marked = client.post("/api/notifications/read", json={"all": True},
                         headers=auth_headers("response"))
    assert marked.status_code == 200


def test_tile_endpoint_serves_image(client):
    res = client.get("/api/tiles/3/6/3.png")
    assert res.status_code == 200
    assert res.headers["Content-Type"].startswith("image/")
    bad = client.get("/api/tiles/99/1/1.png")
    assert bad.status_code == 200  # placeholder, not a crash
    assert client.get("/api/tiles/1/999999/1.png").status_code == 200


def test_audit_log_requires_analyst(client, auth_headers):
    assert client.get("/api/audit").status_code == 401
    assert client.get("/api/audit", headers=auth_headers("citizen")).status_code == 403
    ok = client.get("/api/audit", headers=auth_headers("analyst"))
    assert ok.status_code == 200
    assert ok.get_json()["count"] >= 1
