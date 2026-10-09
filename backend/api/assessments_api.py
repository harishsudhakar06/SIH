"""Assessment endpoints: run the pipeline, read scores and the full audit trace."""
from __future__ import annotations

import json

from flask import Blueprint, request

import auth as auth_mod
import db
from api.common import err, int_arg, ok, parse_since

bp = Blueprint("assessments_api", __name__, url_prefix="/api")


def _parse_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


@bp.post("/assessments/run")
@auth_mod.require("assessments:run")
def run():
    data = request.get_json(silent=True) or {}
    user = auth_mod.current_user()
    from services.pipeline import run_all, run_assessment

    slope_id = data.get("slope_id")
    if slope_id:
        try:
            result = run_assessment(int(slope_id), actor_id=user["id"])
        except ValueError as exc:
            return err("validation", 400, str(exc))
        return ok({"results": [result], "count": 1})

    results = run_all()
    summary: dict[str, int] = {}
    for r in results:
        summary[r["band"]] = summary.get(r["band"], 0) + 1
    auth_mod.audit(user["id"], "assessment.run_all", "assessments", None,
                   {"count": len(results), "bands": summary})
    return ok({
        "results": [{k: v for k, v in r.items() if k != "trace"} for r in results],
        "count": len(results),
        "bands": summary,
    })


@bp.get("/assessments")
def list_assessments():
    slope_id = request.args.get("slope_id")
    since = parse_since(request.args.get("since"))
    limit = int_arg("limit", 50)
    sql = """SELECT a.id, a.slope_id, a.method, a.engine_version, a.fused_probability,
                    a.direct_score, a.inherited_influence, a.uplift, a.final_score, a.risk_band,
                    a.confidence, a.ground_truth, a.accepted_count, a.rejected_count,
                    a.missing_inputs, a.recommendation, a.next_action, a.flags, a.created_at,
                    s.code AS slope_code, s.name AS slope_name, s.district, s.state
             FROM assessments a JOIN slopes s ON s.id = a.slope_id WHERE 1 = 1"""
    params: list = []
    if slope_id:
        sql += " AND a.slope_id = ?"
        params.append(int(slope_id))
    if since:
        sql += " AND a.created_at >= ?"
        params.append(since)
    sql += " ORDER BY a.id DESC LIMIT ?"
    params.append(limit)
    rows = db.query(sql, tuple(params))
    for r in rows:
        r["missing"] = _parse_list(r.get("missing_inputs"))
        r["flag_list"] = _parse_list(r.get("flags"))
    return ok({"assessments": rows, "count": len(rows)})


@bp.get("/assessments/<int:assessment_id>")
def assessment_detail(assessment_id: int):
    row = db.query_one(
        """SELECT a.*, s.code AS slope_code, s.name AS slope_name
           FROM assessments a JOIN slopes s ON s.id = a.slope_id WHERE a.id = ?""",
        (assessment_id,))
    if not row:
        return err("not_found", 404, "assessment not found")
    evidence = db.query(
        "SELECT * FROM assessment_evidence WHERE assessment_id = ? ORDER BY id", (assessment_id,))
    return ok({"assessment": row, "evidence": evidence,
               "missing": _parse_list(row.get("missing_inputs")),
               "flags": _parse_list(row.get("flags"))})


@bp.get("/assessments/<int:assessment_id>/trace")
def assessment_trace(assessment_id: int):
    row = db.query_one("SELECT trace_json FROM assessments WHERE id = ?", (assessment_id,))
    if not row:
        return err("not_found", 404, "assessment not found")
    if not row["trace_json"]:
        return err("not_found", 404, "trace not stored for this assessment")
    return ok(json.loads(row["trace_json"]))


@bp.get("/evidence")
def evidence_feed():
    """Most recent evidence rows across assessments (command-center feed)."""
    limit = int_arg("limit", 100)
    rows = db.query(
        """SELECT e.*, a.slope_id, s.code AS slope_code, s.name AS slope_name, a.created_at
           FROM assessment_evidence e
           JOIN assessments a ON a.id = e.assessment_id
           JOIN slopes s ON s.id = a.slope_id
           ORDER BY e.id DESC LIMIT ?""",
        (limit,))
    return ok({"evidence": rows, "count": len(rows)})
