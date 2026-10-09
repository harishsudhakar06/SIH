"""District Command, Emergency Response and Technical/Geologist read APIs."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, request

import auth as auth_mod
import db
from api.common import err, int_arg, ok, parse_since

bp = Blueprint("command_api", __name__, url_prefix="/api")

BANDS = ("LOW", "MODERATE", "HIGH", "VERY_HIGH", "CONFIRMED")


@bp.get("/command/overview")
@auth_mod.require("command:view")
def command_overview():
    """Regional risk distribution + operational picture for the District Command app."""
    district = request.args.get("district")
    status = request.args.get("status")
    severity = request.args.get("severity")
    since = parse_since(request.args.get("since"))

    band_rows = db.query(
        """SELECT COALESCE(a.risk_band, 'UNASSESSED') AS band, COUNT(*) AS n
           FROM slopes s
           LEFT JOIN assessments a ON a.id = (
               SELECT id FROM assessments WHERE slope_id = s.id ORDER BY id DESC LIMIT 1)
           GROUP BY band""")
    bands = {r["band"]: r["n"] for r in band_rows}

    district_rows = db.query(
        """SELECT s.district, COUNT(*) AS slopes,
                  SUM(CASE WHEN a.risk_band IN ('HIGH','VERY_HIGH','CONFIRMED') THEN 1 ELSE 0 END)
                      AS at_risk,
                  ROUND(COALESCE(MAX(a.final_score), 0), 3) AS max_score
           FROM slopes s
           LEFT JOIN assessments a ON a.id = (
               SELECT id FROM assessments WHERE slope_id = s.id ORDER BY id DESC LIMIT 1)
           GROUP BY s.district ORDER BY at_risk DESC, max_score DESC""")

    alert_sql = """SELECT a.*, s.code AS slope_code, s.name AS slope_name, s.lat, s.lng,
                          s.district AS slope_district
                   FROM alerts a JOIN slopes s ON s.id = a.slope_id WHERE 1=1"""
    alert_params: list = []
    if district:
        alert_sql += " AND s.district = ?"
        alert_params.append(district)
    if status:
        alert_sql += " AND a.status = ?"
        alert_params.append(status)
    if severity:
        alert_sql += " AND a.severity = ?"
        alert_params.append(severity)
    if since:
        alert_sql += " AND a.created_at >= ?"
        alert_params.append(since)
    alert_sql += " ORDER BY a.created_at DESC LIMIT 100"
    alerts = db.query(alert_sql, tuple(alert_params))

    incident_sql = """SELECT i.*, s.code AS slope_code, s.name AS slope_name
                      FROM incidents i LEFT JOIN slopes s ON s.id = i.slope_id WHERE 1=1"""
    incident_params: list = []
    if district:
        incident_sql += " AND i.district = ?"
        incident_params.append(district)
    if status:
        incident_sql += " AND i.status = ?"
        incident_params.append(status)
    if since:
        incident_sql += " AND i.created_at >= ?"
        incident_params.append(since)
    incident_sql += " ORDER BY i.created_at DESC LIMIT 100"
    incidents = db.query(incident_sql, tuple(incident_params))

    # unacknowledged = open alerts nobody has acted on yet
    unack = [a for a in alerts if a["status"] == "open"]

    # response assignments: active dispatches grouped by team (real records only)
    assignments = db.query(
        """SELECT d.*, i.incident_code, i.title AS incident_title, i.status AS incident_status,
                  s.code AS slope_code
           FROM dispatches d
           JOIN incidents i ON i.id = d.incident_id
           LEFT JOIN slopes s ON s.id = i.slope_id
           WHERE d.status IN ('requested','enroute','on_scene')
           ORDER BY d.created_at DESC LIMIT 50""")

    # resource availability: teams derived from dispatch history (labelled as records)
    teams = db.query(
        """SELECT team_name, COUNT(*) AS total_dispatches,
                  SUM(CASE WHEN status IN ('requested','enroute') THEN 1 ELSE 0 END) AS active,
                  MAX(created_at) AS last_used
           FROM dispatches GROUP BY team_name ORDER BY total_dispatches DESC""")

    history = db.query(
        """SELECT 'alert' AS kind, alert_code AS code, title, status, created_at FROM alerts
           UNION ALL
           SELECT 'incident', incident_code, title, status, created_at FROM incidents
           ORDER BY created_at DESC LIMIT 40""")

    return ok({
        "bands": bands,
        "districts": district_rows,
        "alerts": alerts,
        "unacknowledged_count": len(unack),
        "unacknowledged": unack[:20],
        "incidents": incidents,
        "assignments": assignments,
        "resources": teams,
        "resource_note": "Availability derived from persisted dispatch records only.",
        "history": history,
        "filters": {"district": district, "status": status, "severity": severity,
                    "since": since},
        "generated_at": db.utcnow(),
    })


@bp.get("/emergency/incidents")
@auth_mod.require("command:view")
def emergency_incidents():
    """Active incidents + critical alerts for the Emergency Response app."""
    active = db.query(
        """SELECT i.*, s.code AS slope_code, s.name AS slope_name,
                  (SELECT COUNT(*) FROM dispatches d WHERE d.incident_id = i.id) AS dispatch_count,
                  (SELECT COUNT(*) FROM incident_events e WHERE e.incident_id = i.id) AS event_count
           FROM incidents i LEFT JOIN slopes s ON s.id = i.slope_id
           WHERE i.status NOT IN ('closed')
           ORDER BY CASE i.status WHEN 'reported' THEN 0 WHEN 'verifying' THEN 1
                                  WHEN 'acknowledged' THEN 2 WHEN 'dispatched' THEN 3
                                  WHEN 'responding' THEN 4 ELSE 5 END,
                    i.created_at DESC
           LIMIT 100""")
    critical = db.query(
        """SELECT a.*, s.code AS slope_code, s.name AS slope_name, s.lat, s.lng,
                  s.district AS slope_district
           FROM alerts a JOIN slopes s ON s.id = a.slope_id
           WHERE a.status IN ('open','acknowledged','dispatched')
             AND a.severity IN ('very_high','confirmed')
           ORDER BY a.created_at DESC LIMIT 50""")
    assignments = db.query(
        """SELECT d.*, i.incident_code, i.title AS incident_title
           FROM dispatches d JOIN incidents i ON i.id = d.incident_id
           ORDER BY d.created_at DESC LIMIT 50""")
    return ok({"incidents": active, "critical_alerts": critical, "assignments": assignments,
               "generated_at": db.utcnow()})


@bp.get("/technical/slopes")
@auth_mod.require("technical:view")
def technical_slopes():
    """Slope inventory with terrain attributes, evidence quality and graph context."""
    rows = db.query(
        """SELECT s.*,
                  a.risk_band, a.final_score, a.confidence, a.fused_probability,
                  a.direct_score, a.inherited_influence, a.uplift,
                  a.accepted_count, a.rejected_count, a.missing_inputs, a.flags,
                  a.recommendation, a.created_at AS assessed_at,
                  (SELECT COUNT(*) FROM slope_edges e
                    WHERE e.src_slope_id = s.id OR e.dst_slope_id = s.id) AS edge_count,
                  (SELECT COUNT(*) FROM citizen_reports r WHERE r.slope_id = s.id) AS report_count,
                  (SELECT COUNT(*) FROM field_observations f WHERE f.slope_id = s.id) AS observation_count,
                  (SELECT COUNT(*) FROM sensor_readings sr WHERE sr.slope_id = s.id) AS sensor_reading_count,
                  (SELECT COUNT(*) FROM satellite_observations so
                    WHERE so.slope_id = s.id AND so.status = 'available') AS satellite_available
           FROM slopes s
           LEFT JOIN assessments a ON a.id = (
               SELECT id FROM assessments WHERE slope_id = s.id ORDER BY id DESC LIMIT 1)
           ORDER BY s.code""")
    for r in rows:
        missing = r.get("missing_inputs")
        if isinstance(missing, str) and missing:
            try:
                import json

                r["missing_inputs"] = json.loads(missing)
            except ValueError:
                r["missing_inputs"] = [missing]
        elif not missing:
            r["missing_inputs"] = []
    return ok({"slopes": rows, "count": len(rows),
               "data_mode_note": "Rainfall/sensor/terrain inputs are prototype fixtures; "
                                 "satellite is unavailable unless explicitly flagged."})
