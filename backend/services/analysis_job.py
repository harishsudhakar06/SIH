"""Automated citizen-report analysis: explicit stages, timestamps, honest outcomes.

Implements the mandated workflow with one persisted row per stage so the laptop
Analysis page (and the API) can show exactly what ran, what was skipped because a
data source is unavailable, and what failed:

  NEW REPORT -> MEDIA STORED -> GPS VALIDATED -> LOCATION MATCHED -> SLOPE IDENTIFIED
  -> VISUAL EVIDENCE -> RAINFALL EVIDENCE -> TERRAIN EVIDENCE -> AVAILABLE SATELLITE
  EVIDENCE -> ML ASSESSMENT -> BELIEF FUSION -> CONNECTED-SLOPE EVALUATION
  -> RISK DECISION -> ALERT DECISION

Rules honoured here:
  * an unavailable data source is recorded as `skipped` with an explanation - it is
    never marked `ok`;
  * a failure never discards the report: the job records the error and the report
    stays queryable;
  * duplicate processing is prevented by an active-job guard + unique idempotency key.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import db
from config import config

# (stage_key, human label, fatal) - fatal stops the job, the rest are best-effort.
STAGES: list[tuple[str, str, bool]] = [
    ("new_report", "NEW REPORT", True),
    ("media_stored", "MEDIA STORED", False),
    ("gps_validated", "GPS VALIDATED", False),
    ("location_matched", "LOCATION MATCHED", False),
    ("slope_identified", "SLOPE IDENTIFIED", False),
    ("visual_evidence", "VISUAL EVIDENCE", False),
    ("rainfall_evidence", "RAINFALL EVIDENCE", False),
    ("terrain_evidence", "TERRAIN EVIDENCE", False),
    ("satellite_evidence", "AVAILABLE SATELLITE EVIDENCE", False),
    ("ml_assessment", "ML ASSESSMENT", False),
    ("belief_fusion", "BELIEF FUSION", True),
    ("connected_slope", "CONNECTED-SLOPE EVALUATION", False),
    ("risk_decision", "RISK DECISION", False),
    ("alert_decision", "ALERT DECISION", False),
]


class JobBusy(RuntimeError):
    """An analysis job for this report is already running."""


# --------------------------------------------------------------------- job helpers

def job_dict(job: dict[str, Any], stages: list[dict[str, Any]]) -> dict[str, Any]:
    return {**job, "stages": stages, "stage_labels": dict((k, lbl) for k, lbl, _ in STAGES)}


def latest_job_for_report(report_id: int) -> dict[str, Any] | None:
    job = db.query_one(
        "SELECT * FROM analysis_jobs WHERE report_id = ? ORDER BY id DESC LIMIT 1", (report_id,))
    if not job:
        return None
    return job_dict(job, stages_for_job(job["id"]))


def stages_for_job(job_id: int) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM analysis_stages WHERE job_id = ? ORDER BY seq", (job_id,))


def _stage(job_id: int, key: str, seq: int, status: str, *, outcome: Any = None,
           explanation: str | None = None, error: str | None = None,
           started: str | None = None, finished: str | None = None) -> None:
    db.execute(
        """UPDATE analysis_stages SET status = ?, outcome = ?, explanation = ?, error = ?,
               started_at = COALESCE(started_at, ?), finished_at = ?
           WHERE job_id = ? AND stage = ?""",
        (status, json.dumps(outcome, default=str) if outcome is not None else None,
         explanation, error, started, finished, job_id, key))


def _finish_stage(job_id: int, key: str, seq: int, status: str, outcome: Any,
                  explanation: str | None) -> None:
    now = db.utcnow()
    _stage(job_id, key, seq, status, outcome=outcome, explanation=explanation,
           started=now, finished=now)


def _fail_stage(job_id: int, key: str, seq: int, exc: Exception) -> None:
    now = db.utcnow()
    _stage(job_id, key, seq, "failed", error=str(exc), explanation="stage raised an exception",
           started=now, finished=now)


# --------------------------------------------------------------------- stage bodies

def _stage_new_report(report: dict) -> dict:
    return {"report_code": report["report_code"], "category": report["category"],
            "status": report["status"], "created_at": report["created_at"],
            "duplicate": bool(report["is_duplicate"])}, "report persisted in SQLite"


def _media_paths(report: dict) -> tuple[list[str], list[dict]]:
    rows = db.query("SELECT * FROM report_media WHERE report_id = ? ORDER BY id", (report["id"],))
    paths, meta = [], []
    for r in rows:
        p = config.UPLOAD_DIR / r["path"]
        paths.append(str(p))
        meta.append({"kind": r["kind"], "path": r["path"], "bytes": r["bytes"], "mime": r["mime"]})
    if not rows and report.get("photo_path"):
        p = config.UPLOAD_DIR / report["photo_path"]
        paths.append(str(p))
        meta.append({"kind": "photo", "path": report["photo_path"], "bytes": None, "mime": None})
    return paths, meta


def _stage_media(report: dict) -> tuple[Any, str, str]:
    paths, meta = _media_paths(report)
    present = [m for m in meta if (config.UPLOAD_DIR / m["path"]).exists()]
    photos = [m for m in present if m["kind"] == "photo"]
    videos = [m for m in present if m["kind"] == "video"]
    if not present:
        return ({"files": 0}), "skipped", "report submitted without stored media"
    return ({"files": len(present), "photos": len(photos), "videos": len(videos),
             "items": present}), "ok", f"{len(present)} file(s) stored in the upload directory"


def _stage_gps(report: dict) -> tuple[Any, str, str]:
    lat, lng, src = report["lat"], report["lng"], report["gps_source"]
    bbox = config.REGION_BBOX
    in_bbox = (bbox["min_lat"] <= lat <= bbox["max_lat"] and bbox["min_lon"] <= lng <= bbox["max_lon"])
    outcome = {"lat": lat, "lng": lng, "gps_source": src, "in_northeast_bbox": in_bbox,
               "bbox": bbox}
    if src == "unavailable":
        return outcome, "failed", "no GPS fix recorded for this report"
    if not in_bbox:
        return outcome, "failed", "coordinates fall outside the Northeast India working bbox"
    label = {"device": "device GPS", "manual": "manual pin placed by the reporter",
             "fallback_configured": "configured fallback location (labelled, not a device fix)"}
    return outcome, "ok", f"coordinates validated ({label.get(src, src)})"


def _nearest(report: dict) -> tuple[dict | None, float]:
    from gis.geo import haversine_km

    slopes = db.query("SELECT id, code, name, lat, lng, district, state FROM slopes")
    if not slopes:
        return None, None
    best = min(slopes, key=lambda s: haversine_km(report["lat"], report["lng"], s["lat"], s["lng"]))
    return best, round(haversine_km(report["lat"], report["lng"], best["lat"], best["lng"]), 3)


def _stage_location(report: dict) -> tuple[Any, str, str]:
    slope, dist = _nearest(report)
    outcome = {"nearest_slope": slope["code"] if slope else None, "distance_km": dist,
               "radius_km": 3.0}
    if slope is None:
        return outcome, "skipped", "slope inventory is empty"
    if dist is None or dist > 3.0:
        return outcome, "skipped", f"nearest inventory slope is {dist} km away - beyond the 3.0 km matching radius"
    return outcome, "ok", f"report matched to {slope['code']} at {dist} km"


def _stage_slope_identified(report: dict) -> tuple[Any, str, str]:
    if not report["slope_id"]:
        return {"slope_id": None}, "skipped", "no slope within the matching radius - report kept for manual triage"
    slope = db.query_one("SELECT id, code, name, lithology, slope_angle_deg FROM slopes WHERE id = ?",
                         (report["slope_id"],))
    return {"slope_id": slope["id"], "code": slope["code"], "name": slope["name"]}, "ok", \
        f"slope {slope['code']} identified for the pipeline"


def _stage_visual(report: dict) -> tuple[Any, str, str]:
    paths, _ = _media_paths(report)
    photo_paths = [p for p in paths if not p.lower().endswith((".mp4", ".webm", ".ogg", ".mov"))]
    if not photo_paths:
        return {"available": False}, "skipped", "no photograph attached - visual heuristics not run"
    from services.visual_baseline import analyse

    result = analyse(photo_paths)
    if not result.get("available"):
        return result, "failed", "; ".join(e.get("error", "") for e in result.get("errors", []))
    return result, "ok", (f"{result['photo_count']} photograph(s) summarised by baseline image "
                          f"statistics - not a trained classifier")


def _stage_rainfall(report: dict, slope: dict | None) -> tuple[Any, str, str]:
    if not slope:
        return {"available": False}, "skipped", "no slope identified - rainfall not queried for this report"
    from services.evidence import RAINFALL_RADIUS_KM, _latest_rainfall, _parse

    stations = _latest_rainfall(datetime.now(timezone.utc))
    if not stations:
        return {"available": False}, "skipped", "rainfall fixture table is empty"
    for s in stations:
        from gis.geo import haversine_km
        s["distance_km"] = round(haversine_km(slope["lat"], slope["lng"], s["lat"], s["lng"]), 2)
    nearest = min(stations, key=lambda s: s["distance_km"])
    if nearest["distance_km"] > RAINFALL_RADIUS_KM:
        return ({"station": nearest["station_code"], "distance_km": nearest["distance_km"]}), "skipped", \
            f"nearest gauge is {nearest['distance_km']} km away - beyond {RAINFALL_RADIUS_KM:.0f} km"
    age_h = None
    try:
        obs = _parse(nearest["observed_at"])
        age_h = round((datetime.now(timezone.utc) - obs).total_seconds() / 3600, 2)
    except Exception:
        pass
    return ({"station": nearest["station_code"], "distance_km": nearest["distance_km"],
             "mm_24h": nearest["mm_24h"], "mm_72h": nearest["mm_72h"],
             "observed_at": nearest["observed_at"], "age_hours": age_h,
             "data_mode": nearest["data_mode"]}), "ok", \
        f"fixture gauge {nearest['station_code']} at {nearest['distance_km']} km ({nearest['data_mode']})"


def _stage_terrain(report: dict, slope: dict | None) -> tuple[Any, str, str]:
    if not slope:
        return {"available": False}, "skipped", "no slope identified - terrain attributes not read"
    from services.evidence import terrain_probability

    p, conf, detail = terrain_probability(slope)
    return ({"slope_id": slope["id"], "terrain_probability": p, "confidence": conf,
             "attributes": {k: slope.get(k) for k in
                            ("slope_angle_deg", "lithology", "land_use", "road_cut",
                             "vegetation", "drainage_density", "curvature", "elevation_m")},
             "contributions": detail.get("contributions")}), "ok", \
        "static terrain attributes read from the slope inventory"


def _stage_satellite(report: dict, slope: dict | None) -> tuple[Any, str, str]:
    if not slope:
        return {"available": False}, "skipped", "no slope identified - satellite not queried"
    row = db.query_one(
        "SELECT * FROM satellite_observations WHERE slope_id = ? ORDER BY id DESC LIMIT 1",
        (slope["id"],))
    status = (row or {}).get("status") or "unavailable"
    if status != "available":
        return ({"available": False, "status": status,
                 "detail": (row or {}).get("detail") or "no satellite provider configured in this build",
                 "insar_note": "InSAR is an optional surface-deformation source with coverage and "
                               "measurement limitations; none is connected here."}), "skipped", \
            "satellite/InSAR provider unavailable - recorded as missing input, not processed"
    return ({"available": True, "status": status}), "ok", "satellite observation available"


def _stage_ml(report: dict, slope: dict | None) -> tuple[Any, str, str]:
    if not slope:
        return {"available": False}, "skipped", "no slope identified - model not invoked"
    try:
        from ml import predict as ml_predict

        out = ml_predict.predict_for_slope(slope, None, None, None)
        return ({"available": True, "probability": out["probability"],
                 "confidence": out["confidence"], "detail": out["detail"]}), "ok", \
            f"local RandomForest v{out['detail'].get('model_version')} invoked in-process"
    except Exception as exc:
        return ({"available": False, "error": str(exc)}), "skipped", \
            f"model unavailable: {exc}"


# --------------------------------------------------------------------- the runner

def run_report_analysis(report_id: int, actor_id: int | None = None,
                        force: bool = False) -> dict[str, Any]:
    report = db.query_one("SELECT * FROM citizen_reports WHERE id = ?", (report_id,))
    if not report:
        raise ValueError(f"report {report_id} not found")

    active = db.query_one(
        "SELECT * FROM analysis_jobs WHERE report_id = ? AND status IN ('pending','running') "
        "ORDER BY id DESC LIMIT 1", (report_id,))
    if active and not force:
        raise JobBusy(f"analysis job {active['id']} already {active['status']} for report {report_id}")

    attempt = (db.query_one("SELECT COUNT(*) AS n FROM analysis_jobs WHERE report_id = ?",
                            (report_id,)) or {"n": 0})["n"] + 1
    ts = db.utcnow()
    job_id = db.execute(
        """INSERT INTO analysis_jobs (report_id, slope_id, status, idempotency_key, attempt,
              created_by, created_at, started_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (report_id, report["slope_id"], "running", f"report:{report_id}:{attempt}",
         attempt, actor_id, ts, ts))
    for seq, (key, _label, _fatal) in enumerate(STAGES, start=1):
        db.execute(
            "INSERT INTO analysis_stages (job_id, stage, seq, status) VALUES (?,?,?, 'pending')",
            (job_id, key, seq))

    ctx: dict[str, Any] = {}
    job_error: str | None = None

    for seq, (key, label, fatal) in enumerate(STAGES, start=1):
        started = db.utcnow()
        _stage(job_id, key, seq, "running", started=started)
        try:
            if key == "new_report":
                outcome, expl = _stage_new_report(report)
                _finish_stage(job_id, key, seq, "ok", outcome, expl)
            elif key == "media_stored":
                outcome, status, expl = _stage_media(report)
                _finish_stage(job_id, key, seq, status, outcome, expl)
            elif key == "gps_validated":
                outcome, status, expl = _stage_gps(report)
                _finish_stage(job_id, key, seq, status, outcome, expl)
            elif key == "location_matched":
                outcome, status, expl = _stage_location(report)
                ctx["match"] = outcome
                _finish_stage(job_id, key, seq, status, outcome, expl)
            elif key == "slope_identified":
                outcome, status, expl = _stage_slope_identified(report)
                _finish_stage(job_id, key, seq, status, outcome, expl)
            elif key == "visual_evidence":
                outcome, status, expl = _stage_visual(report)
                ctx["visual"] = outcome
                _finish_stage(job_id, key, seq, status, outcome, expl)
            elif key == "rainfall_evidence":
                slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (report["slope_id"],)) \
                    if report["slope_id"] else None
                outcome, status, expl = _stage_rainfall(report, slope)
                _finish_stage(job_id, key, seq, status, outcome, expl)
            elif key == "terrain_evidence":
                slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (report["slope_id"],)) \
                    if report["slope_id"] else None
                outcome, status, expl = _stage_terrain(report, slope)
                _finish_stage(job_id, key, seq, status, outcome, expl)
            elif key == "satellite_evidence":
                slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (report["slope_id"],)) \
                    if report["slope_id"] else None
                outcome, status, expl = _stage_satellite(report, slope)
                _finish_stage(job_id, key, seq, status, outcome, expl)
            elif key == "ml_assessment":
                slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (report["slope_id"],)) \
                    if report["slope_id"] else None
                outcome, status, expl = _stage_ml(report, slope)
                _finish_stage(job_id, key, seq, status, outcome, expl)
            elif key == "belief_fusion":
                if not report["slope_id"]:
                    _finish_stage(job_id, key, seq, "skipped", {"ran": False},
                                  "no slope matched - fusion cannot run without a target slope")
                    job_error = "no slope matched; report retained for manual triage"
                    break
                from services.pipeline import run_assessment

                assessment = run_assessment(report["slope_id"], actor_id=actor_id)
                ctx["assessment"] = assessment
                db.execute("UPDATE analysis_jobs SET assessment_id = ? WHERE id = ?",
                           (assessment["assessment_id"], job_id))
                _finish_stage(job_id, key, seq, "ok", {
                    "assessment_id": assessment["assessment_id"],
                    "accepted": assessment["accepted"], "rejected": assessment["rejected"],
                    "missing_inputs": assessment["missing_inputs"],
                    "method": assessment["trace"]["method"],
                }, "evidence normalised, dependencies discounted, log-odds pool fused")
            elif key == "connected_slope":
                a = ctx.get("assessment")
                if not a:
                    _finish_stage(job_id, key, seq, "skipped", {"ran": False},
                                  "fusion did not run - no inherited influence to evaluate")
                else:
                    conn = a["trace"]["connected_slope"]
                    _finish_stage(job_id, key, seq, "ok", {
                        "direct_score": a["direct_score"],
                        "inherited_influence": a["inherited_influence"],
                        "uplift": a["uplift"], "neighbors_used": conn.get("neighbors_used"),
                        "mode": conn.get("mode"), "contributions": conn.get("contributions", [])[:6],
                    }, "one-hop bounded influence from documented neighbour edges (cap 0.15)")
            elif key == "risk_decision":
                a = ctx.get("assessment")
                if not a:
                    _finish_stage(job_id, key, seq, "skipped", {"ran": False},
                                  "fusion did not run - no risk decision recorded")
                else:
                    _finish_stage(job_id, key, seq, "ok", {
                        "band": a["band"], "previous_band": a["previous_band"],
                        "final_score": a["final_score"], "confidence": a["confidence"],
                        "transition": a["transition"], "recommendation": a["recommendation"],
                        "flags": a["flags"],
                    }, f"band {a['band']} (transition {a['transition']['reason']})")
            elif key == "alert_decision":
                a = ctx.get("assessment")
                if not a:
                    _finish_stage(job_id, key, seq, "skipped", {"ran": False},
                                  "fusion did not run - no alert decision recorded")
                else:
                    if a.get("alert"):
                        db.execute("UPDATE analysis_jobs SET alert_id = ? WHERE id = ?",
                                   (a["alert"]["id"], job_id))
                    _finish_stage(job_id, key, seq, "ok", {
                        "alert_created": a["alert_created"], "alert_id": (a.get("alert") or {}).get("id"),
                        "alert_code": (a.get("alert") or {}).get("alert_code"),
                        "note": a["alert_note"],
                    }, a["alert_note"])
        except Exception as exc:  # record, keep the report, stop only if fatal
            _fail_stage(job_id, key, seq, exc)
            if fatal:
                job_error = f"{label} failed: {exc}"
                now = db.utcnow()
                for offset, (k2, _l2, _f2) in enumerate(STAGES[seq:], start=seq + 1):
                    _stage(job_id, k2, offset, "skipped",
                           explanation="earlier fatal stage failed", started=now, finished=now)
                break

    status = "failed" if job_error else "succeeded"
    db.execute("UPDATE analysis_jobs SET status = ?, finished_at = ?, error = ?, slope_id = ? "
               "WHERE id = ?",
               (status, db.utcnow(), job_error, report["slope_id"], job_id))
    return latest_job_for_report(report_id)
