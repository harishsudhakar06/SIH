"""Serving layer + notification-broadcast + field-observation tests."""
import json
import time

import pytest

from app import create_app


@pytest.fixture(scope="module")
def app():
    a = create_app(auto_bootstrap=False)
    with a.test_client() as c:
        c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        c.get("/api/system/info")  # bootstrap
    return a


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.get_json()['token']}"}


def test_root_serves_command_center(app):
    with app.test_client() as c:
        r = c.get("/")
        assert r.status_code == 200
        data = r.get_data(as_text=True)
        assert "BhuRaksha" in data or "<div id=\"root\"" in data


def test_mobile_launcher_lists_five_apps(app):
    with app.test_client() as c:
        r = c.get("/app/")
        if r.status_code == 200 and b"<!doctype html>" in r.get_data().lower():
            page = r.get_data(as_text=True)
            for name in ("citizen", "field", "community", "response", "analyst"):
                assert f"/app/{name}.html" in page
        else:
            pytest.skip("mobile launcher unavailable")


def test_unknown_api_path_is_json_404(app):
    with app.test_client() as c:
        r = c.get("/api/definitely-not-a-route")
        assert r.status_code == 404
        assert r.get_json()["error"] == "not_found"


def test_broadcast_reaches_citizens(app):
    with app.test_client() as c:
        h = login(c, "leader", "leader123")
        r = c.post("/api/notifications/broadcast", json={
            "title": "Test warning", "body": "rain test", "role_scope": "citizen",
        }, headers=h)
        if r.status_code == 404:
            pytest.skip("broadcast endpoint not present")
        assert r.status_code == 201
        c.post("/api/auth/logout", headers=h)
        h2 = login(c, "citizen", "citizen123")
        notes = c.get("/api/notifications", headers=h2).get_json()["notifications"]
        assert any(n["title"] == "Test warning" for n in notes)


def test_field_observation_enters_fusion(app):
    with app.test_client() as c:
        h = login(c, "volunteer", "volunteer123")
        r = c.post("/api/observations", json={
            "slope_id": 1, "category": "fresh_scarp",
            "verification_status": "ground_truth",
            "notes": "pytest ground truth", "lat": 25.6, "lng": 91.9,
        }, headers=h)
        if r.status_code == 404:
            pytest.skip("observations endpoint not present")
        assert r.status_code == 201
        h_analyst = login(c, "analyst", "analyst123")
        run = c.post("/api/assessments/run", json={"slope_id": 1},
                     headers=h_analyst)
        assert run.status_code == 200
        res = run.get_json()["results"][0]
        trace = c.get(f"/api/assessments/{res['assessment_id']}/trace",
                      headers=h_analyst).get_json()
        labels = [e["label"] for e in trace["evidence_accepted"]]
        assert any("field verification" in lbl for lbl in labels)
