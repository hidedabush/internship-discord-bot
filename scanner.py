"""Run all enabled internship sources and store new jobs in SQLite."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from database.db import init_db, set_state, upsert_internship
from scraper.github_scraper import scrape_github_readme
from utils.filters import passes_filters
from utils.source_store import get_enabled_sources

LOGGER = logging.getLogger(__name__)


def run_scan(config: Dict[str, Any]) -> Dict[str, Any]:
    """Scan enabled sources and return a summary."""
    init_db()
    sources = get_enabled_sources()
    include_keywords = config.get("include_keywords", [])
    exclude_keywords = config.get("exclude_keywords", [])

    total_found = 0
    total_after_filters = 0
    new_jobs: List[Dict[str, Any]] = []
    errors: List[str] = []

    for source in sources:
        source_url = source.get("url", "")
        source_type = source.get("type", "github_readme")
        try:
            if source_type == "github_readme":
                internships = scrape_github_readme(source_url)
            else:
                LOGGER.info("Skipping unsupported automated source type: %s", source_type)
                continue

            total_found += len(internships)

            for internship in internships:
                if not passes_filters(internship, include_keywords, exclude_keywords):
                    continue
                total_after_filters += 1
                db_id, is_new = upsert_internship(internship)
                internship["id"] = db_id
                if is_new:
                    new_jobs.append(internship)

        except Exception as exc:  # Keep scanning even if one repo breaks.
            message = f"{source_url}: {exc}"
            LOGGER.exception("Source failed: %s", message)
            errors.append(message)

    scan_time = datetime.now(timezone.utc).isoformat()
    set_state("last_scan_time", scan_time)
    set_state("last_scan_found_count", str(total_after_filters))

    return {
        "sources_scanned": len(sources),
        "total_found_before_filters": total_found,
        "total_found_after_filters": total_after_filters,
        "new_jobs": new_jobs,
        "errors": errors,
        "scan_time": scan_time,
    }
