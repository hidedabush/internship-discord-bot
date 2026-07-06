"""Tests for FAANG alias detection and classification tagging."""

from __future__ import annotations

import pytest

from utils.tags import add_company_classification_tag, is_faang_company


@pytest.mark.parametrize(
    "company",
    [
        "Meta",
        "Meta Platforms",
        "Facebook",
        "Apple",
        "Apple Inc.",
        "Amazon",
        "AWS",
        "Amazon Web Services",
        "Netflix",
        "Google",
        "Alphabet",
        "Google Cloud",
    ],
)
def test_is_faang_company_matches_known_aliases(company):
    assert is_faang_company(company)


@pytest.mark.parametrize(
    "company",
    [
        "Metamaterials Inc",
        "Megaphone Software",
        "Applesauce Studios",
        "Nvidia",
        "Some Random Startup",
        "",
    ],
)
def test_is_faang_company_rejects_substring_false_positives(company):
    assert not is_faang_company(company)


def test_add_company_classification_tag_appends_faang_for_known_company():
    tags = add_company_classification_tag(["software", "internship"], "Google")
    assert tags[-1] == "faang"
    assert "software" in tags
    assert "internship" in tags


def test_add_company_classification_tag_appends_non_faang_for_unknown_company():
    tags = add_company_classification_tag(["software"], "Some Random Startup")
    assert tags[-1] == "non-faang"


def test_add_company_classification_tag_replaces_stale_classification():
    # Simulates re-tagging a row that was previously (incorrectly) classified.
    tags = add_company_classification_tag(["software", "faang", "non-faang"], "Netflix")
    assert tags.count("faang") + tags.count("non-faang") == 1
    assert tags[-1] == "faang"


def test_add_company_classification_tag_accepts_comma_joined_string():
    tags = add_company_classification_tag("software,internship", "Apple")
    assert tags == ["software", "internship", "faang"]
