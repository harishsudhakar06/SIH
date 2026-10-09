"""Shared API helpers."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from flask import jsonify, request


def err(message: str, status: int, detail: str | None = None, **extra: Any):
    payload = {"error": message}
    if detail:
        payload["detail"] = detail
    payload.update(extra)
    return jsonify(payload), status


def ok(payload: dict[str, Any], status: int = 200):
    return jsonify(payload), status


# ----------------------------------------------------------------- rate limiting
# In-memory sliding-window limiter (single-process prototype). Applied to login
# and report submission so scripted abuse cannot flood the demo database.
_BUCKETS: dict[str, list[float]] = {}


def rate_limit(name: str, limit: int, window_s: int = 60):
    def decorator(fn):
        import functools

        @functools.wraps(fn)
        def inner(*args, **kwargs):
            ident = request.headers.get("Authorization", "")[:40] or (request.remote_addr or "?")
            key = f"{name}:{ident}"
            now = time.monotonic()
            hits = [t for t in _BUCKETS.get(key, []) if now - t < window_s]
            if len(hits) >= limit:
                return err("rate_limited", 429,
                           f"too many requests: {limit} per {window_s}s for '{name}'")
            hits.append(now)
            _BUCKETS[key] = hits
            return fn(*args, **kwargs)

        return inner

    return decorator


def parse_since(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def int_arg(name: str, default: int, lo: int = 1, hi: int = 500) -> int:
    from flask import request

    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))
