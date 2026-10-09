"""Connected-Slope Intelligence (PRIMARY INNOVATION 2).

Slopes are nodes; documented relationships are edges (see gis/graph_builder).
This module performs a bounded, explainable ONE-HOP propagation of contextual
risk from neighbouring slopes and keeps it strictly separate from direct
observations.

Design rules enforced here:
  * Only one hop is propagated - there is no recursive cascade.
  * Every contribution records source slope, relationship, the arithmetic that
    produced it, edge confidence, freshness and the resulting influence.
  * Inherited influence is contextual: it can nudge the final score by at most
    MAX_UPLIFT and can NEVER produce the CONFIRMED band (confirmation requires a
    ground-truth field observation ON THAT SLOPE).
  * Edges flagged `validated = 0` are documented hypotheses (approximate
    hydrology from elevation gradients, distance-based adjacency, etc.). No
    physical landslide-propagation claim is made.

    inherited = Σ(wᵢ·sᵢ) / Σ(wᵢ)
    wᵢ = strength × rel_weight × direction × distance_decay × freshness × confidence
    uplift = min(MAX_UPLIFT, TRANSFER × max(0, inherited − direct))
    final  = min(0.99, direct + uplift)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db

REL_TYPE_WEIGHTS = {
    "spatial_adjacency": 0.85,
    "rainfall_similarity": 0.65,
    "geology_similarity": 0.55,
    "hydrology_downslope": 0.75,
}
DISTANCE_DECAY_KM = 2.0        # decay = 1 / (1 + d/2km)
MAX_UPLIFT = 0.15              # hard cap on contextual influence
TRANSFER_COEFF = 0.35          # share of (inherited - direct) that may transfer
NEIGHBOR_FRESHNESS_HALF_LIFE_H = 24.0
MIN_EDGE_STRENGTH = 0.30
ENGINE_VERSION = "1.0.0"


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _direction_factor(edge: dict[str, Any], slope_id: int) -> tuple[float, str]:
    """How strongly the source slope influences `slope_id` along this edge."""
    if edge["rel_type"] != "hydrology_downslope":
        return 1.0, "symmetric relationship"
    if edge["dst_slope_id"] == slope_id:
        return 1.0, "downslope flow points INTO this slope"
    return 0.35, "downslope flow points AWAY from this slope (context only)"


def propagate(slope_id: int, direct_score: float, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    edges = db.query(
        """SELECT * FROM slope_edges
           WHERE src_slope_id = ? OR dst_slope_id = ?
             AND strength >= ?""",
        (slope_id, slope_id, MIN_EDGE_STRENGTH),
    )
    # SQLite precedence quirk safeguard: filter explicitly in Python too.
    edges = [e for e in edges if (e["src_slope_id"] == slope_id or e["dst_slope_id"] == slope_id)
             and e["strength"] >= MIN_EDGE_STRENGTH]

    contributions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    wsum = 0.0
    wvsum = 0.0

    for edge in edges:
        neighbor_id = edge["dst_slope_id"] if edge["src_slope_id"] == slope_id else edge["src_slope_id"]
        if neighbor_id == slope_id:
            continue

        latest = db.query_one(
            """SELECT id, direct_score, risk_band, confidence, created_at
               FROM assessments WHERE slope_id = ?
               ORDER BY id DESC LIMIT 1""",
            (neighbor_id,),
        )
        if not latest:
            skipped.append({"slope_id": neighbor_id, "reason": "no assessment available for neighbour"})
            continue

        observed = _parse(latest["created_at"])
        age_h = max(0.0, (now - observed).total_seconds() / 3600.0) if observed else 0.0
        freshness = 0.5 ** (age_h / NEIGHBOR_FRESHNESS_HALF_LIFE_H)

        direction, dir_note = _direction_factor(edge, slope_id)
        rel_weight = REL_TYPE_WEIGHTS.get(edge["rel_type"], 0.5)
        distance = edge["distance_km"] or 1.0
        decay = 1.0 / (1.0 + distance / DISTANCE_DECAY_KM)
        confidence = edge["confidence"] or 0.7

        weight = edge["strength"] * rel_weight * direction * decay * freshness * confidence
        neighbor_score = latest["direct_score"]
        wsum += weight
        wvsum += weight * neighbor_score

        neighbor = db.query_one("SELECT code, name, lat, lng FROM slopes WHERE id = ?", (neighbor_id,))
        contributions.append(
            {
                "source_slope_id": neighbor_id,
                "source_code": neighbor["code"] if neighbor else str(neighbor_id),
                "source_name": neighbor["name"] if neighbor else "",
                "source_band": latest["risk_band"],
                "source_score": round(neighbor_score, 4),
                "relationship": edge["rel_type"],
                "strength": edge["strength"],
                "distance_km": round(distance, 3),
                "direction_factor": direction,
                "direction_note": dir_note,
                "rel_weight": rel_weight,
                "distance_decay": round(decay, 4),
                "freshness": round(freshness, 4),
                "edge_confidence": confidence,
                "edge_validated": bool(edge["validated"]),
                "edge_basis": edge["basis"],
                "weight": round(weight, 6),
                "contribution": round(weight * neighbor_score, 6),
                "calc": (
                    f"w = {edge['strength']:.2f}(strength) x {rel_weight:.2f}(rel) x "
                    f"{direction:.2f}(direction) x {decay:.2f}(distance) x "
                    f"{freshness:.2f}(freshness) x {confidence:.2f}(confidence) = {weight:.4f}; "
                    f"contribution = {weight:.4f} x {neighbor_score:.3f} = {weight * neighbor_score:.4f}"
                ),
                "assessment_id": latest["id"],
                "assessment_age_h": round(age_h, 2),
                "unvalidated_edge": not bool(edge["validated"]),
            }
        )

    if wsum <= 0:
        inherited = 0.0
        uplift = 0.0
        final = direct_score
        neighbor_confidence = 0.0
    else:
        inherited = wvsum / wsum
        neighbor_confidence = sum(c["edge_confidence"] for c in contributions) / len(contributions)
        gap = max(0.0, inherited - direct_score)
        uplift = min(MAX_UPLIFT, TRANSFER_COEFF * gap)
        final = min(0.99, direct_score + uplift)

    return {
        "engine_version": ENGINE_VERSION,
        "mode": "bounded_one_hop",
        "slope_id": slope_id,
        "direct_score": round(direct_score, 6),
        "inherited_influence": round(inherited, 6),
        "uplift": round(uplift, 6),
        "final_score": round(final, 6),
        "max_uplift": MAX_UPLIFT,
        "neighbors_used": len(contributions),
        "neighbors_skipped": skipped,
        "confidence": round(neighbor_confidence, 4),
        "contributions": contributions,
        "caveat": (
            "Inherited influence is contextual only. It never confirms a landslide on the "
            "target slope; edge basis is documented/estimated and unvalidated unless flagged."
        ),
    }


def graph_for_map() -> dict[str, Any]:
    """All nodes + edges for the command-center graph view."""
    nodes = db.query(
        """SELECT s.id, s.code, s.name, s.lat, s.lng, s.district, s.state, s.lithology,
                  a.risk_band, a.final_score, a.confidence, a.created_at AS assessed_at
           FROM slopes s
           LEFT JOIN assessments a ON a.id = (
              SELECT id FROM assessments WHERE slope_id = s.id ORDER BY id DESC LIMIT 1)"""
    )
    edges = db.query(
        """SELECT e.*, s1.code AS src_code, s2.code AS dst_code
           FROM slope_edges e
           JOIN slopes s1 ON s1.id = e.src_slope_id
           JOIN slopes s2 ON s2.id = e.dst_slope_id"""
    )
    return {"nodes": nodes, "edges": edges, "rel_type_weights": REL_TYPE_WEIGHTS}
