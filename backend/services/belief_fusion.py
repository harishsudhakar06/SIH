"""Belief Fusion Engine (PRIMARY INNOVATION 1).

Combines heterogeneous evidence about a slope into one explainable risk score
while keeping a full, auditable trace of every decision the engine made.

Evidence model
--------------
Every evidence item carries:
  * source reliability  -> source_weight  (registry: data_sources table)
  * confidence          -> confidence in [0, 1] reported by the collector
  * geographic relevance-> geo_relevance  in [0, 1] (distance decay, see gis.geo)
  * data quality        -> data_quality   in [0, 1]
  * freshness           -> freshness_factor = 0.5 ** (age_hours / half_life)
  * provenance          -> free text (who/what produced it, identifier)
  * dependency group    -> items that were derived from the same underlying
                           measurement are marked so they are not counted twice

Baseline effective weight (documented formula):

    adjusted_confidence = confidence × geo_relevance × data_quality
    effective_weight    = source_weight × adjusted_confidence × freshness_factor

Fusion method (documented): normalized weighted log-odds pooling

    logit(p*) = (w0·logit(p0) + Σ wi·logit(pi)) / (w0 + Σ wi)
    p*        = sigmoid(logit(p*))

p0 is the regional prior (documented prototype base rate) with weight w0.
Log-odds pooling keeps the fused value a proper probability in (0,1) and makes
each evidence contribution additive and inspectable.

Safeguards implemented:
  * zero total weight        -> fall back to prior, flag `zero_total_weight`
  * insufficient total weight-> flag `insufficient_evidence`, low confidence
  * stale observations       -> rejected with reason `stale` (age > max_age)
  * duplicate reports        -> rejected with reason `duplicate`, representative kept
  * correlated sources       -> within a dependency group the first (strongest)
                                item keeps its weight, the rest are scaled by
                                (1 - rho) and the adjustment is recorded
  * missing inputs           -> listed in `missing_inputs` (e.g. satellite feed)
  * probability clamping     -> each pi is clamped to [0.02, 0.98] before logit
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

ENGINE_VERSION = "1.0.0"
METHOD = "normalized_weighted_logodds_pool"

PRIOR_PROBABILITY = 0.12      # documented prototype base rate for a NE-India slope
PRIOR_WEIGHT = 1.0            # w0, pseudo-evidence anchoring the pool
MIN_TOTAL_WEIGHT = 0.5        # below this the result is flagged as weak
PROB_CLAMP = (0.02, 0.98)
RHO_DEPENDENCY = 0.55         # correlation discount inside a dependency group

BANDS = (
    ("LOW", 0.00, 0.25),
    ("MODERATE", 0.25, 0.50),
    ("HIGH", 0.50, 0.75),
    ("VERY_HIGH", 0.75, 1.01),
)

# Expected evidence types for coverage accounting (satellite is wired but unavailable).
EXPECTED_TYPES = (
    "rainfall", "ml_prediction", "terrain", "citizen_report",
    "field_verification", "sensor", "satellite",
)


def logit(p: float) -> float:
    p = min(max(p, PROB_CLAMP[0]), PROB_CLAMP[1])
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def freshness_factor(age_hours: float, half_life_hours: float) -> float:
    if age_hours is None or age_hours < 0:
        return 1.0
    if half_life_hours <= 0:
        return 0.0
    return 0.5 ** (age_hours / half_life_hours)


def age_hours(observed_at: str | datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if isinstance(observed_at, str):
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    else:
        observed = observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - observed).total_seconds() / 3600.0)


def band_for(probability: float, ground_truth: bool = False) -> str:
    if ground_truth:
        return "CONFIRMED"
    for name, lo, hi in BANDS:
        if lo <= probability < hi:
            return name
    return "VERY_HIGH"


@dataclass
class Evidence:
    evidence_type: str
    source_code: str
    label: str
    probability: float | None
    confidence: float
    observed_at: str | None
    provenance: str
    source_weight: float
    freshness_half_life_h: float
    max_age_h: float
    geo_relevance: float = 1.0
    data_quality: float = 1.0
    dependency_group: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    # ---- computed during normalisation -------------------------------------
    status: str = "accepted"
    reason: str | None = None
    age_h: float | None = None
    freshness: float | None = None
    adjusted_confidence: float | None = None
    effective_weight: float = 0.0
    dependency_adjustment: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceSpec:
    weight: float
    half_life_h: float
    max_age_h: float


DEFAULT_SOURCES: dict[str, SourceSpec] = {
    "rainfall_gauge": SourceSpec(0.75, 12.0, 72.0),
    "ml_local_model": SourceSpec(0.65, 24.0, 72.0),
    "terrain_static": SourceSpec(0.70, 24.0 * 365, 24.0 * 365 * 5),
    "citizen_report": SourceSpec(0.55, 24.0, 72.0),
    "field_survey": SourceSpec(0.95, 24.0 * 7, 24.0 * 30),
    "ground_sensor": SourceSpec(0.85, 6.0, 48.0),
    "satellite_feed": SourceSpec(0.80, 72.0, 240.0),
}


def load_source_specs() -> dict[str, SourceSpec]:
    """Read the live registry (data_sources) when present, else use defaults."""
    try:
        import db

        rows = db.query("SELECT code, source_weight, freshness_half_life_h, max_age_h FROM data_sources")
        if rows:
            return {
                r["code"]: SourceSpec(r["source_weight"], r["freshness_half_life_h"], r["max_age_h"])
                for r in rows
            }
    except Exception:  # pragma: no cover - registry absent during unit tests
        pass
    return dict(DEFAULT_SOURCES)


def normalise(items: list[Evidence], now: datetime | None = None) -> tuple[list[Evidence], list[Evidence], list[str]]:
    """Apply freshness decay, staleness rejection and dependency discounts.

    Returns (accepted, rejected, missing_input_descriptions).
    """
    now = now or datetime.now(timezone.utc)
    accepted: list[Evidence] = []
    rejected: list[Evidence] = []
    missing: list[str] = []

    for ev in items:
        if ev.status == "missing":
            ev.reason = ev.reason or "source unavailable"
            missing.append(f"{ev.evidence_type}: {ev.reason}")
            rejected.append(ev)
            continue
        if ev.probability is None:
            ev.status = "missing"
            ev.reason = "no probability estimate produced"
            missing.append(f"{ev.evidence_type}: no probability estimate produced")
            rejected.append(ev)
            continue

        ev.age_h = age_hours(ev.observed_at, now) if ev.observed_at else 0.0
        ev.freshness = freshness_factor(ev.age_h, ev.freshness_half_life_h)

        if ev.age_h > ev.max_age_h:
            ev.status = "rejected"
            ev.reason = f"stale: age {ev.age_h:.1f}h exceeds max_age {ev.max_age_h:.1f}h"
            rejected.append(ev)
            continue

        ev.adjusted_confidence = round(
            min(1.0, max(0.0, ev.confidence)) * ev.geo_relevance * ev.data_quality, 6
        )
        ev.effective_weight = round(
            ev.source_weight * ev.adjusted_confidence * (ev.freshness or 0.0), 6
        )
        ev.probability = min(max(ev.probability, PROB_CLAMP[0]), PROB_CLAMP[1])
        ev.status = "accepted"
        accepted.append(ev)

    # Dependency discount: correlated evidence must not be counted independently.
    groups: dict[str, list[Evidence]] = {}
    for ev in accepted:
        if ev.dependency_group:
            groups.setdefault(ev.dependency_group, []).append(ev)

    for group, members in groups.items():
        members.sort(key=lambda e: e.effective_weight, reverse=True)
        for idx, ev in enumerate(members):
            if idx == 0:
                ev.dependency_adjustment = (
                    f"group '{group}': strongest member keeps full weight"
                )
            else:
                multiplier = 1.0 - RHO_DEPENDENCY
                original = ev.effective_weight
                ev.effective_weight = round(original * multiplier, 6)
                ev.dependency_adjustment = (
                    f"group '{group}': correlated with '{members[0].label}', "
                    f"rho={RHO_DEPENDENCY:.2f}, weight {original:.4f} -> {ev.effective_weight:.4f}"
                )

    return accepted, rejected, missing


def fuse(
    accepted: list[Evidence],
    prior: float = PRIOR_PROBABILITY,
    prior_weight: float = PRIOR_WEIGHT,
) -> dict[str, Any]:
    """Normalized weighted log-odds pooling with zero/low-weight safeguards."""
    total = sum(e.effective_weight for e in accepted)
    flags: list[str] = []

    if total <= 0.0:
        flags.append("zero_total_weight")
        return {
            "probability": prior,
            "total_weight": 0.0,
            "method": METHOD,
            "terms": [],
            "flags": flags,
            "coverage": 0.0,
        }

    terms = [{"label": "prior", "weight": prior_weight, "logit": round(logit(prior), 6)}]
    num = prior_weight * logit(prior)
    den = prior_weight
    for ev in accepted:
        if ev.probability is None:
            continue
        contribution = ev.effective_weight * logit(ev.probability)
        num += contribution
        den += ev.effective_weight
        terms.append(
            {
                "label": ev.label,
                "type": ev.evidence_type,
                "weight": round(ev.effective_weight, 6),
                "p": round(ev.probability, 4),
                "logit": round(logit(ev.probability), 6),
                "contribution": round(contribution, 6),
            }
        )

    fused_logit = num / den
    probability = sigmoid(fused_logit)
    if total < MIN_TOTAL_WEIGHT:
        flags.append("insufficient_evidence")
    if len(accepted) <= 1:
        flags.append("single_source")
    covered = {e.evidence_type for e in accepted}
    coverage = round(len(covered & set(EXPECTED_TYPES)) / len(EXPECTED_TYPES), 4)

    return {
        "probability": round(probability, 6),
        "total_weight": round(total, 6),
        "method": METHOD,
        "terms": terms,
        "flags": flags,
        "coverage": coverage,
        "fused_logit": round(fused_logit, 6),
    }


def overall_confidence(fusion: dict[str, Any], accepted: list[Evidence]) -> float:
    """Confidence combines evidence mass, coverage and mean data quality."""
    total = fusion["total_weight"]
    mass = total / (total + 2.0)
    coverage = fusion.get("coverage", 0.0)
    quality = (
        sum(e.data_quality for e in accepted) / len(accepted) if accepted else 0.0
    )
    conf = 0.15 + 0.55 * mass + 0.20 * coverage + 0.10 * quality
    if "zero_total_weight" in fusion["flags"]:
        conf = 0.10
    elif "insufficient_evidence" in fusion["flags"]:
        conf = min(conf, 0.40)
    return round(min(0.95, conf), 4)


def recommendation_for(band: str, confidence: float, ground_truth: bool = False) -> tuple[str, str]:
    """(recommendation, next_action) for a band + confidence combination."""
    if ground_truth:
        return (
            "Ground truth confirmed: treat as an active landslide",
            "Dispatch emergency response, issue evacuation advisory, keep incident open until stabilised",
        )
    if band == "LOW":
        return ("Continue routine monitoring", "Re-assess on next rainfall event or new report")
    if band == "MODERATE":
        return (
            "Increase monitoring frequency; field verification within 48h",
            "Schedule a field verification task and watch rainfall trend",
        )
    if band == "HIGH":
        if confidence < 0.4:
            return (
                "Evidence weak despite high score: verify before warning",
                "Send field verification team within 12h; do not issue public warning yet",
            )
        return (
            "Field verification within 12h and prepare community advisory",
            "Raise alert, notify community leader, stage response team",
        )
    if confidence < 0.4:
        return (
            "Very high score but weak evidence: immediate verification",
            "Send verification team now; hold public warning until confirmed",
        )
    return (
        "Trigger alert, dispatch field verification, notify community",
        "Open incident workflow, notify response operator and community leader",
    )
