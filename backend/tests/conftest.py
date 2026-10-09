"""Shared pytest fixtures: isolated temp database, seeded fixtures, API client."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_TMP = Path(tempfile.mkdtemp(prefix="bhuraksha_test_"))
os.environ.setdefault("BHURAKSHA_DB_PATH", str(_TMP / "test.db"))
os.environ.setdefault("BHURAKSHA_UPLOAD_DIR", str(_TMP / "uploads"))
os.environ.setdefault("BHURAKSHA_TILE_CACHE_DIR", str(_TMP / "tiles"))
os.environ.setdefault("BHURAKSHA_TILE_OFFLINE", "1")
os.environ.setdefault("BHURAKSHA_SECRET_KEY", "test-secret")

import db  # noqa: E402
from config import config  # noqa: E402

config.ensure_dirs()
db.init_db()

import seed as seed_module  # noqa: E402

if not db.query_one("SELECT id FROM slopes LIMIT 1"):
    seed_module.seed_all()

FIXTURE_PHOTO = _TMP / "test_photo.jpg"
if not FIXTURE_PHOTO.exists():
    seed_module.make_fixture_photo(FIXTURE_PHOTO, seed=3)


@pytest.fixture(scope="session")
def app():
    import app as app_module

    application = app_module.create_app(auto_bootstrap=False)
    application.config["TESTING"] = True
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(scope="session")
def tokens(app):
    """One login token per role (seeded demo accounts)."""
    from auth import authenticate, issue_token

    out = {}
    for username in ("admin", "citizen", "volunteer", "leader", "response", "analyst"):
        user = authenticate(username, f"{username}123" if username != "admin" else "admin123")
        assert user, f"demo user {username} missing"
        out[username] = issue_token(user["id"])
    return out


@pytest.fixture()
def auth_headers(tokens):
    def make(role: str) -> dict:
        return {"Authorization": f"Bearer {tokens[role]}"}

    return make


@pytest.fixture()
def any_slope():
    return db.query_one("SELECT * FROM slopes ORDER BY RANDOM() LIMIT 1")


@pytest.fixture()
def fixture_photo() -> Path:
    return FIXTURE_PHOTO
