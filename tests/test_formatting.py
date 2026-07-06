"""Tests for Discord embed formatting: quality-score display and the
personalized (premium DM) embed variant."""

from __future__ import annotations

from utils.formatting import format_quality_score, internship_to_embed, personal_match_to_embed


def _job(**overrides):
    job = {
        "company": "Acme",
        "title": "SWE Intern",
        "location": "Remote",
        "application_url": "https://acme.example/apply",
        "source_url": "https://github.com/example/internships",
        "source_type": "github_readme",
        "tags": ["software"],
    }
    job.update(overrides)
    return job


def test_format_quality_score_renders_stars_and_clamps_range():
    assert format_quality_score(5) == "⭐⭐⭐⭐⭐ (5/5)"
    assert format_quality_score(1) == "⭐☆☆☆☆ (1/5)"
    assert format_quality_score(99) == "⭐⭐⭐⭐⭐ (5/5)"  # clamped
    assert format_quality_score(0) == "⭐☆☆☆☆ (1/5)"  # clamped


def test_format_quality_score_empty_for_non_int():
    assert format_quality_score(None) == ""
    assert format_quality_score("not a score") == ""


def test_internship_to_embed_shows_match_field_only_when_scored():
    scored = internship_to_embed(_job(quality_score=4, llm_reason="Strong match"))
    field_names = [f.name for f in scored.fields]
    assert "Match" in field_names
    assert scored.footer.text == "Strong match"

    unscored = internship_to_embed(_job())
    assert "Match" not in [f.name for f in unscored.fields]
    assert unscored.footer.text is None


def test_personal_match_to_embed_shows_personal_reason_and_your_match_field():
    embed = personal_match_to_embed(_job(), match_score=5, reason="Matches your backend interest")

    assert "Matches your backend interest" in embed.description
    field_names = [f.name for f in embed.fields]
    assert "Your Match" in field_names
    your_match = next(f for f in embed.fields if f.name == "Your Match")
    assert your_match.value == format_quality_score(5)


def test_personal_match_to_embed_is_distinct_from_server_wide_match_field():
    # A posting can carry both a server-wide quality_score AND a personal one —
    # they should show as two separate, clearly-labeled fields, not collide.
    job = _job(quality_score=2, llm_reason="Generic server-wide note")
    embed = personal_match_to_embed(job, match_score=5, reason="Great personal fit")

    field_names = [f.name for f in embed.fields]
    assert field_names.count("Match") == 1
    assert field_names.count("Your Match") == 1
