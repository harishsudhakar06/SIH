"""BhuRaksha Flask application factory and entry point.

Run (after seeding):
    python backend/app.py
Bind address comes from BHURAKSHA_HOST / BHURAKSHA_PORT (default 0.0.0.0:5000 so
phones on the same Wi-Fi can reach the laptop).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import REPO_ROOT, config, detect_lan_ip  # noqa: E402
import db  # noqa: E402

MOBILE_APPS = (
    ("citizen", "Citizen", "submit reports with photo + GPS"),
    ("field", "Field verification", "verify slopes and close tasks"),
    ("community", "Community leader", "village risk board and warnings"),
    ("response", "Emergency response", "acknowledge, dispatch, resolve"),
    ("analyst", "Analyst", "run fusion, inspect traces and model"),
)


def create_app(auto_bootstrap: bool = False) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["JSON_SORT_KEYS"] = False
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES + 2 * 1024 * 1024

    origins = config.CORS_ORIGINS
    CORS(
        app,
        resources={r"/api/*": {"origins": origins}},
        allow_headers=["Content-Type", "Authorization"],
        methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    )

    from api import (alerts_api, assessments_api, auth_api, command_api, data_api,
                     geo_api, incidents_api, map_api, reports_api, sensors_api,
                     slopes_api, system_api, tiles_api)

    for module in (auth_api, slopes_api, reports_api, assessments_api, alerts_api,
                   incidents_api, data_api, system_api, tiles_api, geo_api,
                   map_api, command_api, sensors_api):
        app.register_blueprint(module.bp)

    @app.get("/")
    def index():
        index_file = REPO_ROOT / "frontend" / "dist" / "index.html"
        if index_file.exists():
            return send_from_directory(index_file.parent, index_file.name)
        return jsonify({
            "app": "BhuRaksha",
            "problem_statement": "SIH26001 - Connected Slope Intelligence",
            "engine_version": config.ENGINE_VERSION,
            "api": "/api",
            "hint": "Frontend not built yet - run: cd frontend && npm install && npm run build",
            "mobile_apps": MOBILE_APPS,
            "endpoints": [
                "/api/auth/login", "/api/auth/register", "/api/auth/me",
                "/api/slopes", "/api/graph", "/api/dashboard",
                "/api/reports", "/api/assessments/run", "/api/alerts",
                "/api/incidents", "/api/rainfall", "/api/sensors", "/api/satellite",
                "/api/data-quality", "/api/ml/model-info", "/api/tiles/<z>/<x>/<y>.png",
            ],
        })

    @app.get("/assets/<path:filename>")
    def frontend_assets(filename: str):
        return send_from_directory(REPO_ROOT / "frontend" / "dist" / "assets", filename)

    # ---------------------------------------------------------------- mobile apps
    # Five standalone applications served next to the API so a phone only needs
    # http://<LAPTOP_LAN_IP>:5000/app/<name>.html
    @app.get("/app")
    @app.get("/app/")
    def mobile_index():
        links = "".join(
            f'<li><a href="/app/{name}.html">{label}</a> — {blurb}</li>'
            for name, label, blurb in MOBILE_APPS
        )
        return (
            '<!doctype html><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>BhuRaksha mobile apps</title>'
            '<body style="font-family:system-ui;background:#0d1117;color:#e6edf3;padding:24px">'
            '<h1>BhuRaksha — five mobile apps</h1>'
            f'<p style="color:#8b98a9">SIH26001 Connected Slope Intelligence. Open on a phone connected to the '
            f'same Wi-Fi as this laptop ({detect_lan_ip()}:{config.PORT}).</p>'
            f'<ul style="line-height:2">{links}</ul>'
            '<p style="color:#8b98a9">Command center: <a style="color:#2f81f7" href="/">/</a></p>'
            '</body>'
        )

    @app.get("/app/<path:filename>")
    def mobile_file(filename: str):
        dist = REPO_ROOT / "mobile" / "dist"
        if filename.startswith("assets/"):
            return send_from_directory(dist / "assets", filename[len("assets/"):])
        name = filename[:-5] if filename.endswith(".html") else filename
        allowed = {m[0] for m in MOBILE_APPS}
        if name in allowed and filename.endswith(".html"):
            target = dist / f"{name}.html"
            if target.exists():
                return send_from_directory(dist, target.name)
        return jsonify({"error": "not_found", "detail": "mobile app not built or unknown name"}), 404

    # ------------------------------------------------- command-center SPA fallback
    @app.get("/<path:path>")
    def spa_fallback(path: str):
        if path.startswith(("api/", "app/", "uploads/", "assets/")):
            return jsonify({"error": "not_found", "detail": f"unknown endpoint: {path}"}), 404
        dist = REPO_ROOT / "frontend" / "dist"
        direct = dist / path
        if direct.is_file():
            return send_from_directory(direct.parent, direct.name)
        if (dist / "index.html").exists():
            return send_from_directory(dist, "index.html")
        return jsonify({"error": "not_found", "detail": "frontend not built"}), 404

    @app.get("/uploads/<path:filename>")
    def uploads(filename: str):
        return send_from_directory(config.UPLOAD_DIR, filename)

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify({"error": "not_found", "detail": "unknown endpoint"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify({"error": "method_not_allowed"}), 405

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"error": "payload_too_large", "detail": "image larger than 8 MB"}), 413

    @app.errorhandler(500)
    def server_error(_e):
        return jsonify({"error": "internal_error"}), 500

    @app.teardown_request
    def _teardown(_exc):
        db.close_conn()

    if auto_bootstrap:
        bootstrap()

    return app


def bootstrap() -> None:
    """Ensure the schema exists; seed fixtures when the database is new."""
    config.ensure_dirs()
    fresh = not db.is_db_initialised()
    db.init_db()
    slopes = db.query_one("SELECT COUNT(*) AS n FROM slopes")
    if slopes["n"] == 0:
        print("[bhuraksha] empty database detected - seeding fixture data ...")
        from seed import seed_all

        stats = seed_all()
        print(f"[bhuraksha] seeded {stats['slopes']} slopes, edges={stats['edges']}")
    if not (config.ML_MODEL_DIR / "latest.joblib").exists():
        print("[bhuraksha] no local model found - training the prototype RF ...")
        try:
            from ml.train import train

            train()
        except Exception as exc:  # pragma: no cover - never block startup on ML
            print(f"[bhuraksha] model training skipped: {exc}")
    assessments = db.query_one("SELECT COUNT(*) AS n FROM assessments")
    if assessments["n"] == 0:
        print("[bhuraksha] running first assessment pass ...")
        from services.pipeline import run_all

        results = run_all()
        bands: dict[str, int] = {}
        for r in results:
            bands[r["band"]] = bands.get(r["band"], 0) + 1
        print(f"[bhuraksha] assessed {len(results)} slopes -> {bands}")
    if fresh:
        print(f"[bhuraksha] database ready at {config.DB_PATH}")

def _wanted_host() -> str:
    return os.environ.get("BHURAKSHA_HOST", config.HOST)


if __name__ == "__main__":
    application = create_app(auto_bootstrap=True)
    host = _wanted_host()
    print(f"[bhuraksha] serving on http://{host}:{config.PORT} (LAN IP for phones: see /api/system/info)")
    application.run(host=host, port=config.PORT, debug=config.DEBUG, threaded=True)
else:  # gunicorn / WSGI import path
    application = create_app(auto_bootstrap=False)
