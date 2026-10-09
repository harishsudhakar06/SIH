"""Alert endpoints with role-based acknowledgement and resolution."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, request

import auth as auth_mod
import db
from api.common import err, int_arg, ok, parse_since

bp = Blueprint("alerts_api", __name__, url_prefix="/api")

SEVERITY_RANK = {"high": 1, "very_high": 2, "confirmed": 3}


@bp.get("/alerts")
def list_alerts():
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    status = request.args.get("status")
    since = parse_since(request.args.get("since"))
    limit = int_arg("limit", 100)
    sql = """SELECT a.*, s.code AS slope_code, s.name AS slope_name, s.lat, s.lng,
                    s.district AS slope_district, s.state AS slope_state,
                    u.username AS acknowledged_by_name
             FROM alerts a
             JOIN slopes s ON s.id = a.slope_id
             LEFT JOIN users u ON u.id = a.acknowledged_by
             WHERE 1 = 1"""
    params: list = []
    if status:
        sql += " AND a.status = ?"
        params.append(status)
    if since:
        sql += " AND a.created_at >= ?"
        params.append(since)
    sql += " ORDER BY a.created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    return ok({"alerts": rows, "count": len(rows)})


@bp.get("/alerts/summary")
def alert_summary():
    counts = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM alerts GROUP BY status")}
    by_severity = {r["severity"]: r["n"] for r in db.query(
        "SELECT severity, COUNT(*) AS n FROM alerts GROUP BY severity")}
    latest = db.query(
        """SELECT a.id, a.alert_code, a.severity, a.band, a.status, a.title, a.created_at,
                  s.code AS slope_code, s.name AS slope_name
           FROM alerts a JOIN slopes s ON s.id = a.slope_id
           ORDER BY a.id DESC LIMIT 10""")
    return ok({"by_status": counts, "by_severity": by_severity, "latest": latest})


@bp.get("/alerts/<int:alert_id>")
def alert_detail(alert_id: int):
    alert = db.query_one(
        """SELECT a.*, s.code AS slope_code, s.name AS slope_name, s.lat, s.lng,
                  s.district AS slope_district, s.state AS slope_state,
                  u.username AS acknowledged_by_name
           FROM alerts a JOIN slopes s ON s.id = a.slope_id
           LEFT JOIN users u ON u.id = a.acknowledged_by
           WHERE a.id = ?""",
        (alert_id,))
    if not alert:
        return err("not_found", 404, "alert not found")
    events = db.query(
        """SELECT e.*, u.username FROM alert_events e
           LEFT JOIN users u ON u.id = e.user_id WHERE e.alert_id = ? ORDER BY e.id""",
        (alert_id,))
    incident = db.query_one("SELECT * FROM incidents WHERE alert_id = ? ORDER BY id LIMIT 1",
                            (alert_id,))
    return ok({"alert": alert, "events": events, "incident": incident})


def _event(alert_id: int, user: dict, action: str, note: str | None) -> None:
    db.execute(
        "INSERT INTO alert_events (alert_id, user_id, action, note, created_at) VALUES (?,?,?,?,?)",
        (alert_id, user["id"], action, note, db.utcnow()))


@bp.post("/alerts/<int:alert_id>/ack")
@auth_mod.require("alerts:ack")
def acknowledge(alert_id: int):
    alert = db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    if not alert:
        return err("not_found", 404, "alert not found")
    if alert["status"] in ("resolved",):
        return err("conflict", 409, "alert already resolved")
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    note = data.get("note")
    ts = db.utcnow()

    if alert["status"] == "open":
        db.execute(
            """UPDATE alerts SET status = 'acknowledged', acknowledged_by = ?,
                   acknowledged_at = ?, updated_at = ? WHERE id = ?""",
            (user["id"], ts, ts, alert_id))
    _event(alert_id, user, "acknowledged", note)

    # Demo convenience: the first acknowledgement materialises an incident
    # (unless one already exists) so the response workflow has a record to act on.
    incident = db.query_one("SELECT * FROM incidents WHERE alert_id = ? ORDER BY id LIMIT 1",
                            (alert_id,))
    created = False
    if not incident:
        import uuid

        slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (alert["slope_id"],))
        incident_id = db.execute(
            """INSERT INTO incidents (incident_code, alert_id, slope_id, title, description,
                   severity, status, lat, lng, district, state, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"INC-{ts[:10].replace('-', '')}-{uuid.uuid4().hex[:6].upper()}",
             alert_id, alert["slope_id"], alert["title"], alert["message"],
             alert["severity"], "acknowledged", slope["lat"], slope["lng"],
             slope["district"], slope["state"], user["id"], ts, ts))
        db.execute(
            "INSERT INTO incident_events (incident_id, user_id, event_type, note, created_at) VALUES (?,?,?,?,?)",
            (incident_id, user["id"], "created", f"auto-created on acknowledgement of {alert['alert_code']}", ts))
        incident = db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))
        created = True

    auth_mod.audit(user["id"], "alert.ack", "alerts", alert_id, {"note": note})
    updated = db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    return ok({"alert": updated, "incident": incident, "incident_created": created})


@bp.post("/alerts/<int:alert_id>/resolve")
@auth_mod.require("alerts:resolve")
def resolve(alert_id: int):
    alert = db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    if not alert:
        return err("not_found", 404, "alert not found")
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    note = data.get("note")
    ts = db.utcnow()
    db.execute("UPDATE alerts SET status = 'resolved', resolved_at = ?, updated_at = ? WHERE id = ?",
               (ts, ts, alert_id))
    _event(alert_id, user, "resolved", note)
    incident = db.query_one("SELECT * FROM incidents WHERE alert_id = ? ORDER BY id LIMIT 1",
                            (alert_id,))
    if incident and incident["status"] not in ("resolved", "closed"):
        db.execute("UPDATE incidents SET status = 'resolved', resolved_at = ?, updated_at = ? WHERE id = ?",
                   (ts, ts, incident["id"]))
        db.execute(
            "INSERT INTO incident_events (incident_id, user_id, event_type, from_status, to_status, note, created_at) VALUES (?,?,?,?,?,?,?)",
            (incident["id"], user["id"], "status_change", incident["status"], "resolved", note, ts))
    auth_mod.audit(user["id"], "alert.resolve", "alerts", alert_id, {"note": note})
    return ok({"alert": db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))})


# ------------------------------------------- spec aliases + notify + dispatch ----

def _ensure_incident(alert: dict, user: dict, ts: str, reason: str) -> tuple[dict, bool]:
    incident = db.query_one("SELECT * FROM incidents WHERE alert_id = ? ORDER BY id LIMIT 1",
                            (alert["id"],))
    if incident:
        return incident, False
    import uuid

    slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (alert["slope_id"],))
    incident_id = db.execute(
        """INSERT INTO incidents (incident_code, alert_id, slope_id, title, description,
               severity, status, lat, lng, district, state, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"INC-{ts[:10].replace('-', '')}-{uuid.uuid4().hex[:6].upper()}",
         alert["id"], alert["slope_id"], alert["title"], alert["message"],
         alert["severity"], "acknowledged", slope["lat"], slope["lng"],
         slope["district"], slope["state"], user["id"], ts, ts))
    db.execute(
        "INSERT INTO incident_events (incident_id, user_id, event_type, note, created_at) VALUES (?,?,?,?,?)",
        (incident_id, user["id"], "created", reason, ts))
    return db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,)), True


@bp.post("/alerts/<int:alert_id>/acknowledge")
@auth_mod.require("alerts:ack")
def acknowledge_spec(alert_id: int):
    """Spec-named alias of /alerts/<id>/ack (same behaviour, same audit trail)."""
    return acknowledge(alert_id)


@bp.post("/alerts/<int:alert_id>/notify")
@auth_mod.require("alerts:notify")
def notify_alert(alert_id: int):
    """Push a notification about this alert to every role that must act on it."""
    alert = db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    if not alert:
        return err("not_found", 404, "alert not found")
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    note = (data.get("note") or "").strip() or None
    roles = data.get("roles") or ["response_operator", "community_leader", "analyst"]
    roles = [r for r in roles if r in ("citizen", "field_volunteer", "community_leader",
                                       "response_operator", "analyst")]
    ts = db.utcnow()
    created = 0
    for role in roles:
        db.execute(
            """INSERT INTO notifications (user_id, role_scope, district, title, body, severity,
                  related_type, related_id, created_at)
               VALUES (NULL,?,?,?,?,?,?,?,?)""",
            (role, alert.get("scope_district"), alert["title"],
             (note + " | " if note else "") + alert["message"],
             "critical" if alert["band"] == "CONFIRMED" else "warning",
             "alert", alert_id, ts))
        created += 1
    _event(alert_id, user, "notified", f"roles: {', '.join(roles)}" + (f"; {note}" if note else ""))
    auth_mod.audit(user["id"], "alert.notify", "alerts", alert_id, {"roles": roles})
    return ok({"alert": db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,)),
               "notifications_created": created, "roles": roles}, 201)


@bp.post("/alerts/<int:alert_id>/dispatch")
@auth_mod.require("dispatch:create")
def dispatch_alert(alert_id: int):
    """Dispatch a response team for this alert (creates/uses the linked incident)."""
    alert = db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    if not alert:
        return err("not_found", 404, "alert not found")
    if alert["status"] == "resolved":
        return err("conflict", 409, "alert already resolved")
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    team_name = (data.get("team_name") or "").strip()
    if not team_name:
        return err("validation", 400, "team_name is required")
    ts = db.utcnow()
    incident, created = _ensure_incident(alert, user, ts, "incident opened for dispatch")
    dispatch_id = db.execute(
        """INSERT INTO dispatches (incident_id, team_name, personnel, eta_minutes, status,
              requested_by, note, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (incident["id"], team_name, data.get("personnel"), data.get("eta_minutes"),
         "requested", user["id"], data.get("note"), ts, ts))
    db.execute("UPDATE incidents SET status = 'dispatched', updated_at = ? WHERE id = ? "
               "AND status NOT IN ('resolved','closed')", (ts, incident["id"]))
    db.execute(
        "INSERT INTO incident_events (incident_id, user_id, event_type, from_status, to_status, note, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (incident["id"], user["id"], "dispatch", incident["status"], "dispatched",
         f"{team_name} requested via alert {alert['alert_code']}", ts))
    if alert["status"] in ("open", "acknowledged"):
        db.execute("UPDATE alerts SET status = 'dispatched', updated_at = ? WHERE id = ?",
                   (ts, alert_id))
    _event(alert_id, user, "dispatched", f"{team_name} via {alert['alert_code']}")
    auth_mod.audit(user["id"], "alert.dispatch", "alerts", alert_id,
                   {"team": team_name, "incident": incident["incident_code"]})
    return ok({
        "alert": db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,)),
        "incident": db.query_one("SELECT * FROM incidents WHERE id = ?", (incident["id"],)),
        "dispatch": db.query_one("SELECT * FROM dispatches WHERE id = ?", (dispatch_id,)),
        "incident_created": created,
    }, 201)
