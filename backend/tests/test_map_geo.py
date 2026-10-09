"""Risk Map layer endpoints: context, slopes/reports/alerts pins, basemap, geo layer."""
from __future__ import annotations


def test_map_context(client):
    r = client.get("/api/map/context")
    assert r.status_code == 200
    data = r.get_json()
    assert set(data) >= {"center", "zoom", "bbox", "tile_url", "tile_cache_tiles"}
    assert 21.0 <= data["center"]["lat"] <= 30.0
    assert 89.0 <= data["center"]["lng"] <= 98.0
    assert data["tile_url"] == "/api/tiles/{z}/{x}/{y}.png"


def test_map_slopes_public_with_valid_coordinates(client):
    r = client.get("/api/map/slopes")
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] >= 1
    in_region = 0
    for s in data["slopes"]:
        assert isinstance(s["lat"], (int, float))
        assert isinstance(s["lng"], (int, float))
        assert {"code", "name", "risk_band", "final_score"} <= set(s)
        if 21.0 <= s["lat"] <= 30.0 and 89.0 <= s["lng"] <= 98.0:
            in_region += 1
    assert in_region >= 1  # seeded slopes live in NE India; some tests may move one


def test_map_reports_and_alerts_require_login(client):
    assert client.get("/api/map/reports").status_code == 401
    assert client.get("/api/map/alerts").status_code == 401


def test_map_reports_and_alerts_authed(client, auth_headers):
    h = auth_headers("analyst")
    rr = client.get("/api/map/reports", headers=h)
    assert rr.status_code == 200
    for row in rr.get_json()["reports"][:10]:
        assert row["lat"] is None or isinstance(row["lat"], (int, float))
        assert {"report_code", "title", "status", "photo_count"} <= set(row)
    ra = client.get("/api/map/alerts", headers=h)
    assert ra.status_code == 200
    for row in ra.get_json()["alerts"][:10]:
        assert {"severity", "band", "status", "slope_id", "lat", "lng"} <= set(row)


def test_tiles_offline_placeholder_and_status(client):
    r = client.get("/api/tiles/7/98/54.png")  # NE India home viewport tile
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/png"
    assert r.headers.get("X-Tile-Source") in {"cache", "offline", "invalid"}
    st = client.get("/api/tiles/status")
    assert st.status_code == 200
    body = st.get_json()
    assert body["mode"] == "offline_forced"  # test env forces BHURAKSHA_TILE_OFFLINE=1
    assert "cache_tiles" in body


def test_tiles_invalid_zoom_is_still_png(client):
    r = client.get("/api/tiles/99/0/0.png")
    assert r.status_code == 200
    assert r.headers.get("X-Tile-Source") == "invalid"


def test_geo_states_and_status(client):
    st = client.get("/api/geo/status")
    assert st.status_code == 200
    body = st.get_json()
    assert body["states_layer"] in {"available", "missing"}
    r = client.get("/api/geo/states")
    assert r.status_code == 200
    if body["states_layer"] == "available":
        assert body["states_count"] == 8
        geo = r.get_json()
        assert geo["type"] == "FeatureCollection"
        assert len(geo["features"]) == 8
        states = {f["properties"]["state"] for f in geo["features"]}
        assert "Sikkim" in states and "Arunachal Pradesh" in states
