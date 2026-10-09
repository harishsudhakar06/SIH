"""Map layer endpoints: geographic data for the Live Map page (Leaflet)."""
from __future__ import annotations

from flask import Blueprint, request

import auth as auth_mod
import db
from api.common import err, int_arg, ok

bp = Blueprint("map_api", __name__, url_prefix="/api")


@bp.get("/map/context")
def map_context():
    """Default viewport + basemap readiness for the Risk Map."""
    from config import BASE_DIR, config as cfg

    bbox = cfg.REGION_BBOX
    center_lat = (bbox["min_lat"] + bbox["max_lat"]) / 2
    center_lng = (bbox["min_lon"] + bbox["max_lon"]) / 2
    pngs = list(cfg.TILE_CACHE_DIR.glob("*/*/*.png")) if cfg.TILE_CACHE_DIR.exists() else []
    geo_file = BASE_DIR / "data" / "geo" / "ne_states.geojson"
    return ok({
        "center": {"lat": round(center_lat, 3), "lng": round(center_lng, 3)},
        "zoom": 6,
        "bbox": bbox,
        "tile_cache_tiles": len(pngs),
        "states_layer": "available" if geo_file.exists() else "missing",
        "tile_url": "/api/tiles/{z}/{x}/{y}.png",
        "generated_at": db.utcnow(),
    })


@bp.get("/map/slopes")
def map_slopes():
    """Geographically valid slope locations with their latest risk state."""
    rows = db.query(
        """SELECT s.id, s.code, s.name, s.lat, s.lng, s.district, s.state, s.cluster,
                  s.slope_angle_deg, s.lithology, s.condition_status,
                  a.risk_band, a.final_score, a.confidence, a.recommendation,
                  a.inherited_influence, a.uplift, a.missing_inputs, a.created_at AS assessed_at,
                  (SELECT COUNT(*) FROM slope_edges e
                    WHERE e.src_slope_id = s.id OR e.dst_slope_id = s.id) AS edge_count
           FROM slopes s
           LEFT JOIN assessments a ON a.id = (
               SELECT id FROM assessments WHERE slope_id = s.id ORDER BY id DESC LIMIT 1)
           ORDER BY COALESCE(a.final_score, 0) DESC""")
    return ok({"slopes": rows, "count": len(rows),
               "generated_at": db.utcnow()})


@bp.get("/map/reports")
def map_reports():
    """Report pins. Requires login: reports carry user-generated location data."""
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    limit = int_arg("limit", 300)
    rows = db.query(
        """SELECT r.id, r.report_code, r.title, r.category, r.lat, r.lng, r.gps_source,
                  r.status, r.created_at, r.observed_at, r.is_duplicate,
                  r.slope_id, s.code AS slope_code, s.name AS slope_name,
                  (SELECT COUNT(*) FROM report_media m WHERE m.report_id = r.id) AS media_count,
                  (SELECT COUNT(*) FROM report_media m WHERE m.report_id = r.id AND m.kind = 'photo')
                      AS photo_count
           FROM citizen_reports r
           LEFT JOIN slopes s ON s.id = r.slope_id
           ORDER BY r.created_at DESC LIMIT ?""", (limit,))
    return ok({"reports": rows, "count": len(rows), "generated_at": db.utcnow()})


@bp.get("/map/alerts")
def map_alerts():
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    rows = db.query(
        """SELECT a.id, a.alert_code, a.severity, a.band, a.status, a.title, a.message,
                  a.created_at, a.acknowledged_at, a.resolved_at,
                  s.id AS slope_id, s.code AS slope_code, s.name AS slope_name,
                  s.lat, s.lng, s.district, s.state
           FROM alerts a JOIN slopes s ON s.id = a.slope_id
           WHERE a.status <> 'suppressed'
           ORDER BY a.created_at DESC LIMIT 200""")
    return ok({"alerts": rows, "count": len(rows), "generated_at": db.utcnow()})
