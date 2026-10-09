"""Train the local landslide-susceptibility model (scikit-learn, no external APIs).

IMPORTANT / HONEST LABELLING
----------------------------
No field dataset survived the project loss, and no public labelled NE-India
landslide inventory is bundled. This trainer therefore builds a *synthetic but
physically-informed* prototype dataset (documented generating process below),
trains a RandomForest on it, and records the ACTUAL cross-validated metrics in
the database (ml_runs). Those metrics describe performance on synthetic data
only - they are not field-validated accuracy claims.

Generating process (deterministic, seed=42):
  z = 0.045·(slope_angle-30) + 0.9·road_cut + lithology_susc + landuse_susc
      + 0.020·rain24 + 0.012·rain72 + 0.03·historical_incidents
      + 0.7·(1-vegetation) + 0.8·drainage_density - 0.02·curvature
      + N(0, 1.1)
  label ~ Bernoulli(sigmoid(z - 1.2))

Run:  python backend/ml/train.py
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import cross_val_score, train_test_split

FEATURES = [
    "slope_angle_deg", "aspect_sin", "aspect_cos", "elevation_m", "curvature",
    "lithology_code", "land_use_code", "road_cut", "vegetation", "drainage_density",
    "rainfall_24h", "rainfall_72h", "antecedent_index", "historical_incidents", "sensor_max",
]

LITHOLOGY_SUSC = {"Shale": 1.1, "Phyllite": 1.0, "Alluvium": 0.7, "Sandstone": 0.5,
                  "Limestone": 0.4, "Gneiss": 0.3, "Granite": 0.2, "Mafic": 0.2}
LANDUSE_SUSC = {"Quarry": 0.9, "Urban fringe": 0.5, "Agriculture": 0.35, "Scrub": 0.15,
                "Tea plantation": 0.1, "Forest": 0.0}
LITHOLOGIES = list(LITHOLOGY_SUSC)
LAND_USES = list(LANDUSE_SUSC)

MODEL_DIR = Path(__file__).resolve().parent / "models"


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def generate_dataset(n: int = 6000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    slope_angle = np.clip(rng.normal(32, 11, n), 8, 62)
    aspect = rng.uniform(0, 360, n)
    elevation = np.clip(rng.normal(900, 450, n), 80, 3200)
    curvature = rng.normal(0, 1, n)
    litho = rng.choice(LITHOLOGIES, n, p=[0.16, 0.12, 0.14, 0.16, 0.10, 0.12, 0.14, 0.06])
    landuse = rng.choice(LAND_USES, n, p=[0.05, 0.10, 0.25, 0.18, 0.12, 0.30])
    road_cut = rng.binomial(1, 0.32, n)
    vegetation = np.clip(rng.beta(5, 3, n), 0.02, 0.98)
    drainage = np.clip(rng.gamma(2.0, 0.25, n), 0.05, 2.0)
    rain24 = np.clip(rng.gamma(2.2, 18.0, n), 0, 380)
    rain72 = np.clip(rain24 * rng.uniform(1.4, 3.6, n) + rng.gamma(2.0, 12.0, n), 0, 780)
    antecedent = np.clip(rain72 * 0.4 + rng.gamma(2.0, 10.0, n), 0, 500)
    historical = rng.poisson(0.7, n)
    sensor_max = np.clip(rng.gamma(1.6, 8.0, n), 0, 90)

    litho_susc = np.array([LITHOLOGY_SUSC[v] for v in litho])
    landuse_susc = np.array([LANDUSE_SUSC[v] for v in landuse])

    z = (
        0.045 * (slope_angle - 30)
        + 0.9 * road_cut
        + litho_susc
        + landuse_susc
        + 0.020 * rain24
        + 0.012 * rain72
        + 0.03 * historical
        + 0.7 * (1 - vegetation)
        + 0.8 * drainage
        - 0.02 * curvature
        + 0.010 * sensor_max
        + rng.normal(0, 1.1, n)
    )
    p = sigmoid(z - 4.6)
    label = rng.binomial(1, p)

    return pd.DataFrame(
        {
            "slope_angle_deg": slope_angle,
            "aspect_sin": np.sin(np.radians(aspect)),
            "aspect_cos": np.cos(np.radians(aspect)),
            "elevation_m": elevation,
            "curvature": curvature,
            "lithology_code": [LITHOLOGIES.index(v) for v in litho],
            "land_use_code": [LAND_USES.index(v) for v in landuse],
            "road_cut": road_cut,
            "vegetation": vegetation,
            "drainage_density": drainage,
            "rainfall_24h": rain24,
            "rainfall_72h": rain72,
            "antecedent_index": antecedent,
            "historical_incidents": historical,
            "sensor_max": sensor_max,
            "unstable": label,
        }
    )


def train(n: int = 6000, seed: int = 42) -> dict:
    df = generate_dataset(n, seed)
    X = df[FEATURES]
    y = df["unstable"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=seed, stratify=y
    )
    model = RandomForestClassifier(
        n_estimators=300, max_depth=11, min_samples_leaf=4,
        random_state=seed, n_jobs=-1,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]
    cv = cross_val_score(
        RandomForestClassifier(n_estimators=150, max_depth=11, min_samples_leaf=4,
                               random_state=seed, n_jobs=-1),
        X, y, cv=5, scoring="roc_auc",
    )

    metrics = {
        "accuracy": round(float(accuracy_score(y_test, y_pred)), 4),
        "precision": round(float(precision_score(y_test, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_test, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_test, y_pred, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_test, y_proba)), 4),
        "cv_roc_auc_mean": round(float(cv.mean()), 4),
        "cv_roc_auc_std": round(float(cv.std()), 4),
        "train_rows": int(len(X_train)),
        "test_rows": int(len(X_test)),
        "positive_rate": round(float(y.mean()), 4),
    }

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = MODEL_DIR / f"susceptibility_rf_{stamp}.joblib"
    meta = {
        "model_name": "slope_susceptibility_rf",
        "version": stamp,
        "features": FEATURES,
        "metrics": metrics,
        "train_stats": {
            f: {"mean": float(X_train[f].mean()), "std": float(X_train[f].std()) or 1.0}
            for f in FEATURES
        },
        "trained_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "artifact": str(artifact),
        "data_mode": "synthetic_prototype",
        "lithologies": LITHOLOGIES,
        "land_uses": LAND_USES,
        "notes": (
            "RandomForest trained on a synthetic physically-informed dataset; "
            "metrics are computed on that synthetic hold-out set only."
        ),
    }
    joblib.dump({"model": model, "meta": meta}, artifact)
    latest = MODEL_DIR / "latest.joblib"
    joblib.dump({"model": model, "meta": meta}, latest)
    (MODEL_DIR / "latest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    _record_run(meta)
    return meta


def _record_run(meta: dict) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import db

    db.init_db()
    db.execute(
        """INSERT INTO ml_runs (model_name, version, trained_at, metrics_json, n_samples,
                                features, data_mode, artifact_path, notes)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            meta["model_name"], meta["version"], meta["trained_at"],
            json.dumps(meta["metrics"]), meta["metrics"]["train_rows"] + meta["metrics"]["test_rows"],
            json.dumps(meta["features"]), meta["data_mode"], meta["artifact"], meta["notes"],
        ),
    )


if __name__ == "__main__":
    result = train()
    print(json.dumps(result, indent=2))
