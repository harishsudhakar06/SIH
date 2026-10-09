"""Slope and graph endpoints."""
from __future__ import annotations

from flask import Blueprint, request

import db
from api.common import err, int_arg, ok, parse_since
from auth import current_user, require
from services import connected_slope as cs

bp = Blueprint("slopes_api", __name__, url_prefix="/api")

BAND_ORDER = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "VERY_HIGH": 3, "CONFIRMED": 4}


def _latest_assessment_clause() -> str:
    return """(SELECT id FROM assessments a2 WHERE a2.slope_id = s.id ORDER BY a2.id DESC LIMIT 1)"""


@bp.get("/slopes")
def list_slopes():
    q = (request.args.get("q") or "").strip()
    state = request.args.get("state")
    district = request.args.get("district")
    band = request.args.get("band")
    limit = int_arg("limit", 200, 1, 500)

    sql = f"""
        SELECT s.id, s.code, s.name, s.lat, s.lng, s.elevation_m, s.slope_angle_deg, s.lithology,
               s.land_use, s.district, s.state, s.cluster, s.condition_status,
               a.risk_band, a.final_score, a.confidence, a.fused_probability,
               a.inherited_influence, a.uplift, a.created_at AS assessed_at, a.recommendation,
               (SELECT COUNT(*) FROM slope_edges e WHERE e.src_slope_id = s.id OR e.dst_slope_id = s.id) AS edge_count,
               (SELECT COUNT(*) FROM citizen_reports r WHERE r.slope_id = s.id AND r.is_duplicate = 0) AS report_count
        FROM slopes s
        LEFT JOIN assessments a ON a.id = {_latest_assessment_clause()}
        WHERE 1 = 1
    """
    params: list = []
    if q:
        sql += " AND (s.name LIKE ? OR s.code LIKE ? OR s.cluster LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like]
    if state:
        sql += " AND s.state = ?"
        params.append(state)
    if district:
        sql += " AND s.district = ?"
        params.append(district)
    sql += " ORDER BY COALESCE(a.final_score, 0) DESC LIMIT ?"
    params.append(limit)

    rows = db.query(sql, tuple(params))
    if band:
        rows = [r for r in rows if (r.get("risk_band") or "LOW") == band]
    facets = {
        "states": [r["state"] for r in db.query("SELECT DISTINCT state FROM slopes ORDER BY state")],
        "districts": [r["district"] for r in db.query("SELECT DISTINCT district FROM slopes ORDER BY district")],
    }
    return ok({"slopes": rows, "facets": facets, "count": len(rows)})


@bp.get("/slopes/<int:slope_id>")
def slope_detail(slope_id: int):
    slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (slope_id,))
    if not slope:
        return err("not_found", 404, "slope not found")
    latest = db.query_one(
        "SELECT * FROM assessments WHERE slope_id = ? ORDER BY id DESC LIMIT 1", (slope_id,))
    neighbors = db.query(
        """SELECT e.*, s.code AS neighbor_code, s.name AS neighbor_name, s.lat, s.lng
           FROM slope_edges e
           JOIN slopes s ON s.id = CASE WHEN e.src_slope_id = ? THEN e.dst_slope_id ELSE e.src_slope_id END
           WHERE e.src_slope_id = ? OR e.dst_slope_id = ?
           ORDER BY e.strength DESC""",
        (slope_id, slope_id, slope_id),
    )
    reports = db.query(
        "SELECT * FROM citizen_reports WHERE slope_id = ? ORDER BY created_at DESC LIMIT 20",
        (slope_id,))
    observations = db.query(
        "SELECT * FROM field_observations WHERE slope_id = ? ORDER BY observed_at DESC LIMIT 10",
        (slope_id,))
    rain = db.query(
        """SELECT r.* FROM rainfall_observations r
           JOIN (SELECT station_code, MAX(observed_at) mx FROM rainfall_observations GROUP BY station_code) m
             ON m.station_code = r.station_code AND m.mx = r.observed_at
           ORDER BY r.station_code""",
    )
    from gis.geo import haversine_km

    for r in rain:
        r["distance_km"] = round(haversine_km(slope["lat"], slope["lng"], r["lat"], r["lng"]), 2)
    rain.sort(key=lambda r: r["distance_km"])
    sensors = db.query(
        "SELECT * FROM sensor_readings WHERE slope_id = ? ORDER BY observed_at DESC LIMIT 10",
        (slope_id,))
    evidence = []
    if latest:
        evidence = db.query(
            "SELECT * FROM assessment_evidence WHERE assessment_id = ? ORDER BY id", (latest["id"],))
    return ok({
        "slope": slope,
        "assessment": latest,
        "evidence": evidence,
        "neighbors": neighbors,
        "reports": reports,
        "field_observations": observations,
        "nearest_rainfall": rain[:3],
        "sensors": sensors,
    })


@bp.get("/slopes/<int:slope_id>/history")
def slope_history(slope_id: int):
    limit = int_arg("limit", 60)
    history = db.query(
        """SELECT h.*, a.final_score, a.confidence, a.flags
           FROM slope_risk_history h
           LEFT JOIN assessments a ON a.id = h.assessment_id
           WHERE h.slope_id = ? ORDER BY h.id DESC LIMIT ?""",
        (slope_id, limit))
    assessments = db.query(
        """SELECT id, fused_probability, direct_score, inherited_influence, uplift, final_score,
                  risk_band, confidence, flags, created_at
           FROM assessments WHERE slope_id = ? ORDER BY id DESC LIMIT ?""",
        (slope_id, limit))
    return ok({"history": history, "assessments": assessments})


@bp.get("/slopes/<int:slope_id>/risk")
def slope_risk(slope_id: int):
    """Risk view for one slope: latest assessment, history, evidence rollup, context."""
    slope = db.query_one("SELECT id, code, name, lat, lng, district, state FROM slopes WHERE id = ?",
                         (slope_id,))
    if not slope:
        return err("not_found", 404, "slope not found")
    latest = db.query_one(
        "SELECT * FROM assessments WHERE slope_id = ? ORDER BY id DESC LIMIT 1", (slope_id,))
    history = db.query(
        """SELECT risk_band, probability, from_band, allowed, changed_at
           FROM slope_risk_history WHERE slope_id = ? ORDER BY id DESC LIMIT 30""", (slope_id,))
    evidence: list[dict] = []
    rollup: dict[str, int] = {}
    if latest:
        evidence = db.query(
            "SELECT evidence_type, source_code, label, status, reason, effective_weight, "
            "probability, observed_at FROM assessment_evidence WHERE assessment_id = ? ORDER BY id",
            (latest["id"],))
        for e in evidence:
            rollup[e["status"]] = rollup.get(e["status"], 0) + 1
    alert = db.query_one(
        "SELECT id, alert_code, status, severity, band, created_at FROM alerts "
        "WHERE slope_id = ? AND status IN ('open','acknowledged','dispatched') "
        "ORDER BY id DESC LIMIT 1", (slope_id,))
    return ok({"slope": slope, "assessment": latest, "history": history,
               "evidence": evidence, "evidence_rollup": rollup,
               "open_alert": alert,
               "missing_inputs": (latest or {}).get("missing_inputs")})


@bp.post("/slopes/<int:slope_id>/reanalyze")
@require("assessments:run")
def reanalyze_slope(slope_id: int):
    from services.pipeline import run_assessment

    if not db.query_one("SELECT id FROM slopes WHERE id = ?", (slope_id,)):
        return err("not_found", 404, "slope not found")
    try:
        result = run_assessment(slope_id, actor_id=current_user()["id"])
    except Exception as exc:
        return err("pipeline_error", 500, str(exc))
    return ok({"assessment_id": result["assessment_id"], "band": result["band"],
               "final_score": result["final_score"], "confidence": result["confidence"],
               "transition": result["transition"], "missing_inputs": result["missing_inputs"],
               "alert_created": result["alert_created"], "alert_note": result["alert_note"]}, 201)


@bp.get("/graph")
def graph():
    since = parse_since(request.args.get("since"))
    payload = cs.graph_for_map()
    if since:
        payload["nodes"] = [n for n in payload["nodes"] if not n.get("assessed_at") or n["assessed_at"] >= since]
    payload["generated_at"] = db.utcnow()
    return ok(payload)


@bp.get("/graph/edges")
def graph_edges():
    return ok({"edges": db.query(
        "SELECT * FROM slope_edges ORDER BY strength DESC LIMIT 1000")})
