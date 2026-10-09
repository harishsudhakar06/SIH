"""Local geographic context layer.

Serves a small, pre-built NE-India state-boundary GeoJSON
(backend/data/geo/ne_states.geojson, open data, simplified with shapely via
scripts/build_geo_layer.py). Used by the Risk Map as a real geographic layer
(state outlines) that works fully offline alongside the local tile cache.
"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, send_file
import io

from config import BASE_DIR

bp = Blueprint("geo_api", __name__, url_prefix="/api/geo")

GEO_FILE = BASE_DIR / "data" / "geo" / "ne_states.geojson"


@bp.get("/states")
def ne_states():
    if not GEO_FILE.exists():
        return jsonify({"features": [], "source": "missing",
                        "note": "Run python scripts/build_geo_layer.py to build the local boundary layer."})
    payload = GEO_FILE.read_bytes()
    resp = send_file(io.BytesIO(payload), mimetype="application/geo+json")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.get("/status")
def geo_status():
    exists = GEO_FILE.exists()
    size = GEO_FILE.stat().st_size if exists else 0
    count = 0
    if exists:
        try:
            count = len(json.loads(GEO_FILE.read_text(encoding="utf-8")).get("features", []))
        except (OSError, ValueError):
            count = 0
    return jsonify({
        "states_layer": "available" if exists else "missing",
        "states_count": count,
        "bytes": size,
        "data_mode": "local_open_data",
        "source": "GADM-derived India states (github.com/geohacker/india), simplified locally",
    })
