"""Tests for controlled risk transitions and duplicate-alert prevention."""
from __future__ import annotations

from services.risk_state import dedupe_key, transition_allowed


def test_initial_and_unchanged_allowed():
    assert transition_allowed(None, "HIGH", 0.5)[0] is True
    assert transition_allowed("HIGH", "HIGH", 0.5)[0] is True


def test_deescalation_allowed():
    ok, reason = transition_allowed("VERY_HIGH", "LOW", 0.2)
    assert ok and "de-escalation" in reason


def test_single_step_escalation_allowed():
    assert transition_allowed("LOW", "MODERATE", 0.3)[0] is True
    assert transition_allowed("MODERATE", "HIGH", 0.3)[0] is True


def test_multi_step_blocked_at_low_confidence():
    ok, reason = transition_allowed("LOW", "VERY_HIGH", 0.4)
    assert not ok
    assert "blocked" in reason


def test_multi_step_allowed_with_high_confidence():
    ok, reason = transition_allowed("LOW", "VERY_HIGH", 0.8, flags=[])
    assert ok
    ok2, _ = transition_allowed("LOW", "VERY_HIGH", 0.8, flags=["insufficient_evidence"])
    assert not ok2


def test_confirmed_requires_ground_truth():
    ok, reason = transition_allowed("VERY_HIGH", "CONFIRMED", 0.9)
    assert not ok
    assert "ground-truth" in reason
    ok2, reason2 = transition_allowed("VERY_HIGH", "CONFIRMED", 0.9, ground_truth=True)
    assert ok2 and "ground-truth" in reason2


def test_dedupe_key_is_stable_within_the_hour():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 9, 12, 34, 56, tzinfo=timezone.utc)
    a = dedupe_key(7, "HIGH", now)
    b = dedupe_key(7, "HIGH", now.replace(minute=10))
    c = dedupe_key(7, "VERY_HIGH", now)
    d = dedupe_key(8, "HIGH", now)
    assert a == b  # same slope+band+hour collapses to one key
    assert a != c and a != d
