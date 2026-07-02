"""Shared internship tag helpers."""

from __future__ import annotations

import re
from typing import Iterable, List

FAANG_ALIASES = {
    "meta": ["meta", "facebook", "instagram", "whatsapp", "oculus"],
    "apple": ["apple"],
    "amazon": ["amazon", "aws", "amazon web services", "audible"],
    "netflix": ["netflix"],
    "google": ["google", "alphabet", "youtube", "google cloud", "deepmind"],
}


def is_faang_company(company: str) -> bool:
    """Return True when the company name matches a FAANG company or common alias."""
    company_lower = (company or "").lower()
    if not company_lower.strip():
        return False

    for aliases in FAANG_ALIASES.values():
        for alias in aliases:
            pattern = rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])"
            if re.search(pattern, company_lower):
                return True
    return False


def add_company_classification_tag(tags: Iterable[str] | str, company: str) -> List[str]:
    """Add exactly one FAANG classification tag while preserving existing tag order."""
    if isinstance(tags, str):
        tags = tags.split(",")

    clean_tags = []
    for tag in tags:
        clean = (tag or "").strip().lower()
        if clean and clean not in {"faang", "non-faang"}:
            clean_tags.append(clean)

    clean_tags.append("faang" if is_faang_company(company) else "non-faang")
    return list(dict.fromkeys(clean_tags))
