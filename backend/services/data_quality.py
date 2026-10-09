"""Data-quality assessment: freshness, completeness, availability per source."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any

import db


def summary() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    sources = db.query("SELECT * FROM data_sources ORDER BY id")

    rainfall = db.query_one(
        "SELECT COUNT(*) AS n, MAX(observed_at) AS latest FROM rainfall_observations")
    sensors = db.query_one(
        "SELECT COUNT(*) AS n, MAX(observed_at) AS latest FROM sensor_readings")
    reports = db.query_one(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN photo_path IS NOT NULL THEN 1 ELSE 0 END) AS with_photo FROM citizen_reports")
    field = db.query_one("SELECT COUNT(*) AS n FROM field_observations")
    satellite = db.query_one(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN status='available' THEN 1 ELSE 0 END) AS avail FROM satellite_observations")
    assessments = db.query_one("SELECT COUNT(*) AS n, MAX(created_at) AS latest FROM assessments")
    missing_sat = satellite["n"] - (satellite["avail"] or 0)

    def age_h(ts: str | None) -> float | None:
        if not ts:
            return None
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((now - dt).total_seconds() / 3600, 2)

    return {
        "generated_at": now.replace(microsecond=0).isoformat(),
        "sources": [
            {
                "code": s["code"], "name": s["name"], "kind": s["kind"],
                "source_weight": s["source_weight"], "half_life_h": s["freshness_half_life_h"],
                "max_age_h": s["max_age_h"], "data_mode": s["data_mode"], "status": s["status"],
                "notes": s["notes"],
            }
            for s in sources
        ],
        "feeds": {
            "rainfall": {"rows": rainfall["n"], "latest": rainfall["latest"],
                         "age_h": age_h(rainfall["latest"]), "data_mode": "prototype_fixture"},
            "sensors": {"rows": sensors["n"], "latest": sensors["latest"],
                        "age_h": age_h(sensors["latest"]), "data_mode": "prototype_fixture"},
            "citizen_reports": {"rows": reports["n"], "with_photo": reports["with_photo"] or 0,
                                "data_mode": "live"},
            "field_observations": {"rows": field["n"]},
            "satellite": {"rows": satellite["n"], "available": satellite["avail"] or 0,
                          "unavailable": missing_sat, "data_mode": "unavailable"},
            "assessments": {"rows": assessments["n"], "latest": assessments["latest"],
                            "age_h": age_h(assessments["latest"])},
        },
        "flags": [
            "satellite feed unavailable in this build - always reported as a missing input"
            if missing_sat or (satellite["n"] or 0) == 0 else None,
            f"latest rainfall fixture is {age_h(rainfall['latest'])} h old"
            if rainfall["latest"] and (age_h(rainfall["latest"]) or 0) > 24 else None,
            "ML model not trained yet" if assessments["n"] in (0, None) else None,
        ],
        "honesty": (
            "Rainfall and sensor rows in this deployment are PROTOTYPE FIXTURES "
            "(data_mode='prototype_fixture'), not live measurements. "
            "Citizen reports and field observations written through the API are live user data."
        ),
    }
