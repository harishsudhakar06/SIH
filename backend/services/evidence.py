"""Evidence collection for the Belief Fusion Engine.

Each collector turns raw database rows into an `Evidence` item with a
probability estimate, a confidence, provenance and (where relevant) a
dependency group so correlated inputs are discounted during fusion.

Everything here reads only the local database. Rainfall, sensor and terrain
values in this build are PROTOTYPE FIXTURES (data_mode = 'prototype_fixture');
satellite observations are recorded as unavailable. Nothing pretends to be a
live external feed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from gis.geo import geo_relevance, haversine_km
from services.belief_fusion import DEFAULT_SOURCES, Evidence, SourceSpec, load_source_specs

RAINFALL_RADIUS_KM = 60.0
REPORT_WINDOW_H = 24.0
REPORT_CLUSTER_M = 750.0


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + pow(2.718281828459045, -x))


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _spec(specs: dict[str, SourceSpec], code: str) -> SourceSpec:
    return specs.get(code, DEFAULT_SOURCES[code])


# ---------------------------------------------------------------- probability models

def rainfall_probability(mm_24h: float | None, mm_72h: float | None) -> tuple[float, float, dict]:
    """Documented prototype rainfall->instability curve.

    r = max(mm24, 0.6 * mm72)  (antecedent proxy)
    p = 0.10 + 0.75 * sigmoid((r - 85) / 30)
    """
    if mm_24h is None and mm_72h is None:
        return 0.0, 0.0, {}
    m24 = float(mm_24h or 0.0)
    m72 = float(mm_72h or 0.0)
    r = max(m24, 0.6 * m72)
    p = 0.10 + 0.75 * _sigmoid((r - 85.0) / 30.0)
    conf = 0.70 + (0.10 if mm_24h is not None and mm_72h is not None else 0.0)
    return round(p, 6), round(conf, 4), {"antecedent_r_mm": round(r, 2), "mm_24h": m24, "mm_72h": m72}


def terrain_probability(slope: dict[str, Any]) -> tuple[float, float, dict]:
    """Documented static-terrain heuristic (also used as explanatory context).

    base 0.12 + contributions per attribute, clamped to [0.05, 0.90].
    """
    litho_susc = {"Shale": 0.16, "Phyllite": 0.15, "Alluvium": 0.10, "Sandstone": 0.08,
                  "Limestone": 0.06, "Gneiss": 0.05, "Granite": 0.04, "Mafic": 0.04}
    landuse_susc = {"Quarry": 0.12, "Urban fringe": 0.06, "Agriculture": 0.04,
                    "Scrub": 0.02, "Tea plantation": 0.02, "Forest": 0.0}

    parts: dict[str, float] = {"base": 0.12}
    angle = slope.get("slope_angle_deg")
    if angle is not None:
        parts["slope_angle"] = max(0.0, min(0.20, 0.008 * (float(angle) - 28.0)))
    litho = slope.get("lithology")
    if litho:
        parts["lithology"] = litho_susc.get(litho, 0.05)
    landuse = slope.get("land_use")
    if landuse:
        parts["land_use"] = landuse_susc.get(landuse, 0.02)
    if slope.get("road_cut"):
        parts["road_cut"] = 0.08
    veg = slope.get("vegetation")
    if veg is not None:
        parts["vegetation"] = max(0.0, (0.55 - float(veg)) * 0.12)
    parts["historical_incidents"] = min(0.15, 0.05 * int(slope.get("historical_incidents") or 0))
    dd = slope.get("drainage_density")
    if dd is not None and float(dd) > 0.9:
        parts["drainage_density"] = 0.05
    curv = slope.get("curvature")
    if curv is not None and float(curv) < -0.4:
        parts["concave_curvature"] = 0.04

    p = min(0.90, max(0.05, sum(parts.values())))
    fields = [slope.get(k) for k in ("slope_angle_deg", "lithology", "land_use", "vegetation",
                                     "drainage_density", "curvature", "elevation_m")]
    completeness = sum(1 for f in fields if f is not None) / len(fields)
    conf = 0.55 + 0.30 * completeness
    return round(p, 6), round(conf, 4), {"contributions": {k: round(v, 4) for k, v in parts.items()},
                                         "completeness": round(completeness, 3)}


SENSOR_MODELS = {
    "inclinometer": ("displacement_mm", lambda v: _sigmoid((v - 12.0) / 6.0), 15.0),
    "crack_gauge": ("crack_opening_mm", lambda v: _sigmoid((v - 15.0) / 8.0), 18.0),
    "piezometer": ("pore_pressure_head_m", lambda v: _sigmoid((v - 10.0) / 4.0), 12.0),
    "soil_moisture": ("volumetric_percent", lambda v: _sigmoid((v - 35.0) / 8.0), 40.0),
}


def sensor_probability(readings: list[dict[str, Any]]) -> tuple[float, float, dict]:
    """Most-alarming sensor channel drives the estimate; quality scales confidence."""
    best_p, best = 0.0, None
    detail = []
    for r in readings:
        unit, fn, _ = SENSOR_MODELS[r["sensor_type"]]
        p = float(fn(float(r["value"])))
        detail.append({"sensor": r["sensor_uid"], "type": r["sensor_type"],
                       "value": r["value"], "unit": unit, "p": round(p, 4),
                       "observed_at": r["observed_at"], "quality": r["quality"]})
        if p > best_p:
            best_p, best = p, r
    if best is None:
        return 0.0, 0.0, {"readings": []}
    conf = 0.55 + 0.30 * float(best.get("quality") or 0.5)
    return round(best_p, 6), round(min(0.9, conf), 4), {"readings": detail, "driver": best["sensor_uid"]}


def report_probability(n_reporters: int, n_reports: int, top_category: str) -> tuple[float, float, dict]:
    severity_boost = 0.10 if top_category in ("rockfall", "debris_flow", "subsidence") else 0.0
    p = min(0.75, 0.32 + 0.12 * n_reporters + severity_boost)
    conf = min(0.80, 0.42 + 0.10 * n_reporters)
    return round(p, 6), round(conf, 4), {"reports": n_reports, "reporters": n_reporters,
                                         "category": top_category}


FIELD_MAP = {"ground_truth": (0.95, 0.95), "inconclusive": (0.55, 0.35), "not_verified": (0.25, 0.50)}


# ---------------------------------------------------------------- collectors

def collect_evidence(slope: dict[str, Any], now: datetime | None = None) -> tuple[list[Evidence], dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    specs = load_source_specs()
    items: list[Evidence] = []
    ctx: dict[str, Any] = {"slope_id": slope["id"], "collected_at": now.isoformat()}

    # 1) rainfall -----------------------------------------------------------------
    stations = _latest_rainfall(now)
    nearest = None
    if stations:
        for s in stations:
            s["distance_km"] = round(haversine_km(slope["lat"], slope["lng"], s["lat"], s["lng"]), 3)
        nearest = min(stations, key=lambda s: s["distance_km"])
    if nearest and nearest["distance_km"] <= RAINFALL_RADIUS_KM:
        p, conf, detail = rainfall_probability(nearest["mm_24h"], nearest["mm_72h"])
        obs_day = (nearest["observed_at"] or "")[:10]
        items.append(Evidence(
            evidence_type="rainfall",
            source_code="rainfall_gauge",
            label=f"rainfall {nearest['station_code']} ({nearest['distance_km']:.1f} km)",
            probability=p, confidence=conf, observed_at=nearest["observed_at"],
            provenance=f"gauge {nearest['station_name']} ({nearest['station_code']}), {nearest['data_mode']}",
            source_weight=_spec(specs, "rainfall_gauge").weight,
            freshness_half_life_h=_spec(specs, "rainfall_gauge").half_life_h,
            max_age_h=_spec(specs, "rainfall_gauge").max_age_h,
            geo_relevance=geo_relevance(nearest["distance_km"], 1.0, RAINFALL_RADIUS_KM),
            data_quality=float(nearest.get("quality") or 1.0),
            dependency_group=f"antecedent_rainfall:{obs_day}",
            detail={**detail, "station": nearest["station_code"], "distance_km": nearest["distance_km"],
                    "data_mode": nearest["data_mode"]},
        ))
        ctx["rainfall_station"] = nearest["station_code"]
        ctx["rainfall_24h"] = nearest["mm_24h"]
        ctx["rainfall_72h"] = nearest["mm_72h"]
    else:
        items.append(Evidence(
            evidence_type="rainfall", source_code="rainfall_gauge", label="rainfall (no station in range)",
            probability=None, confidence=0.0, observed_at=None,
            provenance="nearest gauge search", source_weight=_spec(specs, "rainfall_gauge").weight,
            freshness_half_life_h=_spec(specs, "rainfall_gauge").half_life_h,
            max_age_h=_spec(specs, "rainfall_gauge").max_age_h,
            status="missing", reason=f"no gauge within {RAINFALL_RADIUS_KM:.0f} km",
        ))

    # 2) ML prediction ------------------------------------------------------------
    sensor_max = ctx.get("sensor_max")
    try:
        from ml import predict as ml_predict

        out = ml_predict.predict_for_slope(slope, ctx.get("rainfall_24h"), ctx.get("rainfall_72h"), sensor_max)
        obs_day = now.isoformat()[:10]
        items.append(Evidence(
            evidence_type="ml_prediction", source_code="ml_local_model",
            label=f"local RF model {out['detail']['model_version']}",
            probability=out["probability"], confidence=out["confidence"], observed_at=now.isoformat(),
            provenance=f"joblib artifact, run at {now.isoformat()}",
            source_weight=_spec(specs, "ml_local_model").weight,
            freshness_half_life_h=_spec(specs, "ml_local_model").half_life_h,
            max_age_h=_spec(specs, "ml_local_model").max_age_h,
            geo_relevance=1.0, data_quality=0.9 if not out["detail"]["missing_inputs"] else 0.7,
            # The model ingests the same rainfall window -> correlated with rainfall evidence.
            dependency_group=f"antecedent_rainfall:{ctx.get('rainfall_station', 'na')}:{obs_day}",
            detail=out["detail"],
        ))
        ctx["ml"] = {"probability": out["probability"], "confidence": out["confidence"]}
    except Exception as exc:  # ModelUnavailable or artefact read failure
        items.append(Evidence(
            evidence_type="ml_prediction", source_code="ml_local_model", label="local ML model",
            probability=None, confidence=0.0, observed_at=None, provenance="ml/predict.py",
            source_weight=_spec(specs, "ml_local_model").weight,
            freshness_half_life_h=_spec(specs, "ml_local_model").half_life_h,
            max_age_h=_spec(specs, "ml_local_model").max_age_h,
            status="missing", reason=f"model unavailable: {exc}",
        ))

    # 3) terrain (static) ---------------------------------------------------------
    p, conf, detail = terrain_probability(slope)
    items.append(Evidence(
        evidence_type="terrain", source_code="terrain_static", label="static terrain attributes",
        probability=p, confidence=conf, observed_at=None,
        provenance="slopes table (project fixture attributes)",
        source_weight=_spec(specs, "terrain_static").weight,
        freshness_half_life_h=_spec(specs, "terrain_static").half_life_h,
        max_age_h=_spec(specs, "terrain_static").max_age_h,
        geo_relevance=1.0, data_quality=0.75, dependency_group=None, detail=detail,
    ))

    # 4) citizen reports ----------------------------------------------------------
    reports, dupes = _recent_reports(slope, now)
    if reports:
        reporters = {r["reporter_user"] for r in reports if r["reporter_user"]}
        top = max(reports, key=lambda r: r["created_at"])
        p, conf, detail = report_probability(max(1, len(reporters)), len(reports), top["category"])
        items.append(Evidence(
            evidence_type="citizen_report", source_code="citizen_report",
            label=f"citizen reports x{len(reports)}",
            probability=p, confidence=conf, observed_at=top["observed_at"],
            provenance=f"reports {', '.join(str(r['id']) for r in reports)}",
            source_weight=_spec(specs, "citizen_report").weight,
            freshness_half_life_h=_spec(specs, "citizen_report").half_life_h,
            max_age_h=_spec(specs, "citizen_report").max_age_h,
            geo_relevance=geo_relevance(_report_distance(slope, reports), 0.5, 5.0),
            data_quality=0.85,
            dependency_group=None,
            detail={**detail, "report_ids": [r["id"] for r in reports],
                    "duplicate_ids": [d["id"] for d in dupes]},
        ))
        ctx["reports"] = [r["id"] for r in reports]
        for d in dupes:
            items.append(Evidence(
                evidence_type="citizen_report", source_code="citizen_report",
                label=f"duplicate report #{d['id']}", probability=None, confidence=0.0,
                observed_at=d["observed_at"], provenance=f"report {d['id']}",
                source_weight=_spec(specs, "citizen_report").weight,
                freshness_half_life_h=_spec(specs, "citizen_report").half_life_h,
                max_age_h=_spec(specs, "citizen_report").max_age_h,
                status="duplicate",
                reason=f"clustered with report #{reports[0]['id']} "
                       f"(< {REPORT_CLUSTER_M:.0f} m, < {REPORT_WINDOW_H:.0f} h)",
                detail={"report_id": d["id"]},
            ))

    # 5) field verification --------------------------------------------------------
    obs = db.query_one(
        """SELECT * FROM field_observations WHERE slope_id = ?
           ORDER BY observed_at DESC LIMIT 1""",
        (slope["id"],),
    )
    if obs:
        p, conf = FIELD_MAP[obs["verification_status"]]
        items.append(Evidence(
            evidence_type="field_verification", source_code="field_survey",
            label=f"field verification: {obs['verification_status']}",
            probability=p, confidence=conf, observed_at=obs["observed_at"],
            provenance=f"observation #{obs['id']}, verifier user {obs['verifier_user']}",
            source_weight=_spec(specs, "field_survey").weight,
            freshness_half_life_h=_spec(specs, "field_survey").half_life_h,
            max_age_h=_spec(specs, "field_survey").max_age_h,
            geo_relevance=geo_relevance(_obs_distance(slope, obs), 0.2, 3.0),
            data_quality=float(0.95 if obs["verification_status"] == "ground_truth" else 0.7),
            detail={"status": obs["verification_status"], "crack_mm": obs["crack_mm"],
                    "seepage": obs["seepage"], "fresh_scarp": obs["fresh_scarp"],
                    "displacement_m": obs["displacement_m"], "notes": obs["notes"]},
        ))
        ctx["field_observation"] = obs["id"]

    # 6) ground sensors ------------------------------------------------------------
    readings = db.query(
        """SELECT * FROM sensor_readings WHERE slope_id = ?
           AND observed_at >= datetime(?, '-' || ? || ' hours')
           ORDER BY observed_at DESC""",
        (slope["id"], now.isoformat(), int(_spec(specs, "ground_sensor").max_age_h)),
    )
    if readings:
        p, conf, detail = sensor_probability(readings)
        if p > 0:
            items.append(Evidence(
                evidence_type="sensor", source_code="ground_sensor",
                label=f"ground sensors x{len(readings)}",
                probability=p, confidence=conf, observed_at=readings[0]["observed_at"],
                provenance=f"sensors on slope {slope['code']}",
                source_weight=_spec(specs, "ground_sensor").weight,
                freshness_half_life_h=_spec(specs, "ground_sensor").half_life_h,
                max_age_h=_spec(specs, "ground_sensor").max_age_h,
                geo_relevance=1.0,
                data_quality=round(sum(r["quality"] for r in readings) / len(readings), 4),
                detail=detail,
            ))
            ctx["sensor_max"] = max(float(r["value"]) for r in readings)
    else:
        items.append(Evidence(
            evidence_type="sensor", source_code="ground_sensor", label="ground sensors",
            probability=None, confidence=0.0, observed_at=None, provenance="sensor_readings",
            source_weight=_spec(specs, "ground_sensor").weight,
            freshness_half_life_h=_spec(specs, "ground_sensor").half_life_h,
            max_age_h=_spec(specs, "ground_sensor").max_age_h,
            status="missing", reason="no recent sensor readings for this slope",
        ))

    # 7) satellite (wired, unavailable) ---------------------------------------------
    sat = db.query_one(
        "SELECT * FROM satellite_observations WHERE slope_id = ? ORDER BY id DESC LIMIT 1",
        (slope["id"],),
    )
    sat_reason = (sat["detail"] if sat else None) or "satellite provider not connected in this build"
    items.append(Evidence(
        evidence_type="satellite", source_code="satellite_feed", label="satellite observation",
        probability=None, confidence=0.0,
        observed_at=(sat or {}).get("observed_at"), provenance="satellite_observations table",
        source_weight=_spec(specs, "satellite_feed").weight,
        freshness_half_life_h=_spec(specs, "satellite_feed").half_life_h,
        max_age_h=_spec(specs, "satellite_feed").max_age_h,
        status="missing", reason=sat_reason,
    ))

    return items, ctx


def _latest_rainfall(now: datetime) -> list[dict[str, Any]]:
    """Most recent reading per station (all stations, distance filtered by caller)."""
    return db.query(
        """SELECT r.* FROM rainfall_observations r
           JOIN (SELECT station_code, MAX(observed_at) AS mx
                 FROM rainfall_observations GROUP BY station_code) m
             ON m.station_code = r.station_code AND m.mx = r.observed_at"""
    )


def _recent_reports(slope: dict[str, Any], now: datetime) -> tuple[list[dict], list[dict]]:
    """Cluster fresh reports near the slope; representative kept, duplicates listed."""
    since = (now.timestamp() - REPORT_WINDOW_H * 3600)
    rows = db.query(
        """SELECT * FROM citizen_reports
           WHERE (slope_id = ? OR slope_id IS NULL)
             AND is_duplicate = 0
             AND strftime('%s', created_at) >= ?
           ORDER BY created_at ASC""",
        (slope["id"], int(since)),
    )
    keep: list[dict[str, Any]] = []
    dupes: list[dict[str, Any]] = []
    for r in rows:
        d = haversine_km(slope["lat"], slope["lng"], r["lat"], r["lng"]) * 1000.0
        if d > REPORT_CLUSTER_M and r["slope_id"] != slope["id"]:
            continue
        if not keep:
            keep.append(r)
        else:
            gap_h = abs((_parse(r["observed_at"]) - _parse(keep[0]["observed_at"])).total_seconds()) / 3600
            same_spot = haversine_km(keep[0]["lat"], keep[0]["lng"], r["lat"], r["lng"]) * 1000 <= REPORT_CLUSTER_M
            if same_spot and gap_h <= REPORT_WINDOW_H:
                dupes.append(r)
            else:
                keep.append(r)
    for r in keep:
        r["distance_km"] = round(haversine_km(slope["lat"], slope["lng"], r["lat"], r["lng"]), 3)
    return keep, dupes


def _report_distance(slope: dict, reports: list[dict]) -> float:
    return min(
        haversine_km(slope["lat"], slope["lng"], r["lat"], r["lng"]) for r in reports
    )


def _obs_distance(slope: dict, obs: dict) -> float:
    if obs.get("lat") is None or obs.get("lng") is None:
        return 0.0
    return haversine_km(slope["lat"], slope["lng"], obs["lat"], obs["lng"])
