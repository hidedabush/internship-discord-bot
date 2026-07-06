"""Tests for scanner.run_scan: filtering, closed-role handling, and the
optional LLM relevance pass."""

from __future__ import annotations

from unittest.mock import patch

from database.db import list_internships
from scraper.github_scraper import ScrapeResult
from utils.relevance import RelevanceResult
import scanner


def _source():
    return [{"id": "s1", "url": "https://github.com/o/r", "type": "github_readme"}]


def _job(**overrides):
    job = {
        "company": "Acme",
        "title": "SWE Intern",
        "application_url": "https://acme.example/apply",
        "status": "unknown",
        "tags": ["software"],
    }
    job.update(overrides)
    return job


def _base_config(**overrides):
    config = {"include_keywords": [], "exclude_keywords": []}
    config.update(overrides)
    return config


def _run_with_jobs(jobs, config):
    with patch("scanner.get_enabled_sources", return_value=_source()), \
         patch("scanner.update_source_fetch_cache"), \
         patch(
             "scanner.scrape_github_readme",
             return_value=ScrapeResult(internships=jobs, raw_url="raw", etag="e", not_modified=False),
         ):
        return scanner.run_scan(config)


def test_closed_on_first_sighting_is_stored_but_not_posted():
    jobs = [_job(company="Acme", status="closed"), _job(company="Beta", status="unknown")]
    result = _run_with_jobs(jobs, _base_config())

    assert [j["company"] for j in result["new_jobs"]] == ["Beta"]
    stored_companies = {row["company"] for row in list_internships(limit=10)}
    assert stored_companies == {"Acme", "Beta"}


def test_llm_filter_disabled_by_default_skips_relevance_call():
    with patch("scanner.classify_relevance") as mock_classify:
        result = _run_with_jobs([_job()], _base_config())

    mock_classify.assert_not_called()
    assert len(result["new_jobs"]) == 1
    assert "quality_score" not in result["new_jobs"][0]


def test_llm_filter_enabled_drops_irrelevant_and_scores_relevant():
    jobs = [_job(company="Acme"), _job(company="Beta", application_url="https://beta.example/apply")]
    verdicts = {
        "Acme": RelevanceResult(relevant=True, quality_score=5, reason="great match", source="llm"),
        "Beta": RelevanceResult(relevant=False, quality_score=1, reason="not relevant", source="llm"),
    }

    with patch("scanner.classify_relevance", side_effect=lambda job, cfg: verdicts[job["company"]]):
        result = _run_with_jobs(jobs, _base_config(llm_filter_enabled=True, llm_min_quality_score=1))

    assert [j["company"] for j in result["new_jobs"]] == ["Acme"]
    assert result["new_jobs"][0]["quality_score"] == 5

    # Both postings get their verdict persisted, even the filtered-out one —
    # useful for the dashboard, and avoids re-scoring it on a future scan.
    rows = {row["company"]: row for row in list_internships(limit=10)}
    assert rows["Acme"]["quality_score"] == 5
    assert rows["Beta"]["quality_score"] == 1


def test_llm_min_quality_score_threshold_drops_low_scoring_relevant_postings():
    jobs = [_job(company="Acme")]
    verdict = RelevanceResult(relevant=True, quality_score=2, reason="borderline", source="llm")

    with patch("scanner.classify_relevance", return_value=verdict):
        result = _run_with_jobs(jobs, _base_config(llm_filter_enabled=True, llm_min_quality_score=3))

    assert result["new_jobs"] == []


def test_llm_filter_only_runs_for_new_non_closed_postings():
    # A closed role should never reach classify_relevance — it's dropped before
    # the LLM call, saving a model call on something we'd discard anyway.
    with patch("scanner.classify_relevance") as mock_classify:
        _run_with_jobs([_job(status="closed")], _base_config(llm_filter_enabled=True))

    mock_classify.assert_not_called()
