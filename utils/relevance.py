"""Local-LLM relevance + quality scoring for scraped internship postings.

Keyword include/exclude filtering (utils/filters.py) is cheap but coarse — it
lets through parser garbage and postings that only accidentally contain a
keyword, and it can't tell a strong listing from a weak one. This adds an
optional second pass using a small local Ollama model to:

  1. Catch clearly irrelevant postings the keyword filter missed.
  2. Score the rest 1-5 so the best postings can be prioritized when there
     are more new postings in a scan than max_posts_per_scan can post.

Disabled by default (llm_filter_enabled=false) since it requires a running
Ollama server with a model pulled. Fails open: if Ollama is unreachable or
returns something unusable, the posting is treated as relevant with a neutral
score rather than being silently dropped or blocking the scan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from utils.ollama_client import OllamaError, generate_json

LOGGER = logging.getLogger(__name__)

DEFAULT_OLLAMA_HOST = "http://192.168.1.84:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
NEUTRAL_QUALITY_SCORE = 3
MIN_QUALITY_SCORE = 1
MAX_QUALITY_SCORE = 5

_PROMPT_TEMPLATE = """You help a university Discord server's internship-alert bot decide \
which postings are worth showing students.

Posting:
  Company: {company}
  Role: {title}
  Location: {location}
  Existing tags: {tags}

Judge this ONE posting for a CS/software/data/AI-leaning student audience. Reply with ONLY \
a JSON object, no other text, matching this shape exactly:
{{"relevant": true or false, "quality_score": 1-5 integer, "reason": "one short sentence"}}

Guidance:
- relevant=false only for things that are clearly not a real, in-scope internship/co-op \
listing (e.g. parsing garbage, a full-time/senior role, something unrelated to tech).
- quality_score reflects how strong a match this is: 5 = well-known company, clear \
in-field role; 3 = plausible but generic or unclear; 1 = borderline/likely low value.
"""


@dataclass
class RelevanceResult:
    relevant: bool
    quality_score: int
    reason: str
    source: str  # "llm" or "fallback"


def _fallback(reason: str) -> RelevanceResult:
    return RelevanceResult(
        relevant=True, quality_score=NEUTRAL_QUALITY_SCORE, reason=reason, source="fallback"
    )


def classify_relevance(internship: Dict[str, Any], config: Dict[str, Any]) -> RelevanceResult:
    """Judge one internship posting. Always returns a usable result (fail-open)."""
    host = config.get("ollama_host") or DEFAULT_OLLAMA_HOST
    model = config.get("ollama_model") or DEFAULT_OLLAMA_MODEL
    timeout = float(config.get("llm_timeout_seconds", 15))

    prompt = _PROMPT_TEMPLATE.format(
        company=internship.get("company", "Unknown"),
        title=internship.get("title", "Unknown"),
        location=internship.get("location", "Unknown"),
        tags=", ".join(internship.get("tags", [])) or "none",
    )

    try:
        parsed = generate_json(host=host, model=model, prompt=prompt, timeout=timeout)
    except OllamaError as exc:
        LOGGER.warning("Relevance check failed, keeping posting by default: %s", exc)
        return _fallback("Ollama unavailable; kept by default")

    try:
        relevant = bool(parsed["relevant"])
        quality_score = int(parsed["quality_score"])
        reason = str(parsed.get("reason", "")).strip() or "No reason given"
    except (KeyError, TypeError, ValueError) as exc:
        LOGGER.warning("Relevance check returned an unexpected shape %r: %s", parsed, exc)
        return _fallback("Unexpected model output; kept by default")

    quality_score = max(MIN_QUALITY_SCORE, min(MAX_QUALITY_SCORE, quality_score))
    return RelevanceResult(relevant=relevant, quality_score=quality_score, reason=reason, source="llm")
