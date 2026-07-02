"""Safe Jobright manual-link ingestion.

This module does not scrape Jobright. It stores URLs you manually paste so the bot can
track them alongside GitHub-sourced internships.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List


def build_manual_jobright_job(
    url: str,
    company: str = "Unknown Company",
    title: str = "Jobright Internship Link",
    location: str = "Unknown",
    tags: List[str] | None = None,
) -> Dict:
    return {
        "company": company.strip() or "Unknown Company",
        "title": title.strip() or "Jobright Internship Link",
        "location": location.strip() or "Unknown",
        "application_url": url.strip(),
        "source_url": url.strip(),
        "source_type": "jobright_manual",
        "date_found": datetime.now(timezone.utc).isoformat(),
        "tags": tags or ["manual", "jobright", "internship"],
        "status": "unknown",
    }
