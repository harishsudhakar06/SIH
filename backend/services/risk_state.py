"""Controlled risk transitions and duplicate-alert prevention.

Risk bands only move through allowed transitions so a single noisy reading
cannot bounce a slope straight from LOW to CONFIRMED:

  * de-escalation is always permitted (and logged),
  * one-step escalation is permitted,
  * multi-step escalation requires high confidence and no weak-evidence flags,
  * CONFIRMED is reachable ONLY through a ground-truth field observation.

Duplicate alerts: the alert table carries a UNIQUE dedupe_key of
`{slope_id}|{band}|{hour-bucket}`. Re-running the pipeline inside the same hour
hits the unique constraint and the alert is suppressed instead of duplicated.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import db

ORDER = ["LOW", "MODERATE", "HIGH", "VERY_HIGH", "CONFIRMED"]
ALERTABLE = {"HIGH", "VERY_HIGH", "CONFIRMED"}


def transition_allowed(from_band: str | None, to_band: str, confidence: float,
                       flags: list[str] | None = None, ground_truth: bool = False) -> tuple[bool, str]:
    flags = flags or []
    if from_band is None:
        return True, "initial assessment"
    if from_band == to_band:
        return True, "unchanged"
    if to_band == "CONFIRMED" and not ground_truth:
        return False, "CONFIRMED requires a ground-truth field observation on this slope"
    if ground_truth:
        return True, "ground-truth confirmation permits any transition"

    fi, ti = ORDER.index(from_band), ORDER.index(to_band)
    if ti < fi:
        return True, "de-escalation permitted"
    steps = ti - fi
    if steps == 1:
        return True, "single-step escalation permitted"
    if confidence >= 0.65 and "insufficient_evidence" not in flags and "zero_total_weight" not in flags:
        return True, f"multi-step escalation allowed (confidence {confidence:.2f} >= 0.65)"
    return False, (f"multi-step escalation blocked at confidence {confidence:.2f} "
                   f"with flags {flags}; re-verification required")


def record_history(slope_id: int, band: str, probability: float, assessment_id: int | None,
                   from_band: str | None, allowed: bool) -> int:
    return db.execute(
        """INSERT INTO slope_risk_history (slope_id, risk_band, probability, assessment_id,
                                           from_band, allowed, changed_at)
           VALUES (?,?,?,?,?,?,?)""",
        (slope_id, band, probability, assessment_id, from_band, int(allowed), db.utcnow()),
    )


def current_band(slope_id: int) -> str | None:
    row = db.query_one(
        "SELECT risk_band FROM slope_risk_history WHERE slope_id = ? ORDER BY id DESC LIMIT 1",
        (slope_id,),
    )
    return row["risk_band"] if row else None


def dedupe_key(slope_id: int, band: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    bucket = now.replace(minute=0, second=0, microsecond=0).isoformat()
    return f"{slope_id}|{band}|{bucket}"


def open_alert_for(slope_id: int) -> dict[str, Any] | None:
    return db.query_one(
        """SELECT * FROM alerts
           WHERE slope_id = ? AND status IN ('open','acknowledged','dispatched')
           ORDER BY id DESC LIMIT 1""",
        (slope_id,),
    )


def create_alert(slope_id: int, assessment_id: int, band: str, title: str, message: str,
                 district: str | None, state: str | None, now: datetime | None = None
                 ) -> tuple[dict[str, Any] | None, bool]:
    """Insert an alert with dedupe protection.

    Returns (alert_row, created). `created=False` means a duplicate was prevented.
    """
    import sqlite3

    key = dedupe_key(slope_id, band, now)
    severity = {"HIGH": "high", "VERY_HIGH": "very_high", "CONFIRMED": "confirmed"}[band]
    ts = db.utcnow()
    try:
        alert_id = db.execute(
            """INSERT INTO alerts (alert_code, slope_id, assessment_id, severity, band, title, message,
                                   dedupe_key, status, scope_state, scope_district, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?, 'open', ?,?,?,?)""",
            (f"ALT-{ts[:10].replace('-', '')}-{assessment_id:05d}", slope_id, assessment_id,
             severity, band, title, message, key, state, district, ts, ts),
        )
    except sqlite3.IntegrityError:
        existing = db.query_one("SELECT * FROM alerts WHERE dedupe_key = ?", (key,))
        return existing, False

    db.execute(
        "INSERT INTO alert_events (alert_id, user_id, action, note, created_at) VALUES (?,?,?,?,?)",
        (alert_id, None, "created", "raised by belief fusion pipeline", ts),
    )
    _fan_out_notifications(alert_id, slope_id, band, title, message, district, state, ts)
    return db.query_one("SELECT * FROM alerts WHERE id = ?", (alert_id,)), True


def _fan_out_notifications(alert_id: int, slope_id: int, band: str, title: str, message: str,
                           district: str | None, state: str | None, ts: str) -> None:
    """Persist notifications for the roles that act on an alert."""
    scopes = [
        {"role_scope": "response_operator", "district": None},
        {"role_scope": "community_leader", "district": district},
        {"role_scope": "field_volunteer", "district": district},
        {"role_scope": "analyst", "district": None},
    ]
    for scope in scopes:
        db.execute(
            """INSERT INTO notifications (user_id, role_scope, district, title, body, severity,
                                          related_type, related_id, created_at)
               VALUES (NULL,?,?,?,?,?,?,?,?)""",
            (scope["role_scope"], scope["district"], title, message,
             "critical" if band in ("VERY_HIGH", "CONFIRMED") else "warning",
             "alert", alert_id, ts),
        )
