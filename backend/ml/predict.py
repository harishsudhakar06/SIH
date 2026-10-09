"""Local ML inference used as one evidence source by the Belief Fusion Engine.

Everything runs in-process with joblib/scikit-learn. No external model API is
called. If no trained artifact exists the caller receives a typed
`ModelUnavailable` error which the fusion engine records as a *missing input*.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from ml.train import FEATURES, LAND_USES, LITHOLOGIES

MODEL_DIR = Path(__file__).resolve().parent / "models"
_cache: dict[str, Any] | None = None


class ModelUnavailable(RuntimeError):
    pass


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    latest = MODEL_DIR / "latest.joblib"
    if not latest.exists():
        raise ModelUnavailable("no trained model artifact - run `python backend/ml/train.py`")
    payload = joblib.load(latest)
    _cache = payload
    return payload


def model_info() -> dict[str, Any]:
    payload = _load()
    meta = payload["meta"]
    return {
        "model_name": meta["model_name"],
        "version": meta["version"],
        "trained_at": meta["trained_at"],
        "features": meta["features"],
        "metrics": meta["metrics"],
        "data_mode": meta["data_mode"],
        "notes": meta["notes"],
        "artifact": Path(meta["artifact"]).name,
    }


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def predict_for_slope(slope: dict[str, Any], rainfall_24h: float | None,
                      rainfall_72h: float | None, sensor_max: float | None) -> dict[str, Any]:
    """Return {probability, confidence, detail} for one slope."""
    payload = _load()
    model, meta = payload["model"], payload["meta"]

    missing: list[str] = []
    if rainfall_24h is None:
        rainfall_24h = 0.0
        missing.append("rainfall_24h (imputed 0.0)")
    if rainfall_72h is None:
        rainfall_72h = rainfall_24h * 2.0
        missing.append("rainfall_72h (imputed from 24h)")
    if sensor_max is None:
        sensor_max = 0.0
        missing.append("sensor_max (imputed 0.0)")

    litho = slope.get("lithology") or "Granite"
    landuse = slope.get("land_use") or "Forest"
    aspect = float(slope.get("aspect_deg") or 0.0)

    row = {
        "slope_angle_deg": float(slope.get("slope_angle_deg") or 30.0),
        "aspect_sin": math.sin(math.radians(aspect)),
        "aspect_cos": math.cos(math.radians(aspect)),
        "elevation_m": float(slope.get("elevation_m") or 900.0),
        "curvature": float(slope.get("curvature") or 0.0),
        "lithology_code": LITHOLOGIES.index(litho) if litho in LITHOLOGIES else LITHOLOGIES.index("Granite"),
        "land_use_code": LAND_USES.index(landuse) if landuse in LAND_USES else LAND_USES.index("Forest"),
        "road_cut": int(slope.get("road_cut") or 0),
        "vegetation": float(slope.get("vegetation") if slope.get("vegetation") is not None else 0.5),
        "drainage_density": float(slope.get("drainage_density") or 0.6),
        "rainfall_24h": float(rainfall_24h),
        "rainfall_72h": float(rainfall_72h),
        "antecedent_index": float(rainfall_72h) * 0.5,
        "historical_incidents": int(slope.get("historical_incidents") or 0),
        "sensor_max": float(sensor_max),
    }

    import pandas as pd

    X = pd.DataFrame([[row[f] for f in FEATURES]], columns=FEATURES)
    proba = float(model.predict_proba(X)[0][1])

    # Confidence from predictive entropy of the pooled tree probabilities.
    Xv = X.to_numpy()
    tree_probs = np.array([t.predict_proba(Xv)[0][1] for t in model.estimators_])
    p_mean = float(np.clip(tree_probs.mean(), 1e-6, 1 - 1e-6))
    entropy = -(p_mean * math.log(p_mean) + (1 - p_mean) * math.log(1 - p_mean))
    norm_entropy = entropy / math.log(2)
    confidence = round(0.45 + 0.5 * (1 - norm_entropy), 4)
    if missing:
        confidence = round(max(0.30, confidence - 0.08 * len(missing)), 4)

    # Sensitivity indicator: which inputs sit furthest from the training
    # distribution, weighted by impurity-based feature importance.
    importances = dict(zip(FEATURES, model.feature_importances_))
    means = np.array([_training_mean(meta, f) for f in FEATURES])
    scales = np.array([_training_scale(meta, f) for f in FEATURES])
    values = X.to_numpy()[0]
    z = (values - means) / scales
    score = {f: abs(z[i]) * importances[f] for i, f in enumerate(FEATURES)}
    top = sorted(score.items(), key=lambda kv: -kv[1])[:5]

    return {
        "probability": round(proba, 6),
        "confidence": confidence,
        "detail": {
            "features": row,
            "missing_inputs": missing,
            "tree_agreement_std": round(float(tree_probs.std()), 4),
            "top_sensitivity": [
                {"feature": f, "value": round(float(row[f]), 4),
                 "z_vs_train": round(float(z[FEATURES.index(f)]), 3),
                 "importance": round(importances[f], 4)}
                for f, _ in top
            ],
            "sensitivity_note": (
                "Sensitivity indicator = |z-score vs training distribution| x feature importance. "
                "It is NOT a signed attribution."
            ),
            "model_version": meta["version"],
            "data_mode": meta["data_mode"],
        },
    }


def _training_mean(meta: dict, feature: str) -> float:
    stats = meta.get("train_stats", {}).get(feature)
    return float(stats["mean"]) if stats else 0.0


def _training_scale(meta: dict, feature: str) -> float:
    stats = meta.get("train_stats", {}).get(feature)
    return float(stats["std"]) or 1.0 if stats else 1.0
