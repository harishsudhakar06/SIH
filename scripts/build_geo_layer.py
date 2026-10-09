"""Build a small NE-India state-boundary GeoJSON for the local basemap layer.

Source (downloaded once, kept out of the repo): GADM-derived India states
from https://github.com/geohacker/india (CC-BY-SA / open data).
Output: backend/data/geo/ne_states.geojson — 8 NE states, simplified with
shapely so the browser layer stays small. No paid map API involved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from shapely.geometry import mapping, shape

NE_STATES = [
    "Arunachal Pradesh",
    "Assam",
    "Meghalaya",
    "Nagaland",
    "Manipur",
    "Mizoram",
    "Tripura",
    "Sikkim",
]
TOLERANCE = 0.012  # ~1.3 km — plenty for a regional context layer


def main() -> None:
    src = Path(os.environ["TEMP"]) / "india_states_raw.geojson"
    out = Path(__file__).resolve().parents[1] / "backend" / "data" / "geo" / "ne_states.geojson"
    data = json.loads(src.read_text(encoding="utf-8"))
    features = []
    for ft in data["features"]:
        name = ft["properties"].get("NAME_1")
        if name not in NE_STATES:
            continue
        geom = shape(ft["geometry"]).simplify(TOLERANCE, preserve_topology=True)
        features.append(
            {
                "type": "Feature",
                "properties": {"state": name},
                "geometry": mapping(geom),
            }
        )
    found = {ft["properties"]["state"] for ft in features}
    missing = set(NE_STATES) - found
    if missing:
        raise SystemExit(f"missing states in source: {sorted(missing)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {out} ({out.stat().st_size} bytes, {len(features)} states)")


if __name__ == "__main__":
    main()
