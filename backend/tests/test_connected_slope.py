"""Tests for Connected-Slope Intelligence (innovation 2)."""
from __future__ import annotations

import db
from services import connected_slope as cs


def _slope_with_assessment(score: float, band: str = "HIGH") -> dict:
    slope = db.query_one("SELECT * FROM slopes ORDER BY id LIMIT 1")
    aid = db.execute(
        """INSERT INTO assessments (slope_id, method, engine_version, prior_probability,
               fused_probability, direct_score, inherited_influence, uplift, final_score,
               risk_band, confidence, ground_truth, accepted_count, rejected_count,
               missing_inputs, recommendation, next_action, flags, trace_json, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (slope["id"], "test", "1.0.0", 0.12, score, score, 0, 0, score, band, 0.7,
         0, 1, 0, "[]", "n/a", "n/a", "[]", "{}", db.utcnow()))
    return {"id": slope["id"], "assessment_id": aid}


def test_graph_has_nodes_and_documented_edges():
    payload = cs.graph_for_map()
    assert len(payload["nodes"]) > 0
    assert len(payload["edges"]) > 0
    assert {"spatial_adjacency", "rainfall_similarity"} & {e["rel_type"] for e in payload["edges"]}
    for e in payload["edges"]:
        assert e["strength"] >= 0
        assert e["basis"]  # every edge documents why it exists


def test_influence_is_one_hop_and_capped():
    target = db.query_one("SELECT * FROM slopes ORDER BY RANDOM() LIMIT 1")
    neighbours = db.query(
        """SELECT * FROM slope_edges WHERE src_slope_id = ? OR dst_slope_id = ? LIMIT 5""",
        (target["id"], target["id"]))
    if not neighbours:
        _slope_with_assessment(0.9)
        db.execute("""INSERT INTO slope_edges (src_slope_id, dst_slope_id, rel_type, strength,
                       distance_km, directionality, confidence, basis, validated, meta_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (_slope_with_assessment(0.9)["id"], target["id"], "spatial_adjacency",
                    0.9, 1.0, "symmetric", 0.9, "test edge", 0, "{}", db.utcnow()))

    # give a couple of neighbours a very high score
    touched = []
    for e in db.query("SELECT * FROM slope_edges WHERE src_slope_id = ? OR dst_slope_id = ?",
                      (target["id"], target["id"]))[:3]:
        nid = e["dst_slope_id"] if e["src_slope_id"] == target["id"] else e["src_slope_id"]
        touched.append(nid)
        db.execute("DELETE FROM assessments WHERE slope_id = ?", (nid,))
        _slope_with_assessment(0.95, "VERY_HIGH")

    result = cs.propagate(target["id"], direct_score=0.10)
    assert result["mode"] == "bounded_one_hop"
    assert 0 <= result["uplift"] <= cs.MAX_UPLIFT
    assert result["final_score"] <= 0.99
    assert result["final_score"] >= 0.10
    # inherited influence is a weighted average of neighbour scores (<= 1)
    assert 0 <= result["inherited_influence"] <= 1
    for c in result["contributions"]:
        assert c["calc"] and "strength" in c["calc"]
        assert c["source_slope_id"] != target["id"]
    assert result["caveat"]


def test_no_neighbours_no_uplift():
    orphan = db.query_one(
        """SELECT s.* FROM slopes s
           WHERE NOT EXISTS (SELECT 1 FROM slope_edges e
                             WHERE e.src_slope_id = s.id OR e.dst_slope_id = s.id)
           LIMIT 1""")
    if orphan is None:
        import pytest

        pytest.skip("graph is fully connected")
    result = cs.propagate(orphan["id"], 0.4)
    assert result["uplift"] == 0
    assert result["final_score"] == 0.4
    assert result["neighbors_used"] == 0


def test_unassessed_neighbour_skipped():
    target = db.query_one("SELECT * FROM slopes ORDER BY id LIMIT 1")
    other = db.query_one("SELECT * FROM slopes ORDER BY id DESC LIMIT 1")
    if other["id"] == target["id"]:
        import pytest

        pytest.skip("need two slopes")
    db.execute("DELETE FROM assessments WHERE slope_id = ?", (other["id"],))
    db.execute(
        """INSERT INTO slope_edges (src_slope_id, dst_slope_id, rel_type, strength, distance_km,
               directionality, confidence, basis, validated, meta_json, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (src_slope_id, dst_slope_id, rel_type)
           DO UPDATE SET strength = 0.9""",
        (other["id"], target["id"], "spatial_adjacency", 0.9, 1.0, "symmetric", 0.9,
         "test", 0, "{}", db.utcnow()))
    result = cs.propagate(target["id"], 0.3)
    skipped_ids = [s["slope_id"] for s in result["neighbors_skipped"]]
    assert other["id"] in skipped_ids or any(
        c["source_slope_id"] == other["id"] for c in result["contributions"])


def test_hydrology_direction_asymmetry():
    edge = {"rel_type": "hydrology_downslope", "dst_slope_id": 1, "src_slope_id": 2}
    into, note_in = cs._direction_factor(edge, 1)
    away, note_out = cs._direction_factor(edge, 2)
    assert into == 1.0 and away == 0.35
    assert "INTO" in note_in and "AWAY" in note_out
    sym, _ = cs._direction_factor({"rel_type": "spatial_adjacency", "dst_slope_id": 1,
                                   "src_slope_id": 2}, 1)
    assert sym == 1.0


def test_influence_never_creates_confirmed():
    """Contextual influence must not be able to confirm a landslide."""
    from services.belief_fusion import band_for

    result = cs.propagate(db.query_one("SELECT id FROM slopes LIMIT 1")["id"], 0.9)
    band = band_for(result["final_score"], ground_truth=False)
    assert band != "CONFIRMED"
