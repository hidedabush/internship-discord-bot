"""Thin HTTP client for a local Ollama server.

Kept separate from utils/relevance.py so the HTTP/JSON plumbing can be tested
(and swapped) independently of the prompt and result-parsing logic.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import requests

LOGGER = logging.getLogger(__name__)


class OllamaError(Exception):
    """Raised when the local Ollama server can't be reached or errors out."""


def generate_json(host: str, model: str, prompt: str, timeout: float = 15.0) -> Dict[str, Any]:
    """Ask Ollama to generate a single JSON object and return it parsed.

    Raises OllamaError on any network/HTTP/JSON failure so callers decide how
    to fail — this module never guesses at a fallback value itself.
    """
    url = host.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaError(f"Could not reach Ollama at {url}: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise OllamaError(f"Ollama returned a non-JSON response body: {exc}") from exc

    raw_text = body.get("response", "")
    try:
        return json.loads(raw_text)
    except (TypeError, ValueError) as exc:
        raise OllamaError(f"Ollama's model output wasn't valid JSON: {raw_text!r}") from exc
