"""Assessment pipeline: evidence -> Belief Fusion -> Connected Slope -> state -> alert.

Produces a persisted, fully auditable assessment per slope:
  * assessments row (scores, band, confidence, recommendation, full trace_json)
  * assessment_evidence rows (accepted / rejected / duplicate / missing + reasons)
  * slope_risk_history row (with transition allow/deny decision)
  * alerts row when warranted (duplicate-safe via unique dedupe_key)
  * notifications fan-out for the roles that must act
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import db
from services import belief_fusion as bf
from services import connected_slope as cs
from services import risk_state as rs
from services.evidence import collect_evidence

ALERT_MIN_BAND = "HIGH"
ALERT_MIN_CONFIDENCE = 0.35


def run_assessment(slope_id: int, now: datetime | None = None,
                   actor_id: int | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    with db.tx():
        return _run_assessment_inner(slope_id, now, actor_id)


def _run_assessment_inner(slope_id: int, now: datetime, actor_id: int | None) -> dict[str, Any]:
    slope = db.query_one("SELECT * FROM slopes WHERE id = ?", (slope_id,))
    if not slope:
        raise ValueError(f"slope {slope_id} not found")

    # 1. collect + normalise + fuse -------------------------------------------
    raw_items, ctx = collect_evidence(slope, now)
    accepted, rejected, missing = bf.normalise(raw_items, now)
    fusion = bf.fuse(accepted)
    confidence = bf.overall_confidence(fusion, accepted)

    ground_truth = any(
        e.evidence_type == "field_verification"
        and (e.detail or {}).get("status") == "ground_truth"
        for e in accepted
    )

    direct_score = fusion["probability"]
    direct_band = bf.band_for(direct_score, ground_truth=False)

    # 2. connected-slope one-hop context ---------------------------------------
    connected = cs.propagate(slope_id, direct_score, now)
    final_score = connected["final_score"]
    band = bf.band_for(final_score, ground_truth=ground_truth)

    # 3. controlled transition --------------------------------------------------
    previous = rs.current_band(slope_id)
    allowed, why = rs.transition_allowed(previous, band, confidence, fusion["flags"], ground_truth)
    if not allowed:
        band = previous or direct_band

    recommendation, next_action = bf.recommendation_for(band, confidence, ground_truth)

    # 4. persist assessment + evidence -----------------------------------------
    ts = db.utcnow()
    missing_json = json.dumps(missing)
    flags = list(fusion["flags"])
    if connected["neighbors_used"] == 0:
        flags.append("no_fresh_neighbors")
    if not allowed:
        flags.append("transition_blocked")

    trace = {
        "engine_version": bf.ENGINE_VERSION,
        "generated_at": ts,
        "slope": {"id": slope["id"], "code": slope["code"], "name": slope["name"],
                  "lat": slope["lat"], "lng": slope["lng"], "district": slope["district"],
                  "state": slope["state"]},
        "prior": {"probability": bf.PRIOR_PROBABILITY, "weight": bf.PRIOR_WEIGHT,
                  "note": "documented prototype base rate for a NE-India slope"},
        "method": fusion["method"],
        "context": ctx,
        "evidence_accepted": [e.to_dict() for e in accepted],
        "evidence_rejected": [e.to_dict() for e in rejected],
        "missing_inputs": missing,
        "dependency_groups": _dependency_summary(accepted),
        "fusion": {
            "total_weight": fusion["total_weight"],
            "terms": fusion["terms"],
            "flags": fusion["flags"],
            "coverage": fusion["coverage"],
            "formula": "logit(p*) = (w0*logit(p0) + Σ wi*logit(pi)) / (w0 + Σ wi)",
            "effective_weight_formula": "source_weight × (confidence × geo_relevance × data_quality) × freshness_factor",
        },
        "direct": {"score": direct_score, "band": direct_band},
        "connected_slope": connected,
        "transition": {"from": previous, "to": band, "allowed": allowed, "reason": why},
        "result": {
            "final_score": final_score, "band": band, "confidence": confidence,
            "ground_truth": ground_truth, "flags": flags,
            "recommendation": recommendation, "next_action": next_action,
        },
        "caveats": [
            "Rainfall, sensor and terrain inputs are prototype fixtures in this build.",
            "Satellite input is recorded as unavailable and listed under missing_inputs.",
            "Connected-slope influence is contextual and one-hop only; it does not confirm failure.",
        ],
    }

    assessment_id = db.execute(
        """INSERT INTO assessments (slope_id, method, engine_version, prior_probability,
              fused_probability, direct_score, inherited_influence, uplift, final_score,
              risk_band, confidence, ground_truth, accepted_count, rejected_count,
              missing_inputs, recommendation, next_action, flags, trace_json, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            slope_id, fusion["method"], bf.ENGINE_VERSION, bf.PRIOR_PROBABILITY,
            fusion["probability"], direct_score, connected["inherited_influence"],
            connected["uplift"], final_score, band, confidence, int(ground_truth),
            len(accepted), len([r for r in rejected if r.status == "rejected"]),
            missing_json, recommendation, next_action, json.dumps(flags),
            json.dumps(trace, default=str), ts,
        ),
    )
    _insert_evidence(assessment_id, accepted)
    _insert_evidence(assessment_id, rejected)

    rs.record_history(slope_id, band, final_score, assessment_id, previous, allowed)
    db.execute("UPDATE slopes SET condition_status = ?, updated_at = ? WHERE id = ?",
               ("confirmed_landslide" if ground_truth else "monitored", ts, slope_id))

    # 5. alerting (duplicate-safe) --------------------------------------------
    alert, alert_created, alert_note = _maybe_alert(
        slope, assessment_id, band, final_score, confidence, recommendation, previous, now
    )

    return {
        "assessment_id": assessment_id,
        "slope_id": slope_id,
        "band": band,
        "previous_band": previous,
        "direct_score": direct_score,
        "inherited_influence": connected["inherited_influence"],
        "uplift": connected["uplift"],
        "final_score": final_score,
        "confidence": confidence,
        "ground_truth": ground_truth,
        "accepted": len(accepted),
        "rejected": len(rejected),
        "missing_inputs": missing,
        "flags": flags,
        "recommendation": recommendation,
        "next_action": next_action,
        "transition": {"from": previous, "to": band, "allowed": allowed, "reason": why},
        "alert": alert,
        "alert_created": alert_created,
        "alert_note": alert_note,
        "trace": trace,
    }


def run_all(now: datetime | None = None) -> list[dict[str, Any]]:
    slopes = db.query("SELECT id FROM slopes ORDER BY id")
    return [run_assessment(s["id"], now) for s in slopes]


def _dependency_summary(accepted: list[bf.Evidence]) -> list[dict[str, Any]]:
    groups: dict[str, list[bf.Evidence]] = {}
    for e in accepted:
        if e.dependency_group:
            groups.setdefault(e.dependency_group, []).append(e)
    return [
        {"group": g, "members": [e.label for e in members],
         "rho": bf.RHO_DEPENDENCY,
         "adjustments": [e.dependency_adjustment for e in members if e.dependency_adjustment]}
        for g, members in groups.items()
    ]


def _insert_evidence(assessment_id: int, items: list[bf.Evidence]) -> None:
    for e in items:
        db.execute(
            """INSERT INTO assessment_evidence (assessment_id, evidence_type, source_code, label,
                 source_weight, confidence, adjusted_confidence, freshness_factor, effective_weight,
                 probability, observed_at, geo_relevance, data_quality, dependency_group,
                 dependency_adjustment, status, reason, provenance, detail_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                assessment_id, e.evidence_type, e.source_code, e.label, e.source_weight,
                e.confidence, e.adjusted_confidence, e.freshness, e.effective_weight,
                e.probability, e.observed_at, e.geo_relevance, e.data_quality,
                e.dependency_group, e.dependency_adjustment, e.status, e.reason,
                e.provenance, json.dumps(e.detail, default=str),
            ),
        )


def _maybe_alert(slope: dict[str, Any], assessment_id: int, band: str, score: float,
                 confidence: float, recommendation: str, previous: str | None,
                 now: datetime) -> tuple[dict | None, bool, str]:
    if band not in {"HIGH", "VERY_HIGH", "CONFIRMED"}:
        return None, False, f"band {band} below alert threshold {ALERT_MIN_BAND}"
    if confidence < ALERT_MIN_CONFIDENCE:
        return None, False, f"confidence {confidence:.2f} below {ALERT_MIN_CONFIDENCE}; verify first"

    existing = rs.open_alert_for(slope["id"])
    if existing and existing["band"] == band:
        return existing, False, "open alert for this slope+band already exists (duplicate prevented)"

    title = f"{band} landslide risk: {slope['name']}"
    message = (
        f"Belief Fusion scored {slope['code']} at {score:.2f} (confidence {confidence:.2f}). "
        f"{recommendation}"
    )
    alert, created = rs.create_alert(
        slope_id=slope["id"], assessment_id=assessment_id, band=band, title=title,
        message=message, district=slope["district"], state=slope["state"], now=now,
    )
    if not created:
        return alert, False, "duplicate suppressed by unique dedupe_key (slope|band|hour)"
    return alert, True, "alert raised"
