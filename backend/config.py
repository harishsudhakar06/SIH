"""Central configuration for the BhuRaksha backend.

All values can be overridden with environment variables (see .env.example at the
repository root). Nothing here reaches the network; the only network-facing
decisions are HOST/PORT and CORS origins.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_flag(name: str, default: int) -> bool:
    return _env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    ENGINE_VERSION = "1.0.0"

    HOST: str = _env("BHURAKSHA_HOST", "0.0.0.0")
    PORT: int = int(_env("BHURAKSHA_PORT", "5000"))
    DEBUG: bool = _env_flag("BHURAKSHA_DEBUG", 0)

    SECRET_KEY: str = _env("BHURAKSHA_SECRET_KEY", "bhuraksha-dev-secret-do-not-use-in-production")
    TOKEN_TTL_HOURS: int = int(_env("BHURAKSHA_TOKEN_TTL_HOURS", "72"))

    DB_PATH: Path = Path(_env("BHURAKSHA_DB_PATH", str(REPO_ROOT / "backend" / "data" / "bhuraksha.db")))
    UPLOAD_DIR: Path = Path(_env("BHURAKSHA_UPLOAD_DIR", str(REPO_ROOT / "backend" / "data" / "uploads")))
    TILE_CACHE_DIR: Path = Path(_env("BHURAKSHA_TILE_CACHE_DIR", str(REPO_ROOT / "backend" / "data" / "tiles" / "cache")))

    CORS_ORIGINS: str = _env("BHURAKSHA_CORS_ORIGINS", "*")
    TILE_OFFLINE: bool = _env_flag("BHURAKSHA_TILE_OFFLINE", 0)

    # Optional HTTPS for phones that require a secure context (see DEMO_GUIDE.md)
    SSL_CERT: str | None = _env("BHURAKSHA_SSL_CERT", "") or None
    SSL_KEY: str | None = _env("BHURAKSHA_SSL_KEY", "") or None

    ML_MODEL_DIR: Path = BASE_DIR / "ml" / "models"
    MAX_UPLOAD_BYTES: int = 8 * 1024 * 1024
    MAX_VIDEO_BYTES: int = 24 * 1024 * 1024
    MAX_REQUESTS_PER_MINUTE: int = 120
    REPORTS_PER_MINUTE: int = 20
    LOGIN_ATTEMPTS_PER_MINUTE: int = 15

    # Geographic focus: Northeast India
    REGION_BBOX = {"min_lat": 21.5, "max_lat": 29.2, "min_lon": 89.5, "max_lon": 97.6}

    def ensure_dirs(self) -> None:
        for path in (self.DB_PATH, self.UPLOAD_DIR, self.TILE_CACHE_DIR, self.ML_MODEL_DIR):
            parent = path if path.suffix == "" else path.parent
            parent.mkdir(parents=True, exist_ok=True)
        (self.UPLOAD_DIR / ".gitkeep").touch()
        (self.TILE_CACHE_DIR / ".gitkeep").touch()


config = Config()


def detect_lan_ip() -> str:
    """Best-effort detection of the laptop LAN IP address (no external traffic)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"
