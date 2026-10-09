"""Data + ML endpoints: rainfall, sensors, satellite, source registry, model info."""
from __future__ import annotations

import json

from flask import Blueprint, request

import auth as auth_mod
import db
from api.common import err, int_arg, ok, parse_since
from services import data_quality

bp = Blueprint("data_api", __name__, url_prefix="/api")


@bp.get("/rainfall")
def rainfall():
    station = request.args.get("station_code")
    since = parse_since(request.args.get("since"))
    limit = int_arg("limit", 200)
    sql = "SELECT * FROM rainfall_observations WHERE 1 = 1"
    params: list = []
    if station:
        sql += " AND station_code = ?"
        params.append(station)
    if since:
        sql += " AND observed_at >= ?"
        params.append(since)
    sql += " ORDER BY observed_at DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    return ok({"observations": rows, "count": len(rows),
               "data_mode": "prototype_fixture",
               "disclaimer": "Prototype fixture readings - no live rainfall provider is connected."})


@bp.get("/rainfall/stations")
def stations():
    rows = db.query(
        """SELECT r.* FROM rainfall_observations r
           JOIN (SELECT station_code, MAX(observed_at) mx FROM rainfall_observations
                 GROUP BY station_code) m
             ON m.station_code = r.station_code AND m.mx = r.observed_at
           ORDER BY r.station_name""")
    return ok({"stations": rows, "count": len(rows), "data_mode": "prototype_fixture"})


@bp.get("/sensors")
def sensors():
    slope_id = request.args.get("slope_id")
    limit = int_arg("limit", 300)
    sql = """SELECT sr.*, s.code AS slope_code, s.name AS slope_name
             FROM sensor_readings sr JOIN slopes s ON s.id = sr.slope_id WHERE 1 = 1"""
    params: list = []
    if slope_id:
        sql += " AND sr.slope_id = ?"
        params.append(int(slope_id))
    sql += " ORDER BY sr.observed_at DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    return ok({"readings": rows, "count": len(rows), "data_mode": "prototype_fixture",
               "disclaimer": "Prototype fixture readings - no hardware is deployed in this build."})


@bp.get("/satellite")
def satellite():
    slope_id = request.args.get("slope_id")
    sql = "SELECT * FROM satellite_observations"
    params: list = []
    if slope_id:
        sql += " WHERE slope_id = ?"
        params.append(int(slope_id))
    sql += " ORDER BY id DESC LIMIT 200"
    rows = db.query(sql, params)
    return ok({"observations": rows, "count": len(rows), "data_mode": "unavailable",
               "disclaimer": "No satellite provider is connected; every row is status=unavailable."})


@bp.get("/sources")
def sources():
    return ok({"sources": db.query("SELECT * FROM data_sources ORDER BY id"),
               "fusion_formula": {
                   "effective_weight": "source_weight × (confidence × geo_relevance × data_quality) × freshness_factor",
                   "fusion": "logit(p*) = (w0·logit(p0) + Σ wi·logit(pi)) / (w0 + Σ wi)",
                   "dependency_rho": 0.55,
               }})


@bp.get("/data-quality")
def data_quality_report():
    return ok(data_quality.summary())


# ---------------------------------------------------------------- ML endpoints

@bp.get("/ml/model-info")
def model_info():
    try:
        from ml import predict

        info = predict.model_info()
    except RuntimeError as exc:
        return err("unavailable", 503, str(exc),
                   hint="train it with: python backend/ml/train.py")
    return ok({"model": info})


@bp.get("/ml/runs")
def ml_runs():
    rows = db.query("SELECT * FROM ml_runs ORDER BY id DESC LIMIT 20")
    for r in rows:
        try:
            r["metrics"] = json.loads(r["metrics_json"])
        except (ValueError, TypeError):
            r["metrics"] = {}
        r.pop("metrics_json", None)
    return ok({"runs": rows, "count": len(rows)})


@bp.post("/ml/predict")
@auth_mod.require("assessments:run")
def ml_predict():
    data = request.get_json(silent=True) or {}
    slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (data.get("slope_id"),))
    if not slope:
        return err("validation", 400, "slope_id required")
    from services.evidence import collect_evidence
    from ml import predict as ml_predict_mod

    _, ctx = collect_evidence(slope)
    try:
        out = ml_predict_mod.predict_for_slope(
            slope, ctx.get("rainfall_24h"), ctx.get("rainfall_72h"), ctx.get("sensor_max"))
    except RuntimeError as exc:
        return err("unavailable", 503, str(exc))
    return ok({"slope_id": slope["id"], "prediction": out})


@bp.post("/ml/train")
@auth_mod.require("ml:train")
def ml_train():
    from ml.train import train

    meta = train()
    auth_mod.audit(auth_mod.current_user()["id"], "ml.train", "ml_runs", None,
                   {"version": meta["version"], "metrics": meta["metrics"]})
    return ok({"model": {"version": meta["version"], "metrics": meta["metrics"],
                         "data_mode": meta["data_mode"], "notes": meta["notes"]}}, 201)
