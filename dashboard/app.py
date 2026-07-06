"""Optional local Flask dashboard.

Run with:
    python dashboard/app.py
Then open:
    http://localhost:5000
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

# Allow running this file directly from dashboard/ while importing project modules.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, Response, redirect, render_template, request, url_for

from database.db import init_db, list_internships, update_internship_status
from utils.source_store import add_source, load_sources, remove_source, set_source_enabled

app = Flask(__name__)

DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


def _auth_configured() -> bool:
    return bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD)


def _credentials_match(username: str, password: str) -> bool:
    # compare_digest avoids leaking credential length/content via timing.
    return secrets.compare_digest(username, DASHBOARD_USERNAME) and secrets.compare_digest(
        password, DASHBOARD_PASSWORD
    )


@app.before_request
def require_auth():
    if not _auth_configured():
        return None
    auth = request.authorization
    if not auth or not _credentials_match(auth.username or "", auth.password or ""):
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Internship Bot Dashboard"'},
        )
    return None


@app.route("/")
def index():
    sources = load_sources()
    return render_template("index.html", sources=sources)


@app.post("/sources/add")
def add_source_route():
    url = request.form.get("url", "").strip()
    if url:
        add_source(url, "github_readme")
    return redirect(url_for("index"))


@app.post("/sources/remove")
def remove_source_route():
    source_id = request.form.get("source_id", "").strip()
    if source_id:
        remove_source(source_id)
    return redirect(url_for("index"))


@app.post("/sources/toggle")
def toggle_source_route():
    source_id = request.form.get("source_id", "").strip()
    enabled = request.form.get("enabled") == "true"
    if source_id:
        set_source_enabled(source_id, enabled)
    return redirect(url_for("index"))


@app.route("/internships")
def internships():
    init_db()
    jobs = list_internships(limit=250)
    return render_template("internships.html", jobs=jobs)


@app.post("/internships/status")
def update_status_route():
    internship_id = int(request.form.get("internship_id", "0"))
    status = request.form.get("status", "unknown")
    if internship_id:
        update_internship_status(internship_id, status)
    return redirect(url_for("internships"))


if __name__ == "__main__":
    init_db()

    # Defaults to loopback-only, non-debug, no auth required (nothing to
    # protect against on your own machine). Widen DASHBOARD_HOST only on a
    # network you trust, and set DASHBOARD_USERNAME/DASHBOARD_PASSWORD before
    # you do — see require_auth() above.
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    debug_mode = os.getenv("DASHBOARD_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}

    if debug_mode and host != "127.0.0.1":
        raise RuntimeError(
            "Refusing to start with DASHBOARD_DEBUG enabled while DASHBOARD_HOST "
            "is not 127.0.0.1 — this would expose the Werkzeug debugger console."
        )
    if host != "127.0.0.1" and not _auth_configured():
        raise RuntimeError(
            "Refusing to start with DASHBOARD_HOST != 127.0.0.1 and no "
            "DASHBOARD_USERNAME/DASHBOARD_PASSWORD set — the dashboard has no "
            "other authentication and would be wide open on your network."
        )
    app.run(host=host, port=5000, debug=debug_mode)
