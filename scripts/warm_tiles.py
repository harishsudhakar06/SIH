"""Warm the local OSM tile cache for the NE India command-centre viewport.

Downloads tiles (z6-z10) once into backend/data/tiles/cache so the Risk Map
renders real geography on first paint instead of placeholder grids. Polite
rate-limited fetch with a proper User-Agent, per OSM tile usage policy.
Run once (or occasionally): python scripts/warm_tiles.py
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parents[1] / "backend" / "data" / "tiles" / "cache"
OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
HEADERS = {"User-Agent": "BhuRaksha-SIH26001/1.0 (prototype research build; one-off cache warm)"}
# Viewport around NE India: lat 21.5..29.2, lon 89.5..97.6
BBOX = (89.5, 21.5, 97.6, 29.2)  # min_lon, min_lat, max_lon, max_lat
ZOOMS = range(6, 11)
DELAY = 0.12  # seconds between fetches — stay well under OSM rate limits


def tile_range(z: int) -> range:
    min_lon, min_lat, max_lon, max_lat = BBOX
    n = 2 ** z
    x0 = int((min_lon + 180.0) / 360.0 * n)
    x1 = int((max_lon + 180.0) / 360.0 * n)
    def lat_to_y(lat: float) -> int:
        r = math.radians(lat)
        return int((1.0 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2.0 * n)
    y0, y1 = lat_to_y(max_lat), lat_to_y(min_lat)
    return range(max(0, x0), min(n - 1, x1) + 1), range(max(0, y0), min(n - 1, y1) + 1)


def main() -> None:
    total = cached = fetched = failed = 0
    for z in ZOOMS:
        xs, ys = tile_range(z)
        for x in xs:
            for y in ys:
                total += 1
                path = CACHE / str(z) / str(x) / f"{y}.png"
                if path.exists() and path.stat().st_size > 0:
                    cached += 1
                    continue
                try:
                    resp = requests.get(OSM_URL.format(z=z, x=x, y=y), headers=HEADERS, timeout=12)
                    if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(resp.content)
                        fetched += 1
                    else:
                        failed += 1
                        print(f"  skip {z}/{x}/{y}: HTTP {resp.status_code}", file=sys.stderr)
                except requests.RequestException as exc:
                    failed += 1
                    print(f"  fail {z}/{x}/{y}: {exc}", file=sys.stderr)
                time.sleep(DELAY)
        print(f"z{z}: done (running total cached={cached} fetched={fetched} failed={failed})")
    print(f"tile warm complete: {total} tiles in viewport, cache hits {cached}, downloaded {fetched}, failed {failed}")


if __name__ == "__main__":
    main()
