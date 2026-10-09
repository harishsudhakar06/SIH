"""Incident workflow + dispatch endpoints for the response teams."""
from __future__ import annotations

import uuid

from flask import Blueprint, request

import auth as auth_mod
import db
from api.common import err, int_arg, ok, parse_since

bp = Blueprint("incidents_api", __name__, url_prefix="/api")

# Controlled incident state machine (documented).
TRANSITIONS: dict[str, set[str]] = {
    "reported": {"verifying", "acknowledged", "closed"},
    "verifying": {"acknowledged", "resolved", "closed"},
    "acknowledged": {"dispatched", "responding", "resolved"},
    "dispatched": {"responding", "resolved"},
    "responding": {"resolved"},
    "resolved": {"closed"},
    "closed": set(),
}


@bp.get("/incidents")
def list_incidents():
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    status = request.args.get("status")
    since = parse_since(request.args.get("since"))
    limit = int_arg("limit", 100)
    sql = """SELECT i.*, s.code AS slope_code, s.name AS slope_name,
                    u.username AS created_by_name,
                    (SELECT COUNT(*) FROM incident_events e WHERE e.incident_id = i.id) AS event_count,
                    (SELECT COUNT(*) FROM dispatches d WHERE d.incident_id = i.id) AS dispatch_count
             FROM incidents i
             LEFT JOIN slopes s ON s.id = i.slope_id
             LEFT JOIN users u ON u.id = i.created_by
             WHERE 1 = 1"""
    params: list = []
    if status:
        sql += " AND i.status = ?"
        params.append(status)
    if since:
        sql += " AND i.created_at >= ?"
        params.append(since)
    sql += " ORDER BY i.created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    return ok({"incidents": rows, "count": len(rows)})


@bp.post("/incidents")
@auth_mod.require("incidents:create")
def create_incident():
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    if not title:
        return err("validation", 400, "title is required")
    slope_id = data.get("slope_id")
    slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (slope_id,)) if slope_id else None
    ts = db.utcnow()
    incident_id = db.execute(
        """INSERT INTO incidents (incident_code, alert_id, slope_id, report_id, title, description,
               severity, status, lat, lng, district, state, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"INC-{ts[:10].replace('-', '')}-{uuid.uuid4().hex[:6].upper()}",
         data.get("alert_id"), slope_id, data.get("report_id"), title, data.get("description"),
         data.get("severity", "moderate"), "reported",
         data.get("lat", slope["lat"] if slope else None),
         data.get("lng", slope["lng"] if slope else None),
         data.get("district", slope["district"] if slope else None),
         data.get("state", slope["state"] if slope else None),
         user["id"], ts, ts))
    db.execute(
        "INSERT INTO incident_events (incident_id, user_id, event_type, note, created_at) VALUES (?,?,?,?,?)",
        (incident_id, user["id"], "created", "created manually", ts))
    auth_mod.audit(user["id"], "incident.create", "incidents", incident_id, None)
    return ok({"incident": db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))}, 201)


@bp.get("/incidents/<int:incident_id>")
def incident_detail(incident_id: int):
    incident = db.query_one(
        """SELECT i.*, s.code AS slope_code, s.name AS slope_name, s.lat AS slope_lat, s.lng AS slope_lng,
                  u.username AS created_by_name
           FROM incidents i
           LEFT JOIN slopes s ON s.id = i.slope_id
           LEFT JOIN users u ON u.id = i.created_by
           WHERE i.id = ?""",
        (incident_id,))
    if not incident:
        return err("not_found", 404, "incident not found")
    events = db.query(
        """SELECT e.*, u.username FROM incident_events e
           LEFT JOIN users u ON u.id = e.user_id
           WHERE e.incident_id = ? ORDER BY e.id""",
        (incident_id,))
    dispatches = db.query(
        """SELECT d.*, u.username AS requested_by_name FROM dispatches d
           LEFT JOIN users u ON u.id = d.requested_by
           WHERE d.incident_id = ? ORDER BY d.id""",
        (incident_id,))
    report = None
    if incident["report_id"]:
        report = db.query_one("SELECT * FROM citizen_reports WHERE id = ?", (incident["report_id"],))
    return ok({"incident": incident, "events": events, "dispatches": dispatches, "report": report,
               "allowed_transitions": sorted(TRANSITIONS.get(incident["status"], set()))})


@bp.post("/incidents/<int:incident_id>/events")
@auth_mod.require("incidents:transition")
def add_event(incident_id: int):
    incident = db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    if not incident:
        return err("not_found", 404, "incident not found")
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    to_status = data.get("to_status")
    note = data.get("note")
    event_type = data.get("event_type", "status_change")

    if to_status:
        allowed = TRANSITIONS.get(incident["status"], set())
        if to_status not in allowed:
            return err("conflict", 409,
                       f"transition {incident['status']} -> {to_status} not allowed; "
                       f"allowed: {sorted(allowed)}")
        if to_status not in TRANSITIONS:
            return err("validation", 400, "unknown status")
        ts = db.utcnow()
        db.execute("UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
                   (to_status, ts, incident_id))
        if to_status in ("resolved", "closed"):
            db.execute("UPDATE incidents SET resolved_at = ? WHERE id = ? AND status IN ('resolved','closed')",
                       (ts, incident_id))
        if incident["alert_id"] and to_status in ("dispatched", "responding"):
            db.execute("UPDATE alerts SET status = 'dispatched', updated_at = ? WHERE id = ? AND status <> 'resolved'",
                       (ts, incident["alert_id"]))
        db.execute(
            """INSERT INTO incident_events (incident_id, user_id, event_type, from_status, to_status,
                   note, created_at) VALUES (?,?,?,?,?,?,?)""",
            (incident_id, user["id"], event_type, incident["status"], to_status, note, ts))
    else:
        db.execute(
            """INSERT INTO incident_events (incident_id, user_id, event_type, note, created_at)
               VALUES (?,?,?,?,?)""",
            (incident_id, user["id"], event_type, note, db.utcnow()))

    auth_mod.audit(user["id"], "incident.event", "incidents", incident_id,
                   {"to_status": to_status, "note": note})
    return ok({"incident": db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))})


@bp.post("/incidents/<int:incident_id>/dispatch")
@auth_mod.require("dispatch:create")
def create_dispatch(incident_id: int):
    incident = db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
    if not incident:
        return err("not_found", 404, "incident not found")
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    team = (data.get("team_name") or "").strip()
    if not team:
        return err("validation", 400, "team_name is required")
    ts = db.utcnow()
    dispatch_id = db.execute(
        """INSERT INTO dispatches (incident_id, team_name, personnel, eta_minutes, status,
               requested_by, note, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (incident_id, team, data.get("personnel"), data.get("eta_minutes"), "requested",
         user["id"], data.get("note"), ts, ts))
    new_status = "dispatched" if incident["status"] in ("reported", "verifying", "acknowledged") else incident["status"]
    if new_status != incident["status"]:
        db.execute("UPDATE incidents SET status = ?, updated_at = ? WHERE id = ?",
                   (new_status, ts, incident_id))
    db.execute(
        """INSERT INTO incident_events (incident_id, user_id, event_type, from_status, to_status,
               note, created_at) VALUES (?,?,?,?,?,?,?)""",
        (incident_id, user["id"], "dispatch", incident["status"], new_status,
         f"dispatched {team}" + (f" (ETA {data.get('eta_minutes')} min)" if data.get("eta_minutes") else ""),
         ts))
    if incident["alert_id"]:
        db.execute("UPDATE alerts SET status = 'dispatched', updated_at = ? WHERE id = ? AND status <> 'resolved'",
                   (ts, incident["alert_id"]))
    auth_mod.audit(user["id"], "incident.dispatch", "dispatches", dispatch_id, {"team": team})
    return ok({"dispatch": db.query_one("SELECT * FROM dispatches WHERE id = ?", (dispatch_id,)),
               "incident": db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))}, 201)


@bp.patch("/dispatches/<int:dispatch_id>")
@auth_mod.require("dispatch:create")
def update_dispatch(dispatch_id: int):
    dispatch = db.query_one("SELECT * FROM dispatches WHERE id = ?", (dispatch_id,))
    if not dispatch:
        return err("not_found", 404, "dispatch not found")
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status and status not in {"requested", "enroute", "on_scene", "completed", "cancelled"}:
        return err("validation", 400, "invalid dispatch status")
    ts = db.utcnow()
    db.execute("UPDATE dispatches SET status = COALESCE(?, status), note = COALESCE(?, note), "
               "updated_at = ? WHERE id = ?",
               (status, data.get("note"), ts, dispatch_id))
    incident = db.query_one("SELECT * FROM incidents WHERE id = ?", (dispatch["incident_id"],))
    if status == "enroute" and incident and incident["status"] == "dispatched":
        db.execute("UPDATE incidents SET status = 'responding', updated_at = ? WHERE id = ?",
                   (ts, incident["id"]))
    if status == "on_scene" and incident and incident["status"] in ("dispatched", "acknowledged"):
        db.execute("UPDATE incidents SET status = 'responding', updated_at = ? WHERE id = ?",
                   (ts, incident["id"]))
    db.execute(
        "INSERT INTO incident_events (incident_id, user_id, event_type, note, created_at) VALUES (?,?,?,?,?)",
        (dispatch["incident_id"], user["id"], "dispatch_update",
         f"dispatch #{dispatch_id} -> {status or 'updated'}", ts))
    return ok({"dispatch": db.query_one("SELECT * FROM dispatches WHERE id = ?", (dispatch_id,))})
