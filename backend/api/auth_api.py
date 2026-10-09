"""Auth endpoints: register, login, logout, current user, user management."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import auth as auth_mod
import db
from api.common import err, ok

bp = Blueprint("auth_api", __name__, url_prefix="/api/auth")

SAFE_FIELDS = ("id", "username", "display_name", "role", "phone", "village",
               "district", "state", "lat", "lng", "active", "created_at")


def _public(row: dict) -> dict:
    return {k: row.get(k) for k in SAFE_FIELDS}


@bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    display = (data.get("display_name") or username).strip()
    role = data.get("role", "citizen")
    if not username or len(password) < 6:
        return err("validation", 400, "username and a 6+ character password are required")
    if role != "citizen":
        # privileged roles must be created by an admin
        user = auth_mod.current_user()
        if not user or user["role"] != "admin":
            return err("forbidden", 403, "only admins may create non-citizen accounts")
    if db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
        return err("conflict", 409, "username already taken")
    user_id = auth_mod.create_user(
        username, password, role, display,
        phone=data.get("phone"), village=data.get("village"), district=data.get("district"),
        state=data.get("state"), lat=data.get("lat"), lng=data.get("lng"),
    )
    token = auth_mod.issue_token(user_id)
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    auth_mod.audit(user_id, "user.register", "users", user_id, {"role": role})
    return ok({"token": token, "user": _public(user)}, 201)


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    user = auth_mod.authenticate((data.get("username") or "").strip(), data.get("password") or "")
    if not user:
        return err("unauthorized", 401, "invalid username or password")
    token = auth_mod.issue_token(user["id"])
    auth_mod.audit(user["id"], "user.login", "users", user["id"], None)
    return ok({"token": token, "user": _public(user)})


@bp.post("/logout")
def logout():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        auth_mod.revoke_token(header[7:].strip())
    return ok({"status": "logged_out"})


@bp.get("/me")
def me():
    user = auth_mod.current_user()
    if not user:
        return err("unauthorized", 401, "not logged in")
    unread = db.query_one(
        """SELECT COUNT(*) AS n FROM notifications
           WHERE read = 0 AND (user_id = ? OR (user_id IS NULL AND role_scope = ?))""",
        (user["id"], user["role"]),
    )
    payload = _public(user)
    payload["unread_notifications"] = unread["n"] if unread else 0
    payload["permissions"] = [
        p for p, roles in auth_mod.PERMISSIONS.items() if user["role"] in roles
    ]
    return ok({"user": payload})


@bp.get("/users")
@auth_mod.require("users:manage")
def list_users():
    return ok({"users": [_public(u) for u in db.query("SELECT * FROM users ORDER BY id")]})
