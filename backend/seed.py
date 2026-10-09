"""Database seeder: geography, evidence sources, demo users, prototype fixtures.

Everything written here is deterministic (seeded RNG) and clearly marked:
  * slopes/stations          -> project fixture with real NE-India town coordinates
  * rainfall/sensor readings -> data_mode='prototype_fixture' (NOT live measurements)
  * satellite rows           -> status='unavailable' (no provider connected)
  * citizen reports/field obs-> fixture data for the demo workflow

Run:  python backend/seed.py            (create/refresh fixture data)
      python backend/seed.py --assess   (also run the full assessment pipeline)
"""
from __future__ import annotations

import argparse
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db
from auth import create_user, hash_password
from config import config

CLUSTERS = [
    # (cluster, district, state, lat, lng, n, base_elev, lithologies, land_uses)
    ("Shillong Plateau", "East Khasi Hills", "Meghalaya", 25.5788, 91.8933, 4, 1600,
     ["Shale", "Sandstone"], ["Forest", "Urban fringe", "Agriculture"]),
    ("Cherrapunji Escarpment", "East Khasi Hills", "Meghalaya", 25.2994, 91.6964, 4, 1300,
     ["Sandstone", "Limestone"], ["Scrub", "Forest", "Agriculture"]),
    ("Pynursla Corridor", "East Khasi Hills", "Meghalaya", 25.2700, 92.1200, 3, 1100,
     ["Shale", "Phyllite"], ["Forest", "Agriculture"]),
    ("Jaintia Hills", "West Jaintia Hills", "Meghalaya", 25.1800, 92.2000, 3, 900,
     ["Limestone", "Sandstone"], ["Scrub", "Agriculture"]),
    ("Guwahati Foothills", "Kamrup", "Assam", 26.1445, 91.7362, 3, 450,
     ["Shale", "Alluvium"], ["Urban fringe", "Scrub"]),
    ("Karbi Anglong", "Karbi Anglong", "Assam", 25.9500, 92.7000, 3, 700,
     ["Sandstone", "Shale"], ["Forest", "Tea plantation"]),
    ("Dima Hasao Hills", "Dima Hasao", "Assam", 25.0740, 93.0170, 4, 800,
     ["Limestone", "Shale"], ["Forest", "Scrub"]),
    ("Tawang Highlands", "Tawang", "Arunachal Pradesh", 27.5887, 91.8697, 3, 2600,
     ["Gneiss", "Granite"], ["Forest", "Scrub"]),
    ("Bomdila Ridge", "West Kameng", "Arunachal Pradesh", 27.2647, 92.4090, 3, 2300,
     ["Gneiss", "Granite"], ["Forest", "Agriculture"]),
    ("Papum Pare", "Papum Pare", "Arunachal Pradesh", 27.0844, 93.6053, 3, 400,
     ["Sandstone", "Shale"], ["Forest", "Agriculture"]),
    ("Siang Valley", "East Siang", "Arunachal Pradesh", 28.0667, 95.3333, 3, 350,
     ["Alluvium", "Sandstone"], ["Forest", "Agriculture"]),
    ("Dibang Valley Road", "Lower Dibang Valley", "Arunachal Pradesh", 27.9000, 96.6000, 2, 500,
     ["Gneiss", "Phyllite"], ["Forest"]),
    ("Lower Subansiri", "Lower Subansiri", "Arunachal Pradesh", 27.5946, 93.8290, 3, 1500,
     ["Gneiss", "Schist"], ["Forest", "Agriculture"]),
    ("Dibrugarh Char", "Dibrugarh", "Assam", 27.4728, 94.9120, 2, 120,
     ["Alluvium"], ["Agriculture", "Tea plantation"]),
    ("Majuli Riverbank", "Majuli", "Assam", 26.9500, 94.1700, 2, 90,
     ["Alluvium"], ["Agriculture", "Scrub"]),
    ("Dimapur Foothills", "Dimapur", "Nagaland", 25.9044, 93.7266, 3, 350,
     ["Sandstone", "Shale"], ["Urban fringe", "Agriculture"]),
    ("Kohima Ridge", "Kohima", "Nagaland", 25.6751, 94.1086, 3, 1400,
     ["Shale", "Sandstone"], ["Forest", "Agriculture"]),
    ("Imphal Valley Edge", "Imphal West", "Manipur", 24.8170, 93.9368, 3, 780,
     ["Sandstone", "Schist"], ["Agriculture", "Urban fringe"]),
    ("Churachandpur Hills", "Churachandpur", "Manipur", 24.3333, 93.6833, 3, 1100,
     ["Schist", "Gneiss"], ["Forest", "Agriculture"]),
    ("Aizawl Slopes", "Aizawl", "Mizoram", 23.7271, 92.7176, 4, 1100,
     ["Sandstone", "Shale"], ["Urban fringe", "Forest"]),
    ("Lunglei South", "Lunglei", "Mizoram", 22.8833, 92.7333, 3, 950,
     ["Sandstone", "Mafic"], ["Forest", "Agriculture"]),
    ("Serchhip Belt", "Serchhip", "Mizoram", 23.3167, 92.8500, 2, 900,
     ["Sandstone"], ["Forest"]),
    ("Agartala Periphery", "West Tripura", "Tripura", 23.8315, 91.2868, 3, 80,
     ["Alluvium", "Sandstone"], ["Urban fringe", "Agriculture"]),
    ("Dhalai Hills", "Dhalai", "Tripura", 23.9680, 91.6500, 2, 150,
     ["Sandstone", "Alluvium"], ["Forest", "Agriculture"]),
    ("Gangtok Slope", "East Sikkim", "Sikkim", 27.3314, 88.6041, 3, 1800,
     ["Gneiss", "Schist"], ["Forest", "Urban fringe"]),
    ("Mangan North", "North Sikkim", "Sikkim", 27.5000, 88.5300, 3, 1600,
     ["Gneiss", "Schist"], ["Forest", "Agriculture"]),
]

STATIONS = [
    ("STN-SHL", "Shillong", 25.5788, 91.8933, "East Khasi Hills", "Meghalaya"),
    ("STN-SOH", "Sohra (Cherrapunji)", 25.2994, 91.6964, "East Khasi Hills", "Meghalaya"),
    ("STN-PYN", "Pynursla", 25.2700, 92.1200, "East Khasi Hills", "Meghalaya"),
    ("STN-JOW", "Jowai", 25.1800, 92.2000, "West Jaintia Hills", "Meghalaya"),
    ("STN-GHY", "Guwahati", 26.1445, 91.7362, "Kamrup", "Assam"),
    ("STN-DIP", "Diphu", 25.8450, 92.6270, "Karbi Anglong", "Assam"),
    ("STN-HFL", "Haflong", 25.0740, 93.0170, "Dima Hasao", "Assam"),
    ("STN-TAW", "Tawang", 27.5887, 91.8697, "Tawang", "Arunachal Pradesh"),
    ("STN-BOM", "Bomdila", 27.2647, 92.4090, "West Kameng", "Arunachal Pradesh"),
    ("STN-ITN", "Itanagar", 27.0844, 93.6053, "Papum Pare", "Arunachal Pradesh"),
    ("STN-PGT", "Pasighat", 28.0667, 95.3333, "East Siang", "Arunachal Pradesh"),
    ("STN-ZRO", "Ziro", 27.5946, 93.8290, "Lower Subansiri", "Arunachal Pradesh"),
    ("STN-DBR", "Dibrugarh", 27.4728, 94.9120, "Dibrugarh", "Assam"),
    ("STN-MJL", "Majuli", 26.9500, 94.1700, "Majuli", "Assam"),
    ("STN-DIM", "Dimapur", 25.9044, 93.7266, "Dimapur", "Nagaland"),
    ("STN-KHM", "Kohima", 25.6751, 94.1086, "Kohima", "Nagaland"),
    ("STN-IMP", "Imphal", 24.8170, 93.9368, "Imphal West", "Manipur"),
    ("STN-CCP", "Churachandpur", 24.3333, 93.6833, "Churachandpur", "Manipur"),
    ("STN-AIZ", "Aizawl", 23.7271, 92.7176, "Aizawl", "Mizoram"),
    ("STN-LNG", "Lunglei", 22.8833, 92.7333, "Lunglei", "Mizoram"),
    ("STN-AGR", "Agartala", 23.8315, 91.2868, "West Tripura", "Tripura"),
    ("STN-GAN", "Gangtok", 27.3314, 88.6041, "East Sikkim", "Sikkim"),
    ("STN-MNG", "Mangan", 27.5000, 88.5300, "North Sikkim", "Sikkim"),
]

DEMO_USERS = [
    ("admin", "admin123", "admin", "System Admin", None, None, None),
    ("citizen", "citizen123", "citizen", "Ravi Marak", "Mawsynram", "East Khasi Hills", "Meghalaya"),
    ("volunteer", "volunteer123", "field_volunteer", "Tasha Lyngdoh", "Mawsynram", "East Khasi Hills", "Meghalaya"),
    ("leader", "leader123", "community_leader", "Smt. Binodini Devi", "Cherrapunji", "East Khasi Hills", "Meghalaya"),
    ("response", "response123", "response_operator", "SDRF Team Lead", "Shillong", "East Khasi Hills", "Meghalaya"),
    ("analyst", "analyst123", "analyst", "DM Scientist", "Shillong", "East Khasi Hills", "Meghalaya"),
]

SOURCES = [
    ("rainfall_gauge", "Rainfall gauge network", "rainfall", 0.75, 12.0, 72.0,
     "prototype_fixture", "available", "Prototype fixture readings; no live IMD feed connected."),
    ("ml_local_model", "Local ML susceptibility model", "ml_prediction", 0.65, 24.0, 72.0,
     "synthetic_prototype", "available", "RandomForest trained on synthetic physically-informed data."),
    ("terrain_static", "Static terrain attributes", "terrain", 0.70, 8760.0, 43800.0,
     "fixture", "available", "Slope/lithology attributes are project fixtures, not field surveys."),
    ("citizen_report", "Citizen reports", "citizen_report", 0.55, 24.0, 72.0,
     "live", "available", "Live uploads from the mobile apps."),
    ("field_survey", "Field verification", "field_verification", 0.95, 168.0, 720.0,
     "live", "available", "Field observations submitted by volunteers/analysts."),
    ("ground_sensor", "Ground sensors", "sensor", 0.85, 6.0, 48.0,
     "prototype_fixture", "available", "Prototype fixture readings; no hardware deployed."),
    ("satellite_feed", "Satellite observations", "satellite", 0.80, 72.0, 240.0,
     "unavailable", "unavailable", "No satellite provider connected; always a missing input."),
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def seed_sources() -> None:
    for code, name, kind, w, hl, maxage, mode, status, notes in SOURCES:
        db.execute(
            """INSERT INTO data_sources (code, name, kind, source_weight, freshness_half_life_h,
                   max_age_h, data_mode, status, notes)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT (code) DO UPDATE SET source_weight=excluded.source_weight,
                   freshness_half_life_h=excluded.freshness_half_life_h,
                   max_age_h=excluded.max_age_h, status=excluded.status, notes=excluded.notes""",
            (code, name, kind, w, hl, maxage, mode, status, notes),
        )


def seed_users() -> None:
    for username, password, role, display, village, district, state in DEMO_USERS:
        if db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
            continue
        create_user(username, password, role, display,
                    village=village, district=district, state=state)


def seed_slopes(rng: random.Random) -> list[dict]:
    if db.query_one("SELECT id FROM slopes LIMIT 1"):
        return db.query("SELECT * FROM slopes")
    now = db.utcnow()
    slopes = []
    for cluster, district, state, lat, lng, n, base_elev, lithos, landuses in CLUSTERS:
        for i in range(n):
            plat = lat + rng.uniform(-0.06, 0.06)
            plng = lng + rng.uniform(-0.06, 0.06)
            angle = round(rng.uniform(18, 55), 1)
            code = f"{state[:2].upper()}-{len(slopes) + 1:03d}"
            row = dict(
                code=code,
                name=f"{cluster} slope {i + 1}",
                lat=round(plat, 6), lng=round(plng, 6),
                elevation_m=round(base_elev + rng.uniform(-220, 320), 1),
                slope_angle_deg=angle,
                aspect_deg=round(rng.uniform(0, 360), 1),
                curvature=round(rng.uniform(-1.2, 1.2), 2),
                area_km2=round(rng.uniform(0.4, 3.5), 2),
                lithology=rng.choice(lithos),
                land_use=rng.choice(landuses),
                road_cut=1 if rng.random() < 0.35 else 0,
                vegetation=round(rng.uniform(0.15, 0.92), 2),
                drainage_density=round(rng.uniform(0.2, 1.6), 2),
                district=district, state=state, cluster=cluster,
                historical_incidents=rng.choice([0, 0, 1, 1, 2, 3]),
                catchment_elev_m=round(base_elev + rng.uniform(150, 700), 1),
                notes=None,
                created_at=now, updated_at=now,
            )
            slope_id = db.execute(
                """INSERT INTO slopes (code, name, lat, lng, elevation_m, slope_angle_deg, aspect_deg,
                       curvature, area_km2, lithology, land_use, road_cut, vegetation, drainage_density,
                       district, state, cluster, historical_incidents, catchment_elev_m, notes,
                       created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(row.values()),
            )
            row["id"] = slope_id
            slopes.append(row)
    return slopes


def seed_rainfall(rng: random.Random) -> None:
    if db.query_one("SELECT id FROM rainfall_observations LIMIT 1"):
        return
    now = utcnow()
    for code, name, lat, lng, district, state in STATIONS:
        wet = rng.random()
        mm24 = round(rng.uniform(5, 175) * (0.4 + wet), 1)
        mm72 = round(mm24 * rng.uniform(1.6, 3.1) + rng.uniform(4, 60), 1)
        for offset_h, f in ((1, 1.0), (26, 0.75), (50, 0.6)):
            ts = (now - timedelta(hours=offset_h)).isoformat()
            db.execute(
                """INSERT INTO rainfall_observations (station_code, station_name, lat, lng, observed_at,
                       mm_24h, mm_72h, source_code, quality, data_mode, ingested_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (code, name, lat, lng, ts, round(mm24 * f, 1), round(mm72 * f, 1),
                 "rainfall_gauge", round(rng.uniform(0.85, 1.0), 2),
                 "prototype_fixture", now.isoformat()),
            )


def seed_sensors(rng: random.Random, slopes: list[dict]) -> None:
    if db.query_one("SELECT id FROM sensor_readings LIMIT 1"):
        return
    now = utcnow()
    kinds = [("inclinometer", "mm", 4, 34), ("piezometer", "m", 3, 22),
             ("crack_gauge", "mm", 2, 30), ("soil_moisture", "%", 18, 58)]
    for s in slopes:
        if rng.random() > 0.45:
            continue
        stype, unit, lo, hi = rng.choice(kinds)
        for k in range(rng.randint(1, 2)):
            db.execute(
                """INSERT INTO sensor_readings (slope_id, sensor_uid, sensor_type, observed_at, value,
                       unit, quality, data_mode, ingested_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (s["id"], f"{s['code']}-{stype[:3].upper()}", stype,
                 (now - timedelta(hours=1 + k * 5)).isoformat(),
                 round(rng.uniform(lo, hi), 2), unit,
                 round(rng.uniform(0.8, 1.0), 2), "prototype_fixture", now.isoformat()),
            )


def seed_satellite(slopes: list[dict]) -> None:
    if db.query_one("SELECT id FROM satellite_observations LIMIT 1"):
        return
    now = utcnow()
    for s in slopes:
        db.execute(
            """INSERT INTO satellite_observations (slope_id, observed_at, product, index_name,
                   index_value, provider, status, data_mode, detail, ingested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (s["id"], None, "SAR/InSAR displacement product", "los_displacement_mm", None, None,
             "unavailable", "unavailable",
             "no satellite provider configured in this build", now.isoformat()),
        )


def make_fixture_photo(path: Path, seed: int = 7) -> None:
    """Deterministic synthetic slope image for seeded fixture reports ONLY.

    Live reports uploaded through the API store the caller's real photograph.
    """
    from PIL import Image, ImageDraw

    rng = random.Random(seed)
    w, h = 800, 600
    img = Image.new("RGB", (w, h))
    dr = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        dr.line([(0, y), (w, y)], fill=(int(150 + 60 * t), int(175 + 40 * t), int(205 - 30 * t)))
    ridge = [0, int(h * 0.45)]
    pts = [(0, h)]
    x = 0
    while x <= w:
        pts.append((x, ridge[1] + int(35 * math.sin(x / 70) + rng.uniform(-8, 8))))
        x += 20
    pts.append((w, h))
    dr.polygon(pts, fill=(96, 110, 78))
    pts2 = [(0, h)]
    x = 0
    while x <= w:
        pts2.append((x, int(h * 0.72) + int(22 * math.sin(x / 55 + 1.4) + rng.uniform(-6, 6))))
        x += 20
    pts2.append((w, h))
    dr.polygon(pts2, fill=(122, 108, 84))
    dr.line([(int(w * 0.42), int(h * 0.52)), (int(w * 0.60), int(h * 0.70))],
            fill=(70, 55, 40), width=6)
    dr.line([(int(w * 0.46), int(h * 0.55)), (int(w * 0.63), int(h * 0.73))],
            fill=(158, 140, 112), width=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=86)


def seed_fixture_reports(slopes: list[dict]) -> None:
    if db.query_one("SELECT id FROM citizen_reports LIMIT 1"):
        return
    now = utcnow()
    upload_dir = config.UPLOAD_DIR
    photo = upload_dir / "fixture_report_0001.jpg"
    if not photo.exists():
        make_fixture_photo(photo)
    import hashlib

    photo_hash = hashlib.sha256(photo.read_bytes()).hexdigest()
    targets = [s for s in slopes if s["cluster"] in ("Cherrapunji Escarpment", "Aizawl Slopes",
                                                     "Dima Hasao Hills", "Kohima Ridge")]
    cats = [("crack", "Fresh crack near the road cut"), ("water_seepage", "Water seeping from slope face"),
            ("erosion", "Gully erosion below the trail"), ("rockfall", "Rocks fallen across the road")]
    for idx, s in enumerate(targets[:4]):
        lat = s["lat"] + 0.001
        lng = s["lng"] + 0.001
        db.execute(
            """INSERT INTO citizen_reports (report_code, slope_id, reporter_user, title, description,
                   category, observed_at, lat, lng, gps_source, photo_path, photo_hash, photo_origin,
                   severity_hint, status, cluster_id, is_duplicate, data_mode, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (f"RPT-FIX-{idx + 1:04d}", s["id"], None, cats[idx][1],
             "Fixture report seeded for the demonstration workflow (not a live submission).",
             cats[idx][0], (now - timedelta(hours=2 + idx)).isoformat(), lat, lng,
             "fallback_configured", str(photo.relative_to(config.UPLOAD_DIR)) if idx == 0 else None,
             photo_hash if idx == 0 else None,
             "generated_fixture" if idx == 0 else None,
             "moderate", "received", None, 0, "fixture", (now - timedelta(hours=2 + idx)).isoformat()),
        )
    # duplicate pair on the first slope (same spot, 40 minutes apart)
    first = targets[0]
    db.execute(
        """INSERT INTO citizen_reports (report_code, slope_id, reporter_user, title, description,
               category, observed_at, lat, lng, gps_source, photo_path, photo_hash, photo_origin,
               severity_hint, status, cluster_id, is_duplicate, data_mode, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("RPT-FIX-0005", first["id"], None, "Same crack reported again",
         "Fixture duplicate of RPT-FIX-0001 (dedup demo).", "crack",
         (now - timedelta(hours=1, minutes=20)).isoformat(),
         first["lat"] + 0.0012, first["lng"] + 0.0011, "fallback_configured",
         None, None, None, "moderate", "received", None, 0, "fixture",
         (now - timedelta(hours=1, minutes=20)).isoformat()),
    )
    db.execute("UPDATE citizen_reports SET is_duplicate = 1, duplicate_of = "
               "(SELECT id FROM citizen_reports WHERE report_code='RPT-FIX-0001') "
               "WHERE report_code = 'RPT-FIX-0005'")


def seed_fixture_field_observations(slopes: list[dict]) -> None:
    if db.query_one("SELECT id FROM field_observations LIMIT 1"):
        return
    now = utcnow()
    confirmed = next((s for s in slopes if s["cluster"] == "Aizawl Slopes"), slopes[0])
    inconcl = next((s for s in slopes if s["cluster"] == "Dima Hasao Hills"), slopes[-1])
    db.execute(
        """INSERT INTO field_observations (report_id, slope_id, verifier_user, verification_status,
               crack_mm, seepage, fresh_scarp, displacement_m, material, notes, photo_path,
               photo_origin, lat, lng, gps_source, observed_at, created_at)
           VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (confirmed["id"], None, "ground_truth", 42.0, 1, 1, 0.35, "weathered shale",
         "Fixture ground-truth: fresh scarp and open tension crack confirmed in field.",
         None, None, confirmed["lat"], confirmed["lng"], "device",
         (now - timedelta(hours=3)).isoformat(), now.isoformat()),
    )
    db.execute(
        """INSERT INTO field_observations (report_id, slope_id, verifier_user, verification_status,
               crack_mm, seepage, fresh_scarp, displacement_m, material, notes, photo_path,
               photo_origin, lat, lng, gps_source, observed_at, created_at)
           VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (inconcl["id"], None, "inconclusive", 4.0, 1, 0, 0.0, "schist",
         "Fixture: seepage observed, no fresh movement visible.",
         None, None, inconcl["lat"], inconcl["lng"], "device",
         (now - timedelta(hours=9)).isoformat(), now.isoformat()),
    )


def seed_all(with_fixtures: bool = True) -> dict:
    db.init_db()
    config.ensure_dirs()
    rng = random.Random(2026001)
    seed_sources()
    seed_users()
    slopes = seed_slopes(rng)
    seed_rainfall(rng)
    seed_sensors(rng, slopes)
    seed_satellite(slopes)
    if with_fixtures:
        seed_fixture_reports(slopes)
        seed_fixture_field_observations(slopes)
    from gis.graph_builder import build_edges

    edges = build_edges()
    return {"slopes": len(slopes), "edges": edges}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--assess", action="store_true", help="run the full assessment pipeline after seeding")
    args = parser.parse_args()
    stats = seed_all()
    print(f"seeded: {stats['slopes']} slopes, edges={stats['edges']}")
    if args.assess:
        from services.pipeline import run_all

        results = run_all()
        bands: dict[str, int] = {}
        for r in results:
            bands[r["band"]] = bands.get(r["band"], 0) + 1
        print(f"assessed {len(results)} slopes -> bands {bands}")
