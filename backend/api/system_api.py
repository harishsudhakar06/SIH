"""System info, dashboard KPIs, notifications and audit endpoints."""
from __future__ import annotations

from flask import Blueprint, request

import auth as auth_mod
import db
from api.common import err, int_arg, ok, parse_since
from config import config, detect_lan_ip

bp = Blueprint("system_api", __name__, url_prefix="/api")


@bp.get("/system/info")
def system_info():
    counts = {
        "slopes": db.query_one("SELECT COUNT(*) AS n FROM slopes")["n"],
        "edges": db.query_one("SELECT COUNT(*) AS n FROM slope_edges")["n"],
        "reports": db.query_one("SELECT COUNT(*) AS n FROM citizen_reports")["n"],
        "assessments": db.query_one("SELECT COUNT(*) AS n FROM assessments")["n"],
        "alerts": db.query_one("SELECT COUNT(*) AS n FROM alerts")["n"],
        "incidents": db.query_one("SELECT COUNT(*) AS n FROM incidents")["n"],
        "users": db.query_one("SELECT COUNT(*) AS n FROM users")["n"],
    }
    model_version = None
    try:
        from ml import predict

        model_version = predict.model_info()["version"]
    except Exception:
        pass
    return ok({
        "app": "BhuRaksha",
        "problem_statement": "SIH26001 - Connected Slope Intelligence",
        "engine_version": config.ENGINE_VERSION,
        "server": {"lan_ip": detect_lan_ip(), "host": config.HOST, "port": config.PORT},
        "counts": counts,
        "model_version": model_version,
        "tile_mode": "offline-cache+proxy" if not config.TILE_OFFLINE else "local-only",
        "canned_endpoints": {"polling_interval_s": 5},
        "data_disclaimer": (
            "Rainfall/sensor values are prototype fixtures; satellite is unavailable. "
            "ML metrics are from a synthetic prototype dataset."
        ),
    })


@bp.get("/dashboard")
def dashboard():
    band_rows = db.query(
        """SELECT COALESCE(a.risk_band, 'UNASSESSED') AS band, COUNT(*) AS n
           FROM slopes s LEFT JOIN assessments a ON a.id = (
               SELECT id FROM assessments WHERE slope_id = s.id ORDER BY id DESC LIMIT 1)
           GROUP BY band""")
    bands = {r["band"]: r["n"] for r in band_rows}
    alerts = db.query(
        "SELECT status, COUNT(*) AS n FROM alerts GROUP BY status")
    incidents = db.query(
        "SELECT status, COUNT(*) AS n FROM incidents GROUP BY status")
    reports_24h = db.query_one(
        "SELECT COUNT(*) AS n FROM citizen_reports WHERE created_at >= datetime('now','-1 day')")["n"]
    assessments_24h = db.query_one(
        "SELECT COUNT(*) AS n FROM assessments WHERE created_at >= datetime('now','-1 day')")["n"]
    top = db.query(
        """SELECT a.slope_id, s.code, s.name, s.lat, s.lng, s.district, s.state,
                  a.risk_band, a.final_score, a.confidence, a.recommendation, a.created_at
           FROM assessments a JOIN slopes s ON s.id = a.slope_id
           WHERE a.id IN (SELECT id FROM assessments GROUP BY slope_id HAVING MAX(id))
           ORDER BY a.final_score DESC LIMIT 12""")
    feed = db.query(
        """SELECT 'report' AS kind, id, title, created_at, status FROM citizen_reports
           UNION ALL
           SELECT 'alert' AS kind, id, title, created_at, status FROM alerts
           UNION ALL
           SELECT 'incident' AS kind, id, title, created_at, status FROM incidents
           ORDER BY created_at DESC LIMIT 25""")
    open_alerts = db.query(
        """SELECT a.*, s.code AS slope_code, s.name AS slope_name, s.lat, s.lng
           FROM alerts a JOIN slopes s ON s.id = a.slope_id
           WHERE a.status IN ('open','acknowledged','dispatched')
           ORDER BY a.created_at DESC LIMIT 20""")
    return ok({
        "bands": bands,
        "alerts_by_status": {r["status"]: r["n"] for r in alerts},
        "incidents_by_status": {r["status"]: r["n"] for r in incidents},
        "reports_24h": reports_24h,
        "assessments_24h": assessments_24h,
        "top_risks": top,
        "activity": feed,
        "open_alerts": open_alerts,
        "generated_at": db.utcnow(),
    })


@bp.get("/notifications")
def notifications():
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    since = parse_since(request.args.get("since"))
    unread_only = request.args.get("unread") == "1"
    limit = int_arg("limit", 100)
    sql = """SELECT * FROM notifications
             WHERE (user_id = ? OR (user_id IS NULL AND role_scope = ?))"""
    params: list = [user["id"], user["role"]]
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    if unread_only:
        sql += " AND read = 0"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    unread = db.query_one(
        """SELECT COUNT(*) AS n FROM notifications
           WHERE read = 0 AND (user_id = ? OR (user_id IS NULL AND role_scope = ?))""",
        (user["id"], user["role"]))["n"]
    return ok({"notifications": rows, "count": len(rows), "unread": unread})


@bp.post("/notifications/read")
def mark_read():
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    ts_mark_all = data.get("all") is True
    if ts_mark_all:
        db.execute(
            """UPDATE notifications SET read = 1
               WHERE (user_id = ? OR (user_id IS NULL AND role_scope = ?)) AND read = 0""",
            (user["id"], user["role"]))
    elif isinstance(ids, list) and ids:
        marks = ",".join("?" for _ in ids)
        db.execute(
            f"UPDATE notifications SET read = 1 WHERE id IN ({marks}) AND "
            "(user_id = ? OR (user_id IS NULL AND role_scope = ?))",
            tuple(ids) + (user["id"], user["role"]))
    return ok({"status": "ok"})


@bp.post("/notifications/broadcast")
def broadcast():
    """Community warning / operations broadcast (role-restricted)."""
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    if user["role"] not in ("community_leader", "response_operator", "analyst", "admin"):
        return err("forbidden", 403, "broadcast is limited to community leaders and response roles")
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    body = (data.get("body") or "").strip()
    if not title or not body:
        return err("validation", 400, "title and body are required")
    role_scope = data.get("role_scope") or "citizen"
    if role_scope not in ("citizen", "field_volunteer", "community_leader", "response_operator", "analyst"):
        return err("validation", 400, "invalid role_scope")
    district = data.get("district") or user.get("district")
    db.execute(
        """INSERT INTO notifications (user_id, role_scope, district, title, body, severity,
              related_type, related_id, created_at)
           VALUES (NULL,?,?,?,?,?,?,?,?)""",
        (role_scope, district, title, body, data.get("severity", "warning"),
         "broadcast", None, db.utcnow()))
    auth_mod.audit(user["id"], "notification.broadcast", "notifications", None,
                   {"role_scope": role_scope, "district": district})
    return ok({"status": "sent", "role_scope": role_scope, "district": district}, 201)


@bp.get("/system/health")
def system_health():
    """Liveness/readiness probe required by the platform spec (no secrets exposed)."""
    checks: dict[str, str] = {}
    overall = "ok"
    try:
        db.query_one("SELECT 1 AS ok")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        overall = "degraded"
    try:
        from ml import predict

        info = predict.model_info()
        checks["ml_model"] = f"ok ({info.get('version')})"
    except Exception as exc:
        checks["ml_model"] = f"unavailable: {exc}"
        overall = "degraded"
    try:
        n = db.query_one("SELECT COUNT(*) AS n FROM slopes")["n"]
        checks["slope_inventory"] = f"ok ({n} slopes)"
        if n == 0:
            overall = "degraded"
    except Exception as exc:
        checks["slope_inventory"] = f"error: {exc}"
        overall = "degraded"
    uploads = config.UPLOAD_DIR
    checks["uploads_dir"] = "ok" if uploads.exists() else "missing"
    if not uploads.exists():
        overall = "degraded"
    return ok({"status": overall, "checks": checks, "time": db.utcnow(),
               "engine_version": config.ENGINE_VERSION,
               "lan_ip": detect_lan_ip(), "port": config.PORT})


@bp.get("/system/data-status")
def system_data_status():
    """Per-source availability, freshness and ingestion errors."""
    from services import data_quality

    summary = data_quality.summary()
    summary["ingestion_errors"] = db.query(
        """SELECT source_code, status, detail, created_at FROM ingestion_log
           WHERE status IN ('error','degraded') ORDER BY id DESC LIMIT 20""")
    summary["analysis_jobs"] = {
        "total": db.query_one("SELECT COUNT(*) AS n FROM analysis_jobs")["n"],
        "failed": db.query_one("SELECT COUNT(*) AS n FROM analysis_jobs WHERE status = 'failed'")["n"],
        "last_finished": (db.query_one(
            "SELECT MAX(finished_at) AS t FROM analysis_jobs") or {}).get("t"),
    }
    return ok(summary)


@bp.post("/notifications/<int:notification_id>/acknowledge")
def acknowledge_notification(notification_id: int):
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    row = db.query_one("SELECT * FROM notifications WHERE id = ?", (notification_id,))
    if not row:
        return err("not_found", 404, "notification not found")
    if row["user_id"] not in (None, user["id"]):
        return err("forbidden", 403, "notification belongs to another user")
    if row["user_id"] is None and row["role_scope"] != user["role"]:
        return err("forbidden", 403, "notification was scoped to a different role")
    db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))
    return ok({"notification": db.query_one("SELECT * FROM notifications WHERE id = ?",
                                            (notification_id,))})


@bp.get("/audit")
@auth_mod.require("audit:view")
def audit_log():
    limit = int_arg("limit", 200)
    rows = db.query(
        """SELECT a.*, u.username FROM audit_log a
           LEFT JOIN users u ON u.id = a.user_id
           ORDER BY a.id DESC LIMIT ?""",
        (limit,))
    return ok({"entries": rows, "count": len(rows)})
