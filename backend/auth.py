"""Authentication, sessions and role-based access control."""
from __future__ import annotations

import functools
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from flask import g, jsonify, request

import db
from config import config

ROLES = ("citizen", "field_volunteer", "community_leader", "response_operator", "analyst", "admin")

ROLE_RANK = {
    "citizen": 0,
    "field_volunteer": 1,
    "community_leader": 2,
    "response_operator": 3,
    "analyst": 3,
    "admin": 9,
}

# Every sensitive endpoint maps to the minimum roles allowed to call it.
PERMISSIONS: dict[str, tuple[str, ...]] = {
    "reports:create": ("citizen", "field_volunteer", "community_leader", "response_operator", "analyst", "admin"),
    "reports:verify": ("field_volunteer", "analyst", "admin"),
    "reports:view_all": ("field_volunteer", "community_leader", "response_operator", "analyst", "admin"),
    "alerts:ack": ("community_leader", "response_operator", "analyst", "admin"),
    "alerts:resolve": ("response_operator", "analyst", "admin"),
    "alerts:notify": ("community_leader", "response_operator", "analyst", "admin"),
    "command:view": ("community_leader", "response_operator", "analyst", "admin"),
    "technical:view": ("field_volunteer", "analyst", "admin"),
    "sensors:manage": ("analyst", "admin"),
    "incidents:create": ("citizen", "field_volunteer", "community_leader", "response_operator", "analyst", "admin"),
    "incidents:transition": ("community_leader", "response_operator", "analyst", "admin"),
    "dispatch:create": ("response_operator", "admin"),
    "assessments:run": ("analyst", "admin"),
    "ml:train": ("analyst", "admin"),
    "users:manage": ("admin",),
    "audit:view": ("analyst", "admin"),
}


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return hmac.compare_digest(candidate.hex(), digest)


def create_user(username: str, password: str, role: str, display_name: str, **extra: Any) -> int:
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    return db.execute(
        """INSERT INTO users (username, password_hash, display_name, role, phone, village, district, state,
                              lat, lng, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            username,
            hash_password(password),
            display_name,
            role,
            extra.get("phone"),
            extra.get("village"),
            extra.get("district"),
            extra.get("state"),
            extra.get("lat"),
            extra.get("lng"),
            db.utcnow(),
        ),
    )


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    user = db.query_one("SELECT * FROM users WHERE username = ? AND active = 1", (username,))
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user


def issue_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=config.TOKEN_TTL_HOURS)
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
        (token, user_id, db.utcnow(), expires.isoformat()),
    )
    return token


def revoke_token(token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def load_user_from_token(token: str) -> dict[str, Any] | None:
    row = db.query_one(
        """SELECT u.*, s.expires_at AS session_expires
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND u.active = 1""",
        (token,),
    )
    if not row:
        return None
    expires = datetime.fromisoformat(row["session_expires"])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        revoke_token(token)
        return None
    return row


def current_user() -> dict[str, Any] | None:
    if hasattr(g, "user"):
        return g.user
    header = request.headers.get("Authorization", "")
    token = header[7:].strip() if header.startswith("Bearer ") else request.args.get("token", "")
    user = load_user_from_token(token) if token else None
    g.user = user
    return user


def permission_error(permission: str, status: int = 403):
    return jsonify({"error": "forbidden", "detail": f"permission '{permission}' required"}), status


def require(permission: str) -> Callable:
    """Decorator enforcing an entry from PERMISSIONS."""

    def wrapper(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def inner(*args: Any, **kwargs: Any):
            user = current_user()
            if not user:
                return jsonify({"error": "unauthorized", "detail": "missing or invalid token"}), 401
            allowed = PERMISSIONS.get(permission, ())
            if user["role"] not in allowed:
                return permission_error(permission)
            g.permission = permission
            return fn(*args, **kwargs)

        return inner

    return wrapper


def require_roles(*roles: str) -> Callable:
    def wrapper(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def inner(*args: Any, **kwargs: Any):
            user = current_user()
            if not user:
                return jsonify({"error": "unauthorized", "detail": "missing or invalid token"}), 401
            if user["role"] not in roles and user["role"] != "admin":
                return jsonify({"error": "forbidden", "detail": f"roles {roles} required"}), 403
            return fn(*args, **kwargs)

        return inner

    return wrapper


def audit(user_id: int | None, action: str, entity: str | None = None, entity_id: int | None = None, detail: Any = None) -> None:
    import json

    db.execute(
        "INSERT INTO audit_log (user_id, action, entity, entity_id, detail_json, created_at) VALUES (?,?,?,?,?,?)",
        (user_id, action, entity, entity_id, json.dumps(detail, default=str) if detail is not None else None, db.utcnow()),
    )
