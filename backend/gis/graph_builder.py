"""Slope graph builder: turns slopes into nodes and documented relations into edges.

Relationship types (each edge stores its `basis`, `confidence` and `validated` flag):

  spatial_adjacency     haversine distance <= 6 km           (documented rule)
  rainfall_similarity   24h rainfall of nearest gauges within 12 mm, <= 15 km
  geology_similarity    same mapped lithology within 12 km
  hydrology_downslope   elevation gradient approximation (higher -> lower, <= 5 km)

Honesty note: hydrology edges are an APPROXIMATION from elevation difference.
DEM flow accumulation / catchment delineation is not available in this build,
so every hydrology edge is stored with validated = 0 and a basis string saying so.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import db
from gis.geo import haversine_km

MAX_PAIR_KM = 15.0
ADJACENCY_KM = 6.0
GEOLOGY_KM = 12.0
RAINFALL_KM = 15.0
RAINFALL_DIFF_MM = 12.0
HYDRO_KM = 5.0
HYDRO_MIN_DROP_M = 40.0


def _station_for(slope: dict[str, Any], stations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not stations:
        return None
    return min(stations, key=lambda s: haversine_km(slope["lat"], slope["lng"], s["lat"], s["lng"]))


def build_edges() -> dict[str, int]:
    slopes = db.query("SELECT * FROM slopes ORDER BY id")
    stations = db.query(
        """SELECT r.* FROM rainfall_observations r
           JOIN (SELECT station_code, MAX(observed_at) AS mx FROM rainfall_observations
                 GROUP BY station_code) m
             ON m.station_code = r.station_code AND m.mx = r.observed_at"""
    )
    for s in slopes:
        st = _station_for(s, stations)
        s["_station"] = st
        s["_mm24"] = (st or {}).get("mm_24h")

    ts = db.utcnow()
    counts = {"spatial_adjacency": 0, "rainfall_similarity": 0, "geology_similarity": 0,
              "hydrology_downslope": 0}

    def upsert(src: int, dst: int, rel: str, strength: float, distance: float,
               directionality: str, confidence: float, basis: str, meta: dict | None = None) -> None:
        db.execute(
            """INSERT INTO slope_edges (src_slope_id, dst_slope_id, rel_type, strength, distance_km,
                   directionality, confidence, basis, validated, meta_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,0,?,?)
               ON CONFLICT (src_slope_id, dst_slope_id, rel_type)
               DO UPDATE SET strength=excluded.strength, distance_km=excluded.distance_km,
                             confidence=excluded.confidence, basis=excluded.basis,
                             meta_json=excluded.meta_json""",
            (src, dst, rel, round(strength, 4), round(distance, 3), directionality,
             round(confidence, 3), basis, json.dumps(meta or {}), ts),
        )
        counts[rel] += 1

    for i, a in enumerate(slopes):
        for b in slopes[i + 1:]:
            d = haversine_km(a["lat"], a["lng"], b["lat"], b["lng"])
            if d > MAX_PAIR_KM:
                continue

            if d <= ADJACENCY_KM:
                strength = max(0.35, 1.0 - d / ADJACENCY_KM)
                upsert(a["id"], b["id"], "spatial_adjacency", strength, d, "symmetric", 0.9,
                       f"haversine distance {d:.2f} km <= {ADJACENCY_KM} km adjacency rule")

            if a["lithology"] and a["lithology"] == b["lithology"] and d <= GEOLOGY_KM:
                upsert(a["id"], b["id"], "geology_similarity", 0.55, d, "symmetric", 0.6,
                       f"same mapped lithology ({a['lithology']}) within {GEOLOGY_KM} km")

            mm_a, mm_b = a.get("_mm24"), b.get("_mm24")
            if mm_a is not None and mm_b is not None and d <= RAINFALL_KM:
                diff = abs(float(mm_a) - float(mm_b))
                if diff <= RAINFALL_DIFF_MM:
                    strength = max(0.35, 1.0 - diff / RAINFALL_DIFF_MM)
                    upsert(a["id"], b["id"], "rainfall_similarity", strength, d, "symmetric", 0.7,
                           f"nearest-gauge 24h rainfall differs by {diff:.1f} mm <= {RAINFALL_DIFF_MM} mm",
                           {"mm24_a": mm_a, "mm24_b": mm_b})

            if d <= HYDRO_KM and a["elevation_m"] and b["elevation_m"]:
                drop = float(a["elevation_m"]) - float(b["elevation_m"])
                if abs(drop) >= HYDRO_MIN_DROP_M:
                    hi, lo = (a, b) if drop > 0 else (b, a)
                    strength = min(0.85, 0.40 + abs(drop) / 600.0)
                    upsert(hi["id"], lo["id"], "hydrology_downslope", strength, d, "directed", 0.5,
                           (f"elevation gradient {abs(drop):.0f} m over {d:.2f} km "
                            f"(approximation: DEM flow routing unavailable)"),
                           {"elev_drop_m": round(abs(drop), 1), "validated": False})

    return counts


def rebuild() -> dict[str, Any]:
    db.execute("DELETE FROM slope_edges")
    counts = build_edges()
    return {"edges_created": counts, "total": sum(counts.values())}
