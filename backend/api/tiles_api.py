"""Local basemap tile endpoint.

Serves `/api/tiles/<z>/<x>/<y>.png`:
  1. local cached tile (backend/data/tiles/cache) if present  -> fully offline
  2. otherwise fetch from OpenStreetMap and cache it           -> first-run online
  3. otherwise, or when BHURAKSHA_TILE_OFFLINE=1, return a neutral placeholder tile

No paid map API (Google/Mapbox) or API key is involved anywhere.
Cache can be pre-warmed for the NE India viewport with scripts/warm_tiles.py.
"""
from __future__ import annotations

import io
import time

import requests
from flask import Blueprint, jsonify, send_file

from config import config

bp = Blueprint("tiles_api", __name__, url_prefix="/api/tiles")
OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
HEADERS = {"User-Agent": "BhuRaksha-SIH26001/1.0 (prototype research build)"}
MAX_ZOOM = 18
FETCH_TIMEOUT = 10
RETRIES = 2

# In-memory guard: avoid re-hitting OSM for tiles that just failed.
_fail_cache: dict[tuple[int, int, int], float] = {}
_FAIL_TTL = 300
_last_degraded_log = 0.0


def _placeholder() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (256, 256), (232, 234, 231))
    dr = ImageDraw.Draw(img)
    for i in range(0, 256, 32):
        dr.line([(i, 0), (i, 256)], fill=(221, 224, 219), width=1)
        dr.line([(0, i), (256, i)], fill=(221, 224, 219), width=1)
    dr.rectangle([0, 0, 255, 255], outline=(205, 209, 202))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _valid(z: int, x: int, y: int) -> bool:
    if not (0 <= z <= MAX_ZOOM):
        return False
    limit = 2 ** z
    return 0 <= x < limit and 0 <= y < limit


def _send_png(data: bytes, source: str):
    resp = send_file(io.BytesIO(data), mimetype="image/png")
    resp.headers["X-Tile-Source"] = source
    resp.headers["Cache-Control"] = "public, max-age=604800" if source == "cache" else "public, max-age=300"
    return resp


def _log_degraded(z: int, x: int, y: int, reason: str) -> None:
    """Record placeholder fallback in ingestion_log, rate-limited to one row/5 min."""
    global _last_degraded_log
    now = time.time()
    if now - _last_degraded_log < 300:
        return
    _last_degraded_log = now
    try:
        from db import get_conn
        from datetime import datetime, timezone

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO ingestion_log (source_code, status, detail, created_at) VALUES (?, ?, ?, ?)",
                ("osm_tiles", "degraded", f"placeholder tile {z}/{x}/{y}: {reason}", datetime.now(timezone.utc).isoformat()),
            )
    except Exception:
        pass  # tile serving must never fail on logging


@bp.get("/<int:z>/<int:x>/<int:y>.png")
def tile(z: int, x: int, y: int):
    if not _valid(z, x, y):
        return _send_png(_placeholder(), "invalid")

    cache_path = config.TILE_CACHE_DIR / str(z) / str(x) / f"{y}.png"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return _send_png(cache_path.read_bytes(), "cache")

    key = (z, x, y)
    failed_at = _fail_cache.get(key)
    if failed_at and time.time() - failed_at < _FAIL_TTL:
        _log_degraded(z, x, y, "recent fetch failure (cached in-memory)")
        return _send_png(_placeholder(), "offline")

    if not config.TILE_OFFLINE:
        for attempt in range(RETRIES):
            try:
                resp = requests.get(OSM_URL.format(z=z, x=x, y=y), headers=HEADERS, timeout=FETCH_TIMEOUT)
                if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(resp.content)
                    _fail_cache.pop(key, None)
                    return _send_png(resp.content, "network")
                if resp.status_code == 404:
                    break  # invalid tile — do not retry
            except requests.RequestException:
                if attempt < RETRIES - 1:
                    time.sleep(0.4)
        _fail_cache[key] = time.time()

    _log_degraded(z, x, y, "network unavailable or rate-limited")
    return _send_png(_placeholder(), "offline")


@bp.get("/status")
def tiles_status():
    cache_dir = config.TILE_CACHE_DIR
    pngs = list(cache_dir.glob("*/*/*.png")) if cache_dir.exists() else []
    zooms = sorted({int(p.parent.parent.name) for p in pngs}) if pngs else []
    return jsonify({
        "mode": "offline_forced" if config.TILE_OFFLINE else ("cache_plus_network" if not _fail_cache else "cache_plus_network_with_failures"),
        "cache_tiles": len(pngs),
        "cache_zooms": zooms,
        "provider": "OpenStreetMap (tile.openstreetmap.org)",
        "data_mode": "local_cache" if pngs else "unavailable",
        "note": "Pre-warm NE India viewport: python scripts/warm_tiles.py",
    })
