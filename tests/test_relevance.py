"""Tests for the local-LLM relevance/quality classification logic."""

from __future__ import annotations

from unittest.mock import patch

from utils import relevance


def _job():
    return {"company": "Acme", "title": "SWE Intern", "location": "Remote", "tags": ["software"]}


def test_classify_relevance_happy_path():
    with patch.object(
        relevance, "generate_json", return_value={"relevant": True, "quality_score": 4, "reason": "solid"}
    ):
        result = relevance.classify_relevance(_job(), {})

    assert result == relevance.RelevanceResult(
        relevant=True, quality_score=4, reason="solid", source="llm"
    )


def test_classify_relevance_fails_open_on_ollama_error():
    with patch.object(relevance, "generate_json", side_effect=relevance.OllamaError("down")):
        result = relevance.classify_relevance(_job(), {})

    assert result.relevant
    assert result.quality_score == relevance.NEUTRAL_QUALITY_SCORE
    assert result.source == "fallback"


def test_classify_relevance_fails_open_on_unexpected_shape():
    with patch.object(relevance, "generate_json", return_value={"unexpected": "shape"}):
        result = relevance.classify_relevance(_job(), {})

    assert result.source == "fallback"


def test_classify_relevance_fails_open_on_non_integer_score():
    with patch.object(
        relevance,
        "generate_json",
        return_value={"relevant": True, "quality_score": "not a number", "reason": "x"},
    ):
        result = relevance.classify_relevance(_job(), {})

    assert result.source == "fallback"


def test_classify_relevance_clamps_out_of_range_score():
    with patch.object(
        relevance, "generate_json", return_value={"relevant": True, "quality_score": 99, "reason": "x"}
    ):
        result = relevance.classify_relevance(_job(), {})

    assert result.quality_score == relevance.MAX_QUALITY_SCORE


def test_classify_relevance_uses_config_overrides():
    seen_kwargs = {}

    def fake_generate_json(**kwargs):
        seen_kwargs.update(kwargs)
        return {"relevant": True, "quality_score": 3, "reason": "ok"}

    config = {
        "ollama_host": "http://custom-host:11434",
        "ollama_model": "custom-model",
        "llm_timeout_seconds": 5,
    }
    with patch.object(relevance, "generate_json", side_effect=fake_generate_json):
        relevance.classify_relevance(_job(), config)

    assert seen_kwargs["host"] == "http://custom-host:11434"
    assert seen_kwargs["model"] == "custom-model"
    assert seen_kwargs["timeout"] == 5
