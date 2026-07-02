"""Safe LinkedIn manual-link ingestion.

Direct LinkedIn scraping is intentionally not implemented. LinkedIn commonly blocks
bots and their policies prohibit scraping/automation. This module lets you paste a
job URL that you personally found and stores it in the same structure as scraped jobs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from utils.tags import add_company_classification_tag


def build_manual_linkedin_job(
    url: str,
    company: str = "Unknown Company",
    title: str = "LinkedIn Internship Link",
    location: str = "Unknown",
    tags: List[str] | None = None,
) -> Dict:
    return {
        "company": company.strip() or "Unknown Company",
        "title": title.strip() or "LinkedIn Internship Link",
        "location": location.strip() or "Unknown",
        "application_url": url.strip(),
        "source_url": url.strip(),
        "source_type": "linkedin_manual",
        "date_found": datetime.now(timezone.utc).isoformat(),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "tags": add_company_classification_tag(tags or ["manual", "linkedin", "internship"], company),
        "status": "unknown",
    }
