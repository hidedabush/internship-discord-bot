"""Optional local Flask dashboard.

Run with:
    python dashboard/app.py
Then open:
    http://localhost:5000
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this file directly from dashboard/ while importing project modules.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, redirect, render_template, request, url_for

from database.db import init_db, list_internships, update_internship_status
from utils.source_store import add_source, load_sources, remove_source, set_source_enabled

app = Flask(__name__)


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
    app.run(host="127.0.0.1", port=5000, debug=True)
