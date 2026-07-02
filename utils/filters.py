"""Keyword filtering helpers."""

from __future__ import annotations

from typing import Dict, Iterable, List


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lower = text.lower()
    return any(keyword.strip().lower() in lower for keyword in keywords if keyword.strip())


def internship_to_search_text(internship: Dict) -> str:
    parts: List[str] = [
        internship.get("company", ""),
        internship.get("title", ""),
        internship.get("location", ""),
        internship.get("application_url", ""),
        internship.get("source_url", ""),
        " ".join(internship.get("tags", [])),
    ]
    return " ".join(parts)


def passes_filters(internship: Dict, include_keywords: List[str], exclude_keywords: List[str]) -> bool:
    """Return True if the internship should be stored/posted."""
    text = internship_to_search_text(internship)

    if exclude_keywords and _contains_any(text, exclude_keywords):
        return False

    # Empty include list means include everything.
    if include_keywords and not _contains_any(text, include_keywords):
        return False

    return True
