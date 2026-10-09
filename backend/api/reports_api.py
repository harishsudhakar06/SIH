"""Citizen report submission (with real photo upload), field verification, tasks."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, request
from werkzeug.utils import secure_filename

import auth as auth_mod
import db
from api.common import err, int_arg, ok, parse_since, rate_limit
from config import config
from gis.geo import haversine_km
from services.analysis_job import STAGES as ANALYSIS_STAGES

bp = Blueprint("reports_api", __name__, url_prefix="/api")

ALLOWED_CATEGORIES = {"crack", "erosion", "debris_flow", "subsidence", "water_seepage", "rockfall", "other"}
AUTO_INCIDENT_CATEGORIES = {"rockfall", "debris_flow", "subsidence"}
NEAREST_SLOPE_RADIUS_KM = 3.0
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "heic"}
VIDEO_EXTS = {"mp4", "webm", "ogg", "mov", "m4v"}
MAX_MEDIA_FILES = 6


def _classify(filename: str) -> str | None:
    ext = filename.rsplit(".", 1)[1].lower() if "." in filename else ""
    if ext in IMAGE_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def _save_upload(file_storage, prefix: str) -> tuple[str, str, str]:
    """Persist an uploaded photograph; returns (relative_path, sha256, origin)."""
    if file_storage is None or not file_storage.filename:
        raise ValueError("no file")
    filename = secure_filename(file_storage.filename) or "upload.jpg"
    kind = _classify(filename)
    if kind is None:
        raise ValueError("unsupported media type; use jpg/png/webp images or mp4/webm/ogg video")
    limit = config.MAX_VIDEO_BYTES if kind == "video" else config.MAX_UPLOAD_BYTES
    data = file_storage.read()
    if len(data) > limit:
        size_mb = limit // (1024 * 1024)
        raise ValueError(f"{kind} larger than {size_mb} MB")
    digest = hashlib.sha256(data).hexdigest()
    ext = filename.rsplit(".", 1)[1].lower()
    stored = f"{prefix}_{digest[:16]}.{ext}"
    target = Path(config.UPLOAD_DIR) / stored
    if not target.exists():
        target.write_bytes(data)
    return stored, digest, "user_upload"


def _save_all_uploads(files: list) -> list[dict]:
    """Store up to MAX_MEDIA_FILES photos/videos; caller has already validated types."""
    saved: list[dict] = []
    for fs in files:
        if not fs or not fs.filename:
            continue
        kind = _classify(secure_filename(fs.filename) or fs.filename)
        if kind is None:
            raise ValueError("unsupported media type; use jpg/png/webp images or mp4/webm/ogg video")
        limit = config.MAX_VIDEO_BYTES if kind == "video" else config.MAX_UPLOAD_BYTES
        data = fs.read()
        if len(data) > limit:
            size_mb = limit // (1024 * 1024)
            raise ValueError(f"{kind} file larger than {size_mb} MB")
        digest = hashlib.sha256(data).hexdigest()
        ext = (secure_filename(fs.filename) or "f.bin").rsplit(".", 1)[1].lower()
        stored = f"report_{digest[:16]}.{ext}"
        target = Path(config.UPLOAD_DIR) / stored
        if not target.exists():
            target.write_bytes(data)
        saved.append({"kind": kind, "path": stored, "sha256": digest,
                      "bytes": len(data), "mime": fs.mimetype})
    if len(saved) > MAX_MEDIA_FILES:
        raise ValueError(f"at most {MAX_MEDIA_FILES} media files per report")
    return saved


def _nearest_slope(lat: float, lng: float) -> tuple[dict | None, float | None]:
    slopes = db.query("SELECT id, code, name, lat, lng, district, state FROM slopes")
    if not slopes:
        return None, None
    best = min(slopes, key=lambda s: haversine_km(lat, lng, s["lat"], s["lng"]))
    d = haversine_km(lat, lng, best["lat"], best["lng"])
    if d <= NEAREST_SLOPE_RADIUS_KM:
        return best, round(d, 3)
    return None, round(d, 3)


@bp.post("/reports")
@auth_mod.require("reports:create")
@rate_limit("report_create", 20, 60)
def create_report():
    user = auth_mod.current_user()
    form = request.form
    files = [f for f in (request.files.values() if request.files else []) if f and f.filename]

    title = (form.get("title") or "").strip()
    category = (form.get("category") or "other").strip()
    description = (form.get("description") or "").strip()
    if not title:
        return err("validation", 400, "title is required")
    if category not in ALLOWED_CATEGORIES:
        return err("validation", 400, f"category must be one of {sorted(ALLOWED_CATEGORIES)}")

    gps_source = (form.get("gps_source") or "unavailable").strip()
    if gps_source not in {"device", "manual", "fallback_configured", "unavailable"}:
        gps_source = "manual"
    try:
        lat = float(form.get("lat"))
        lng = float(form.get("lng"))
    except (TypeError, ValueError):
        if gps_source in {"device", "manual"}:
            return err("validation", 400, "lat/lng required when gps_source is device or manual")
        user_lat, user_lng = user.get("lat"), user.get("lng")
        if user_lat is None or user_lng is None:
            return err("validation", 400,
                       "no GPS fix and no configured fallback location - cannot place the report")
        lat, lng = float(user_lat), float(user_lng)
        gps_source = "fallback_configured"

    observed_at = form.get("observed_at") or db.utcnow()
    slope_id = form.get("slope_id")
    distance = None
    slope = None
    if slope_id:
        slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (int(slope_id),))
        if not slope:
            return err("validation", 400, "unknown slope_id")
    else:
        slope, distance = _nearest_slope(lat, lng)

    # media first: validate everything before any row is written
    try:
        media = _save_all_uploads(files)
    except ValueError as exc:
        return err("validation", 400, str(exc))
    first_photo = next((m for m in media if m["kind"] == "photo"), None)
    photo_path = first_photo["path"] if first_photo else None
    photo_hash = first_photo["sha256"] if first_photo else None

    ts = db.utcnow()
    report_id = db.execute(
        """INSERT INTO citizen_reports (report_code, slope_id, reporter_user, title, description,
               category, observed_at, lat, lng, gps_source, photo_path, photo_hash, photo_origin,
               severity_hint, status, is_duplicate, data_mode, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"RPT-{ts[:10].replace('-', '')}-{uuid.uuid4().hex[:6].upper()}",
         slope["id"] if slope else None, user["id"], title, description, category,
         observed_at, lat, lng, gps_source, photo_path, photo_hash,
         "user_upload" if photo_path else None,
         form.get("severity_hint"), "received", 0, "live", ts),
    )
    for m in media:
        db.execute(
            """INSERT INTO report_media (report_id, kind, path, sha256, origin, bytes, mime, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (report_id, m["kind"], m["path"], m["sha256"], "user_upload", m["bytes"], m["mime"], ts))
    auth_mod.audit(user["id"], "report.create", "citizen_reports", report_id,
                   {"slope_id": slope["id"] if slope else None, "gps_source": gps_source,
                    "media": len(media)})

    # duplicate marking against an existing report on the same slope in the last 6 h
    duplicate_of = None
    if slope:
        window = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        existing = db.query_one(
            """SELECT id FROM citizen_reports
               WHERE slope_id = ? AND id <> ? AND created_at >= ?
                 AND (lat - ?) * (lat - ?) + (lng - ?) * (lng - ?) < 0.00001
               ORDER BY id LIMIT 1""",
            (slope["id"], report_id, window, lat, lat, lng, lng),
        )
        if existing:
            duplicate_of = existing["id"]
            db.execute("UPDATE citizen_reports SET is_duplicate = 1, duplicate_of = ? WHERE id = ?",
                       (duplicate_of, report_id))

    # staged analysis job: media -> GPS -> match -> evidence -> fusion -> alert
    from services.analysis_job import JobBusy, run_report_analysis

    job = None
    analysis_error = None
    try:
        job = run_report_analysis(report_id, actor_id=user["id"])
    except JobBusy as exc:
        analysis_error = str(exc)
    except Exception as exc:  # report is preserved and surfaced, never discarded
        analysis_error = str(exc)

    assessment = _brief_from_job(job)
    pipeline_error = analysis_error or (job or {}).get("error")

    incident = None
    if category in AUTO_INCIDENT_CATEGORIES or (assessment and assessment.get("alert_created")):
        incident_id = _create_incident(
            title=title, description=description,
            severity=(assessment or {}).get("band") or "moderate",
            slope=slope, report_id=report_id, lat=lat, lng=lng, user=user,
            alert_id=(assessment or {}).get("alert_id"),
        )
        incident = db.query_one("SELECT * FROM incidents WHERE id = ?", (incident_id,))

    report = db.query_one("SELECT * FROM citizen_reports WHERE id = ?", (report_id,))
    return ok({
        "report": report,
        "media": media,
        "slope_matched": slope,
        "distance_to_slope_km": distance,
        "duplicate_of": duplicate_of,
        "assessment": assessment,
        "analysis": job,
        "incident": incident,
        "pipeline_error": pipeline_error,
    }, 201)


def _load_outcome(stage: dict | None) -> dict:
    if not stage or not stage.get("outcome"):
        return {}
    try:
        return json.loads(stage["outcome"])
    except (TypeError, ValueError):
        return {}


def _brief_from_job(job: dict | None) -> dict | None:
    """Reconstruct the assessment brief from persisted analysis stages."""
    if not job:
        return None
    stages = {s["stage"]: s for s in job.get("stages", [])}
    fusion = _load_outcome(stages.get("belief_fusion"))
    risk = _load_outcome(stages.get("risk_decision"))
    alertd = _load_outcome(stages.get("alert_decision"))
    if not risk:
        return None
    return {
        "assessment_id": fusion.get("assessment_id"),
        "band": risk.get("band"),
        "previous_band": risk.get("previous_band"),
        "final_score": risk.get("final_score"),
        "confidence": risk.get("confidence"),
        "accepted": fusion.get("accepted"),
        "missing_inputs": fusion.get("missing_inputs", []),
        "recommendation": risk.get("recommendation"),
        "alert_created": alertd.get("alert_created", False),
        "alert_note": alertd.get("note"),
        "alert_id": alertd.get("alert_id"),
    }


@bp.post("/reports/<int:report_id>/analyze")
@auth_mod.require("reports:verify")
@rate_limit("report_analyze", 30, 60)
def analyze_report(report_id: int):
    """(Re)run the staged analysis for a stored report. Idempotent while a job is active."""
    report = db.query_one("SELECT * FROM citizen_reports WHERE id = ?", (report_id,))
    if not report:
        return err("not_found", 404, "report not found")
    from services.analysis_job import JobBusy, latest_job_for_report, run_report_analysis

    try:
        job = run_report_analysis(report_id, actor_id=auth_mod.current_user()["id"])
    except JobBusy as exc:
        return err("conflict", 409, str(exc), job=latest_job_for_report(report_id))
    auth_mod.audit(auth_mod.current_user()["id"], "report.analyze", "analysis_jobs", job["id"],
                   {"report_id": report_id})
    return ok({"job": job, "assessment": _brief_from_job(job)}, 201)


@bp.get("/reports/<int:report_id>/analysis")
def report_analysis(report_id: int):
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    report = db.query_one("SELECT * FROM citizen_reports WHERE id = ?", (report_id,))
    if not report:
        return err("not_found", 404, "report not found")
    media = db.query("SELECT * FROM report_media WHERE report_id = ? ORDER BY id", (report_id,))
    from services.analysis_job import latest_job_for_report

    job = latest_job_for_report(report_id)
    return ok({"report": report, "media": media, "job": job,
               "stages_defined": [lbl for _k, lbl, _f in ANALYSIS_STAGES]})


def _brief(assessment: dict | None) -> dict | None:
    if not assessment:
        return None
    alert = assessment.get("alert")
    return {
        "assessment_id": assessment["assessment_id"],
        "band": assessment["band"],
        "previous_band": assessment["previous_band"],
        "final_score": assessment["final_score"],
        "confidence": assessment["confidence"],
        "accepted": assessment["accepted"],
        "missing_inputs": assessment["missing_inputs"],
        "recommendation": assessment["recommendation"],
        "alert_created": assessment["alert_created"],
        "alert_note": assessment["alert_note"],
        "alert_id": (alert or {}).get("id"),
    }


def _create_incident(*, title: str, description: str | None, severity: str,
                     slope: dict | None, report_id: int | None, lat: float, lng: float,
                     user: dict, alert_id: int | None) -> int:
    ts = db.utcnow()
    incident_id = db.execute(
        """INSERT INTO incidents (incident_code, alert_id, slope_id, report_id, title, description,
               severity, status, lat, lng, district, state, created_by, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"INC-{ts[:10].replace('-', '')}-{uuid.uuid4().hex[:6].upper()}",
         alert_id, slope["id"] if slope else None, report_id, title, description,
         severity, "reported", lat, lng,
         slope["district"] if slope else None, slope["state"] if slope else None,
         user["id"], ts, ts),
    )
    db.execute(
        "INSERT INTO incident_events (incident_id, user_id, event_type, note, created_at) VALUES (?,?,?,?,?)",
        (incident_id, user["id"], "created", "auto-created from citizen report", ts),
    )
    return incident_id


@bp.get("/reports")
def list_reports():
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    scope = request.args.get("scope", "mine")
    since = parse_since(request.args.get("since"))
    limit = int_arg("limit", 100)
    sql = """SELECT r.*, s.code AS slope_code, s.name AS slope_name, u.username AS reporter
             FROM citizen_reports r
             LEFT JOIN slopes s ON s.id = r.slope_id
             LEFT JOIN users u ON u.id = r.reporter_user
             WHERE 1 = 1"""
    params: list = []
    if scope == "mine" and user["role"] == "citizen":
        sql += " AND r.reporter_user = ?"
        params.append(user["id"])
    if since:
        sql += " AND r.created_at >= ?"
        params.append(since)
    sql += " ORDER BY r.created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    return ok({"reports": rows, "count": len(rows)})


@bp.get("/reports/<int:report_id>")
def report_detail(report_id: int):
    report = db.query_one(
        """SELECT r.*, s.code AS slope_code, s.name AS slope_name, u.username AS reporter
           FROM citizen_reports r
           LEFT JOIN slopes s ON s.id = r.slope_id
           LEFT JOIN users u ON u.id = r.reporter_user
           WHERE r.id = ?""",
        (report_id,))
    if not report:
        return err("not_found", 404, "report not found")
    observations = db.query(
        "SELECT * FROM field_observations WHERE report_id = ? ORDER BY observed_at DESC",
        (report_id,))
    return ok({"report": report, "field_observations": observations})


@bp.post("/reports/<int:report_id>/verify")
@auth_mod.require("reports:verify")
def verify_report(report_id: int):
    report = db.query_one("SELECT * FROM citizen_reports WHERE id = ?", (report_id,))
    if not report:
        return err("not_found", 404, "report not found")
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    status = data.get("verification_status")
    if status not in {"ground_truth", "inconclusive", "not_verified"}:
        return err("validation", 400, "verification_status must be ground_truth|inconclusive|not_verified")

    slope_id = data.get("slope_id") or report["slope_id"]
    if not slope_id:
        slope, _ = _nearest_slope(report["lat"], report["lng"])
        slope_id = slope["id"] if slope else None
    if not slope_id:
        return err("validation", 400, "cannot resolve a slope for this report")

    photo_path = photo_origin = None
    file = request.files.get("photo") if request.files else None
    if file and file.filename:
        try:
            photo_path, _, photo_origin = _save_upload(file, "field")
        except ValueError as exc:
            return err("validation", 400, str(exc))

    ts = db.utcnow()
    obs_id = db.execute(
        """INSERT INTO field_observations (report_id, slope_id, verifier_user, verification_status,
               crack_mm, seepage, fresh_scarp, displacement_m, material, notes, photo_path,
               photo_origin, lat, lng, gps_source, observed_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (report_id, slope_id, user["id"], status,
         data.get("crack_mm"), int(bool(data.get("seepage"))), int(bool(data.get("fresh_scarp"))),
         data.get("displacement_m"), data.get("material"), data.get("notes"),
         photo_path, photo_origin, data.get("lat"), data.get("lng"),
         data.get("gps_source"), data.get("observed_at") or ts, ts),
    )
    db.execute("UPDATE citizen_reports SET status = 'verified' WHERE id = ?", (report_id,))
    auth_mod.audit(user["id"], "report.verify", "field_observations", obs_id,
                   {"report_id": report_id, "status": status})

    assessment = None
    try:
        from services.pipeline import run_assessment

        assessment = run_assessment(int(slope_id), actor_id=user["id"])
    except Exception as exc:  # pragma: no cover
        return ok({"observation_id": obs_id, "assessment": None, "pipeline_error": str(exc)})

    return ok({"observation_id": obs_id, "assessment": _brief(assessment)}, 201)


@bp.get("/observations")
@auth_mod.require("reports:verify")
def list_observations():
    limit = int_arg("limit", 50)
    mine = request.args.get("mine") == "1"
    sql = """SELECT f.*, s.code AS slope_code, s.name AS slope_name, u.username AS verifier
             FROM field_observations f
             JOIN slopes s ON s.id = f.slope_id
             LEFT JOIN users u ON u.id = f.verifier_user
             WHERE 1 = 1"""
    params: list = []
    if mine:
        sql += " AND f.verifier_user = ?"
        params.append(auth_mod.current_user()["id"])
    sql += " ORDER BY f.id DESC LIMIT ?"
    params.append(limit)
    return ok({"observations": db.query(sql, tuple(params))})


@bp.post("/observations")
@auth_mod.require("reports:verify")
def create_observation():
    """Standalone field observation (no citizen report required)."""
    user = auth_mod.current_user()
    data = request.get_json(silent=True) or {}
    slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (data.get("slope_id"),))
    if not slope:
        return err("validation", 400, "slope_id required")
    status = data.get("verification_status")
    if status not in {"ground_truth", "inconclusive", "not_verified"}:
        return err("validation", 400, "verification_status must be ground_truth|inconclusive|not_verified")
    ts = db.utcnow()
    obs_id = db.execute(
        """INSERT INTO field_observations (report_id, slope_id, verifier_user, verification_status,
               crack_mm, seepage, fresh_scarp, displacement_m, material, notes, photo_path,
               photo_origin, lat, lng, gps_source, observed_at, created_at)
           VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (slope["id"], user["id"], status, data.get("crack_mm"), int(bool(data.get("seepage"))),
         int(bool(data.get("fresh_scarp"))), data.get("displacement_m"), data.get("material"),
         data.get("notes"), None, None, data.get("lat"), data.get("lng"), data.get("gps_source"),
         data.get("observed_at") or ts, ts))
    auth_mod.audit(user["id"], "observation.create", "field_observations", obs_id,
                   {"slope_id": slope["id"], "status": status})
    try:
        from services.pipeline import run_assessment
        assessment = run_assessment(slope["id"], actor_id=user["id"])
    except Exception as exc:
        return ok({"observation_id": obs_id, "assessment": None, "pipeline_error": str(exc)}, 201)
    return ok({"observation_id": obs_id, "assessment": _brief(assessment)}, 201)


@bp.get("/tasks/verification")
@auth_mod.require("reports:view_all")
def verification_tasks():
    """Field queue: slopes that need a human check (uncertainty-driven verification)."""
    rows = db.query(
        """SELECT s.id AS slope_id, s.code, s.name, s.lat, s.lng, s.district, s.state,
                  a.risk_band, a.final_score, a.confidence, a.flags, a.recommendation,
                  a.created_at AS assessed_at,
                  (SELECT COUNT(*) FROM field_observations f WHERE f.slope_id = s.id) AS obs_count
           FROM slopes s
           JOIN assessments a ON a.id = (
               SELECT id FROM assessments WHERE slope_id = s.id ORDER BY id DESC LIMIT 1)
           WHERE a.confidence < 0.55
              OR a.flags LIKE '%insufficient_evidence%'
              OR a.risk_band IN ('HIGH','VERY_HIGH')
           ORDER BY a.final_score DESC
           LIMIT 100"""
    )
    now = datetime.now(timezone.utc)
    tasks = []
    for r in rows:
        assessed = datetime.fromisoformat(r["assessed_at"].replace("Z", "+00:00"))
        age_h = (now - (assessed if assessed.tzinfo else assessed.replace(tzinfo=timezone.utc))).total_seconds() / 3600
        reason = []
        if r["confidence"] < 0.55:
            reason.append("low fusion confidence")
        if r["flags"] and "insufficient_evidence" in r["flags"]:
            reason.append("insufficient evidence")
        if r["risk_band"] in ("HIGH", "VERY_HIGH"):
            reason.append("elevated risk band")
        tasks.append({**r, "age_hours": round(age_h, 1), "reasons": reason,
                      "priority": "high" if r["risk_band"] in ("HIGH", "VERY_HIGH") else "normal"})
    return ok({"tasks": tasks, "count": len(tasks)})
