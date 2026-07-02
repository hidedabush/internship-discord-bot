"""Simple JSON-backed source management."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT_DIR / "sources.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_sources() -> List[Dict[str, Any]]:
    if not SOURCES_PATH.exists():
        save_sources([])
    with SOURCES_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "sources" in data:
        return data["sources"]
    if isinstance(data, list):
        return data
    return []


def save_sources(sources: List[Dict[str, Any]]) -> None:
    with SOURCES_PATH.open("w", encoding="utf-8") as f:
        json.dump(sources, f, indent=2)


def add_source(url: str, source_type: str = "github_readme") -> Dict[str, Any]:
    sources = load_sources()
    normalized_url = url.strip()

    for source in sources:
        if source["url"].strip().lower() == normalized_url.lower():
            source["enabled"] = True
            save_sources(sources)
            return source

    new_source = {
        "id": str(uuid4())[:8],
        "url": normalized_url,
        "type": source_type,
        "enabled": True,
        "date_added": _now_iso(),
    }
    sources.append(new_source)
    save_sources(sources)
    return new_source


def remove_source(url_or_id: str) -> bool:
    sources = load_sources()
    target = url_or_id.strip().lower()
    kept = [
        s
        for s in sources
        if s.get("id", "").lower() != target and s.get("url", "").strip().lower() != target
    ]
    if len(kept) == len(sources):
        return False
    save_sources(kept)
    return True


def set_source_enabled(source_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
    sources = load_sources()
    for source in sources:
        if source.get("id") == source_id:
            source["enabled"] = enabled
            save_sources(sources)
            return source
    return None


def get_enabled_sources() -> List[Dict[str, Any]]:
    return [s for s in load_sources() if s.get("enabled", True)]
