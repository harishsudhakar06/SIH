"""ML subsystem tests: training happens only if no artifact exists yet."""
from __future__ import annotations

from pathlib import Path

import pytest

from config import config


def _ensure_model() -> dict:
    from ml.train import train

    latest = config.ML_MODEL_DIR / "latest.joblib"
    if not latest.exists():
        return train(n=2500)
    import json

    meta_path = config.ML_MODEL_DIR / "latest.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    return train(n=2500)


def test_training_produces_metrics_and_artifact():
    meta = _ensure_model()
    assert meta["data_mode"] == "synthetic_prototype"
    metrics = meta["metrics"]
    for key in ("accuracy", "roc_auc", "cv_roc_auc_mean", "f1"):
        assert key in metrics
    assert 0.5 <= metrics["roc_auc"] <= 1.0
    assert Path(meta["artifact"]).exists() or (config.ML_MODEL_DIR / "latest.joblib").exists()


def test_predict_returns_bounded_probability():
    _ensure_model()
    from ml import predict

    slope = {"slope_angle_deg": 45, "aspect_deg": 180, "elevation_m": 1200,
             "curvature": -0.8, "lithology": "Shale", "land_use": "Agriculture",
             "road_cut": 1, "vegetation": 0.25, "drainage_density": 1.2,
             "historical_incidents": 2}
    out = predict.predict_for_slope(slope, 120.0, 300.0, 25.0)
    assert 0.0 <= out["probability"] <= 1.0
    assert 0.0 < out["confidence"] <= 1.0
    assert out["detail"]["top_sensitivity"]
    assert out["detail"]["model_version"]

    calmer = predict.predict_for_slope({**slope, "slope_angle_deg": 10, "road_cut": 0,
                                        "vegetation": 0.9, "historical_incidents": 0},
                                       2.0, 5.0, 1.0)
    assert calmer["probability"] < out["probability"], "riskier inputs should score higher"


def test_predict_without_rainfall_flags_missing_input():
    _ensure_model()
    from ml import predict

    slope = {"slope_angle_deg": 35, "lithology": "Sandstone", "land_use": "Forest"}
    out = predict.predict_for_slope(slope, None, None, None)
    assert any("rainfall_24h" in m for m in out["detail"]["missing_inputs"])
    assert out["confidence"] <= 0.9


def test_model_info_endpoint(client, auth_headers):
    _ensure_model()
    res = client.get("/api/ml/model-info")
    assert res.status_code == 200
    model = res.get_json()["model"]
    assert model["metrics"]["roc_auc"] > 0.5
    assert model["data_mode"] == "synthetic_prototype"


def test_predict_endpoint_requires_analyst(client, auth_headers, any_slope):
    _ensure_model()
    assert client.post("/api/ml/predict", json={"slope_id": any_slope["id"]}).status_code == 401
    res = client.post("/api/ml/predict", json={"slope_id": any_slope["id"]},
                      headers=auth_headers("citizen"))
    assert res.status_code == 403
    ok = client.post("/api/ml/predict", json={"slope_id": any_slope["id"]},
                     headers=auth_headers("analyst"))
    assert ok.status_code == 200
    assert 0 <= ok.get_json()["prediction"]["probability"] <= 1
