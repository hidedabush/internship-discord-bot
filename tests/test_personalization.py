"""Tests for per-member personalized match scoring."""

from __future__ import annotations

from unittest.mock import patch

from utils import personalization


def _job():
    return {"company": "Acme", "title": "Backend Intern", "location": "Remote", "tags": ["backend"]}


def test_score_personal_match_happy_path():
    with patch.object(
        personalization,
        "generate_json",
        return_value={"match_score": 5, "reason": "Matches your backend interest"},
    ):
        result = personalization.score_personal_match(_job(), "backend/Go", {})

    assert result == personalization.PersonalMatchResult(
        match_score=5, reason="Matches your backend interest", source="llm"
    )


def test_score_personal_match_fails_open_on_ollama_error():
    with patch.object(personalization, "generate_json", side_effect=personalization.OllamaError("down")):
        result = personalization.score_personal_match(_job(), "backend/Go", {})

    assert result.match_score == personalization.NEUTRAL_QUALITY_SCORE
    assert result.source == "fallback"


def test_score_personal_match_fails_open_on_unexpected_shape():
    with patch.object(personalization, "generate_json", return_value={"unexpected": "shape"}):
        result = personalization.score_personal_match(_job(), "backend/Go", {})

    assert result.source == "fallback"


def test_score_personal_match_clamps_out_of_range_score():
    with patch.object(
        personalization, "generate_json", return_value={"match_score": 0, "reason": "x"}
    ):
        result = personalization.score_personal_match(_job(), "backend/Go", {})

    assert result.match_score == personalization.MIN_MATCH_SCORE


def test_score_personal_match_includes_profile_blurb_in_prompt():
    seen_prompts = []

    def fake_generate_json(host, model, prompt, timeout):
        seen_prompts.append(prompt)
        return {"match_score": 3, "reason": "ok"}

    with patch.object(personalization, "generate_json", side_effect=fake_generate_json):
        personalization.score_personal_match(_job(), "I only want remote ML roles", {})

    assert "I only want remote ML roles" in seen_prompts[0]
