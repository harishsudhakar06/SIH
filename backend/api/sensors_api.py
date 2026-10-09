"""Sensor device registry, observations, health and maintenance (Sensor Operations)."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, request

import auth as auth_mod
import db
from api.common import err, int_arg, ok

bp = Blueprint("sensors_api", __name__, url_prefix="/api")

SENSOR_TYPES = {"inclinometer", "piezometer", "crack_gauge", "soil_moisture", "rain_gauge", "gateway"}
STALE_HOURS = 6.0


def _age_hours(ts: str | None) -> float | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 2)


def _health(device: dict) -> dict:
    """Health from actual stored fields: staleness, battery, device status."""
    reasons = []
    obs_age = _age_hours(device.get("last_obs_at"))
    comm_age = _age_hours(device.get("last_comm_at"))
    if device.get("status") == "fault":
        health = "fault"
        reasons.append("device status is fault")
    elif obs_age is None:
        health = "unknown"
        reasons.append("no observation ever recorded")
    elif obs_age > STALE_HOURS:
        health = "stale"
        reasons.append(f"last observation {obs_age} h ago (>{STALE_HOURS:.0f} h)")
    elif device.get("battery_pct") is not None and float(device["battery_pct"]) < 20:
        health = "warning"
        reasons.append(f"battery at {device['battery_pct']}%")
    elif comm_age is not None and comm_age > STALE_HOURS * 2:
        health = "warning"
        reasons.append(f"last communication {comm_age} h ago")
    else:
        health = "ok"
        reasons.append("observations within the freshness window")
    return {"health": health, "reasons": reasons,
            "observation_age_hours": obs_age, "communication_age_hours": comm_age}


@bp.get("/sensors/devices")
def list_devices():
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    slope_id = request.args.get("slope_id")
    sql = """SELECT d.*, s.code AS slope_code, s.name AS slope_name, s.district
             FROM sensors d LEFT JOIN slopes s ON s.id = d.slope_id WHERE 1=1"""
    params: list = []
    if slope_id:
        sql += " AND d.slope_id = ?"
        params.append(int(slope_id))
    sql += " ORDER BY d.device_uid"
    devices = []
    for row in db.query(sql, tuple(params)):
        h = _health(row)
        devices.append({**row, **h})
    modes = {}
    for d in devices:
        modes[d["data_mode"]] = modes.get(d["data_mode"], 0) + 1
    return ok({"devices": devices, "count": len(devices), "data_modes": modes,
               "disclaimer": "Devices with data_mode='prototype_fixture' report simulated values - "
                             "no hardware is deployed in this build."})


@bp.get("/sensors/<int:sensor_id>")
def device_detail(sensor_id: int):
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    device = db.query_one(
        """SELECT d.*, s.code AS slope_code, s.name AS slope_name FROM sensors d
           LEFT JOIN slopes s ON s.id = d.slope_id WHERE d.id = ?""", (sensor_id,))
    if not device:
        return err("not_found", 404, "device not found")
    maintenance = db.query(
        """SELECT m.*, u.username AS performed_by_name FROM sensor_maintenance m
           LEFT JOIN users u ON u.id = m.performed_by
           WHERE m.sensor_id = ? ORDER BY m.id DESC LIMIT 20""", (sensor_id,))
    readings = db.query(
        "SELECT * FROM sensor_readings WHERE sensor_uid = ? ORDER BY observed_at DESC LIMIT 50",
        (device["device_uid"],))
    return ok({"device": {**device, **_health(device)},
               "maintenance": maintenance, "recent_readings": readings})


@bp.get("/sensors/<int:sensor_id>/observations")
def device_observations(sensor_id: int):
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "login required")
    device = db.query_one("SELECT * FROM sensors WHERE id = ?", (sensor_id,))
    if not device:
        return err("not_found", 404, "device not found")
    limit = int_arg("limit", 200)
    rows = db.query(
        "SELECT * FROM sensor_readings WHERE sensor_uid = ? ORDER BY observed_at DESC LIMIT ?",
        (device["device_uid"], limit))
    return ok({"device": device, "observations": rows, "count": len(rows),
               "data_mode": device["data_mode"]})


@bp.post("/sensors")
@auth_mod.require("sensors:manage")
def register_device():
    data = request.get_json(silent=True) or {}
    uid = (data.get("device_uid") or "").strip()
    if not uid:
        return err("validation", 400, "device_uid is required")
    stype = data.get("sensor_type")
    if stype not in SENSOR_TYPES:
        return err("validation", 400, f"sensor_type must be one of {sorted(SENSOR_TYPES)}")
    slope_id = data.get("slope_id")
    if slope_id is not None and not db.query_one("SELECT id FROM slopes WHERE id = ?", (slope_id,)):
        return err("validation", 400, "unknown slope_id")
    if db.query_one("SELECT id FROM sensors WHERE device_uid = ?", (uid,)):
        return err("conflict", 409, "device_uid already registered")
    data_mode = data.get("data_mode", "prototype_fixture")
    if data_mode not in {"prototype_fixture", "live", "manual"}:
        return err("validation", 400, "data_mode must be prototype_fixture|live|manual")
    ts = db.utcnow()
    device_id = db.execute(
        """INSERT INTO sensors (device_uid, slope_id, sensor_type, measured_vars, vendor,
              installed_at, battery_pct, power_source, health, last_comm_at, data_mode,
              status, note, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, slope_id, stype, data.get("measured_vars"), data.get("vendor"),
         data.get("installed_at") or ts, data.get("battery_pct"), data.get("power_source"),
         "unknown", ts, data_mode, "active", data.get("note"), ts))
    auth_mod.audit(auth_mod.current_user()["id"], "sensor.register", "sensors", device_id,
                   {"device_uid": uid, "slope_id": slope_id, "data_mode": data_mode})
    device = db.query_one("SELECT * FROM sensors WHERE id = ?", (device_id,))
    return ok({"device": {**device, **_health(device)}}, 201)


@bp.post("/sensors/<int:sensor_id>/readings")
@auth_mod.require("sensors:manage")
def ingest_reading(sensor_id: int):
    device = db.query_one("SELECT * FROM sensors WHERE id = ?", (sensor_id,))
    if not device:
        return err("not_found", 404, "device not found")
    data = request.get_json(silent=True) or {}
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return err("validation", 400, "numeric value is required")
    unit = data.get("unit") or "mm"
    ts = data.get("observed_at") or db.utcnow()
    if not device["slope_id"]:
        return err("conflict", 409, "device is not associated with a slope")
    db.execute(
        """INSERT INTO sensor_readings (slope_id, sensor_uid, sensor_type, observed_at, value,
              unit, quality, data_mode, ingested_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (device["slope_id"], device["device_uid"], device["sensor_type"], ts, value, unit,
         float(data.get("quality", 1.0)), device["data_mode"], db.utcnow()))
    db.execute("UPDATE sensors SET last_obs_at = ?, last_comm_at = ?, health = 'ok' WHERE id = ?",
               (ts, db.utcnow(), device["id"]))
    return ok({"status": "stored", "device_uid": device["device_uid"],
               "data_mode": device["data_mode"]}, 201)


@bp.post("/sensors/<int:sensor_id>/maintenance")
@auth_mod.require("sensors:manage")
def record_maintenance(sensor_id: int):
    device = db.query_one("SELECT * FROM sensors WHERE id = ?", (sensor_id,))
    if not device:
        return err("not_found", 404, "device not found")
    data = request.get_json(silent=True) or {}
    kind = data.get("kind", "maintenance")
    if kind not in {"calibration", "maintenance", "repair", "note"}:
        return err("validation", 400, "kind must be calibration|maintenance|repair|note")
    ts = db.utcnow()
    maint_id = db.execute(
        """INSERT INTO sensor_maintenance (sensor_id, kind, performed_at, performed_by, notes,
              result, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (sensor_id, kind, data.get("performed_at") or ts,
         auth_mod.current_user()["id"], data.get("notes"), data.get("result"), ts))
    if kind == "calibration" and data.get("battery_pct") is not None:
        db.execute("UPDATE sensors SET battery_pct = ? WHERE id = ?",
                   (data["battery_pct"], sensor_id))
    auth_mod.audit(auth_mod.current_user()["id"], f"sensor.{kind}", "sensors", sensor_id, data)
    return ok({"maintenance": db.query_one(
        "SELECT * FROM sensor_maintenance WHERE id = ?", (maint_id,))}, 201)


@bp.post("/sensors/<int:sensor_id>/readings/batch")
@auth_mod.require("sensors:manage")
def ingest_batch(sensor_id: int):
    """Bulk ingestion for gateways; reports per-row success instead of silent drops."""
    device = db.query_one("SELECT * FROM sensors WHERE id = ?", (sensor_id,))
    if not device:
        return err("not_found", 404, "device not found")
    data = request.get_json(silent=True) or {}
    rows = data.get("readings") or []
    if not isinstance(rows, list) or not rows:
        return err("validation", 400, "readings must be a non-empty list")
    stored, rejected = 0, []
    for i, row in enumerate(rows):
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError):
            rejected.append({"index": i, "error": "value must be numeric"})
            continue
        ts = row.get("observed_at") or db.utcnow()
        db.execute(
            """INSERT INTO sensor_readings (slope_id, sensor_uid, sensor_type, observed_at, value,
                  unit, quality, data_mode, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (device["slope_id"], device["device_uid"], device["sensor_type"], ts, value,
             row.get("unit") or "mm", float(row.get("quality", 1.0)), device["data_mode"],
             db.utcnow()))
        stored += 1
    if device["slope_id"] and stored:
        db.execute("UPDATE sensors SET last_obs_at = ?, last_comm_at = ? WHERE id = ?",
                   (db.utcnow(), db.utcnow(), device["id"]))
    db.execute("INSERT INTO ingestion_log (source_code, status, detail, created_at) VALUES (?,?,?,?)",
               (f"sensor:{device['device_uid']}", "ok" if not rejected else "degraded",
                f"stored {stored}, rejected {len(rejected)}", db.utcnow()))
    return ok({"stored": stored, "rejected": rejected}, 201 if stored else 400)
