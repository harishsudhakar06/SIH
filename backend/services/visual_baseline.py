"""Baseline visual evidence service (legitimate image processing, NOT a trained CNN).

HONEST LABELLING
----------------
There is no trained landslide image classifier in this build. This module only
extracts classical, well-defined image indicators with Pillow/numpy:

  * edge_density        - fraction of pixels changed by an edge filter (FIND_EDGES)
  * texture_variance    - variance of the luminance image (roughness proxy)
  * exposed_soil_ratio  - fraction of pixels in a brown/exposed-soil colour band
  * vegetation_ratio    - fraction of pixels in a green vegetation band
  * brightness          - mean luminance
  * dark_ratio          - fraction of very dark pixels (shadow / wet surface)

These indicators are recorded so an operator can see *what the photographs look
like statistically*. They are labelled `baseline_visual_heuristics` everywhere,
they are never called a landslide detector, and they can never on their own
confirm a landslide or raise a slope to CONFIRMED.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

METHOD = "baseline_visual_heuristics"
METHOD_VERSION = "1.0"
DISCLAIMER = (
    "Classical image statistics only - not a trained landslide classifier. "
    "Indicators describe the photograph, not a confirmed geological finding."
)

try:  # Pillow + numpy are hard dependencies (requirements.txt)
    from PIL import Image, ImageFilter
    import numpy as np
except Exception:  # pragma: no cover - reported as unavailable instead of faking results
    Image = None  # type: ignore
    np = None  # type: ignore


class VisualUnavailable(RuntimeError):
    pass


def _load(path: Path) -> "Any":
    if Image is None:
        raise VisualUnavailable("Pillow/numpy unavailable in this environment")
    try:
        img = Image.open(path)
        img.load()
    except Exception as exc:
        raise VisualUnavailable(f"unreadable image: {exc}") from exc
    return img.convert("RGB")


def analyse_file(path: str | Path) -> dict[str, Any]:
    """Extract indicators from a single stored photograph."""
    p = Path(path)
    if not p.exists():
        raise VisualUnavailable(f"file missing: {p.name}")
    img = _load(p)
    small = img.copy()
    small.thumbnail((512, 512))

    arr = np.asarray(small, dtype=float)            # H x W x 3
    lum = arr @ np.array([0.299, 0.587, 0.114])
    brightness = float(lum.mean() / 255.0)
    dark_ratio = float((lum < 45).mean())

    # edges
    edges = np.asarray(small.convert("L").filter(ImageFilter.FIND_EDGES), dtype=float)
    edge_density = float((edges > 40).mean())

    # texture: local variance of luminance via 8x8 blocks
    h, w = lum.shape
    bh, bw = max(1, h // 8), max(1, w // 8)
    blocks = lum[: bh * 8, : bw * 8].reshape(8, bh, 8, bw).transpose(0, 2, 1, 3).reshape(64, -1)
    texture_variance = float(blocks.var(axis=1).mean() / (255.0 ** 2))

    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    exposed = ((r > 90) & (r > g * 1.15) & (g >= b * 0.85) & (b < 170)).mean()
    vegetation = ((g > r * 1.05) & (g > b * 1.05) & (g > 55)).mean()

    indicators = {
        "method": METHOD,
        "method_version": METHOD_VERSION,
        "file": p.name,
        "edge_density": round(edge_density, 4),
        "texture_variance": round(texture_variance, 5),
        "exposed_soil_ratio": round(float(exposed), 4),
        "vegetation_ratio": round(float(vegetation), 4),
        "brightness": round(brightness, 4),
        "dark_ratio": round(dark_ratio, 4),
        "size_px": [img.width, img.height],
    }
    indicators["indicator_score"] = round(
        min(1.0, 0.55 * edge_density + 0.45 * exposed + 0.30 * min(texture_variance * 6, 1.0)), 4
    )
    indicators["disclaimer"] = DISCLAIMER
    return indicators


def analyse(paths: list[str | Path]) -> dict[str, Any]:
    """Aggregate indicators across the report's photographs."""
    files, failures = [], []
    for p in paths:
        try:
            files.append(analyse_file(p))
        except VisualUnavailable as exc:
            failures.append({"file": Path(p).name, "error": str(exc)})
    if not files:
        return {
            "method": METHOD, "available": False, "indicators": [],
            "errors": failures or [{"error": "no photograph attached to the report"}],
            "disclaimer": DISCLAIMER,
        }
    keys = ("edge_density", "texture_variance", "exposed_soil_ratio",
            "vegetation_ratio", "brightness", "dark_ratio", "indicator_score")
    mean = {k: round(sum(f[k] for f in files) / len(files), 4) for k in keys}
    # Indicative banding (documentation only, never a decision):
    if mean["indicator_score"] >= 0.55:
        reading = "high exposed-soil/edge signature - field check recommended"
    elif mean["indicator_score"] >= 0.35:
        reading = "moderate exposed-soil/edge signature - monitor"
    else:
        reading = "low exposed-soil/edge signature in these frames"
    return {
        "method": METHOD, "method_version": METHOD_VERSION, "available": True,
        "photo_count": len(files), "failed": failures,
        "per_file": files, "aggregate": mean, "reading": reading,
        "disclaimer": DISCLAIMER,
    }
