"""Unit tests for the Belief Fusion Engine (innovation 1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.belief_fusion import (
    PRIOR_PROBABILITY,
    RHO_DEPENDENCY,
    Evidence,
    band_for,
    freshness_factor,
    fuse,
    normalise,
    overall_confidence,
    recommendation_for,
)

NOW = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)


def make_ev(**kw) -> Evidence:
    base = dict(
        evidence_type="rainfall",
        source_code="rainfall_gauge",
        label="gauge A",
        probability=0.6,
        confidence=0.8,
        observed_at=(NOW - timedelta(hours=2)).isoformat(),
        provenance="test",
        source_weight=0.75,
        freshness_half_life_h=12.0,
        max_age_h=72.0,
    )
    base.update(kw)
    return Evidence(**base)


def test_freshness_halves_each_half_life():
    assert freshness_factor(0, 12) == 1.0
    assert abs(freshness_factor(12, 12) - 0.5) < 1e-9
    assert abs(freshness_factor(24, 12) - 0.25) < 1e-9
    assert freshness_factor(-3, 12) == 1.0


def test_effective_weight_formula():
    ev = make_ev(confidence=0.8, geo_relevance=0.5, data_quality=0.5)
    accepted, rejected, missing = normalise([ev], NOW)
    assert not rejected and not missing
    got = accepted[0].effective_weight
    adjusted = 0.8 * 0.5 * 0.5
    fresh = freshness_factor(2, 12)
    assert abs(got - 0.75 * adjusted * fresh) < 1e-6
    assert accepted[0].adjusted_confidence is not None


def test_stale_evidence_rejected():
    ev = make_ev(observed_at=(NOW - timedelta(hours=100)).isoformat())
    accepted, rejected, _ = normalise([ev], NOW)
    assert not accepted
    assert rejected[0].status == "rejected"
    assert "stale" in rejected[0].reason


def test_zero_total_weight_falls_back_to_prior():
    ev = make_ev(confidence=0.0)
    accepted, _, _ = normalise([ev], NOW)
    # zero confidence -> zero weight, but still 'accepted'; fusion must not divide by zero
    result = fuse(accepted)
    if result["total_weight"] == 0:
        assert result["flags"] == ["zero_total_weight"]
        assert result["probability"] == PRIOR_PROBABILITY
    empty = fuse([])
    assert empty["flags"] == ["zero_total_weight"]
    assert empty["probability"] == PRIOR_PROBABILITY


def test_insufficient_evidence_flag():
    ev = make_ev(confidence=0.1)
    accepted, _, _ = normalise([ev], NOW)
    result = fuse(accepted)
    if result["total_weight"] > 0:
        assert "insufficient_evidence" in result["flags"]
        assert overall_confidence(result, accepted) <= 0.40


def test_correlated_sources_discounted():
    rain = make_ev(label="rain", dependency_group="g1", probability=0.8, source_weight=0.9)
    ml = make_ev(evidence_type="ml_prediction", source_code="ml_local_model", label="ml",
                 dependency_group="g1", probability=0.8, source_weight=0.8)
    accepted, _, _ = normalise([rain, ml], NOW)
    strong, weak = sorted(accepted, key=lambda e: e.effective_weight, reverse=True)
    assert weak.dependency_adjustment is not None
    assert "rho" in weak.dependency_adjustment
    # weak member scaled by (1 - rho) relative to what it would have been
    assert abs(weak.effective_weight - (strong.effective_weight / strong.source_weight
                                        * strong.adjusted_confidence / weak.adjusted_confidence
                                        * weak.source_weight * (1 - RHO_DEPENDENCY))) < 1e-3


def test_independent_sources_not_discounted():
    a = make_ev(label="a", probability=0.7)
    b = make_ev(evidence_type="sensor", source_code="ground_sensor", label="b",
                probability=0.7, dependency_group=None)
    accepted, _, _ = normalise([a, b], NOW)
    assert all(e.dependency_adjustment is None for e in accepted)


def test_missing_evidence_reported():
    sat = make_ev(evidence_type="satellite", source_code="satellite_feed",
                  probability=None, status="missing", reason="no provider")
    accepted, rejected, missing = normalise([sat], NOW)
    assert not accepted
    assert missing and "satellite" in missing[0]


def test_fusion_is_bounded_and_moves_with_evidence():
    low = fuse(normalise([make_ev(probability=0.1)], NOW)[0])
    # three independent strong sources are needed to overcome the documented prior weight
    high = fuse(normalise(
        [make_ev(probability=0.9, confidence=0.9, label=f"h{i}",
                 evidence_type=t, source_code="x")
         for i, t in enumerate(("rainfall", "sensor", "citizen_report"))], NOW)[0])
    assert 0.0 < low["probability"] < 0.5 < high["probability"] < 1.0


def test_more_weight_moves_further():
    single = fuse(normalise([make_ev(probability=0.85, confidence=0.6)], NOW)[0])
    triple_items = [make_ev(probability=0.85, confidence=0.9, label=f"l{i}",
                            evidence_type=t, source_code="x")
                    for i, t in enumerate(("rainfall", "sensor", "citizen_report"))]
    triple = fuse(normalise(triple_items, NOW)[0])
    assert triple["probability"] > single["probability"]


def test_band_mapping_and_ground_truth():
    assert band_for(0.10) == "LOW"
    assert band_for(0.30) == "MODERATE"
    assert band_for(0.60) == "HIGH"
    assert band_for(0.85) == "VERY_HIGH"
    assert band_for(0.95, ground_truth=True) == "CONFIRMED"
    assert band_for(0.10, ground_truth=True) == "CONFIRMED"


def test_confidence_penalises_missing_coverage():
    items = [make_ev(probability=0.5, evidence_type=t, source_code="x", label=t)
             for t in ("rainfall", "ml_prediction", "terrain", "citizen_report",
                       "field_verification", "sensor")]
    accepted, _, _ = normalise(items, NOW)
    full = fuse(accepted)
    conf = overall_confidence(full, accepted)
    assert 0 < conf <= 0.95
    assert full["coverage"] > 0.7


def test_recommendations_escalate_with_band():
    rec_low, _ = recommendation_for("LOW", 0.7)
    rec_high, action_high = recommendation_for("HIGH", 0.7)
    rec_weak, action_weak = recommendation_for("HIGH", 0.3)
    assert "routine" in rec_low.lower()
    assert "verify" not in rec_high.lower()
    assert "verify" in rec_weak.lower()
    assert "do not issue public warning" in action_weak.lower()
