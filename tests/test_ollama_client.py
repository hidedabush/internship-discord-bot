"""Tests for the thin Ollama HTTP wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from utils import ollama_client


def _fake_response(json_body):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_body)
    return resp


def test_generate_json_returns_parsed_model_output():
    body = {"response": '{"relevant": true, "quality_score": 5, "reason": "great"}'}
    with patch.object(ollama_client.requests, "post", return_value=_fake_response(body)):
        parsed = ollama_client.generate_json("http://host:11434", "model", "prompt")

    assert parsed == {"relevant": True, "quality_score": 5, "reason": "great"}


def test_generate_json_raises_ollama_error_on_network_failure():
    with patch.object(ollama_client.requests, "post", side_effect=requests.RequestException("down")):
        with pytest.raises(ollama_client.OllamaError):
            ollama_client.generate_json("http://host:11434", "model", "prompt")


def test_generate_json_raises_ollama_error_on_malformed_model_output():
    body = {"response": "not json at all"}
    with patch.object(ollama_client.requests, "post", return_value=_fake_response(body)):
        with pytest.raises(ollama_client.OllamaError):
            ollama_client.generate_json("http://host:11434", "model", "prompt")


def test_generate_json_strips_trailing_slash_from_host():
    seen_urls = []

    def fake_post(url, json=None, timeout=None):
        seen_urls.append(url)
        return _fake_response({"response": "{}"})

    with patch.object(ollama_client.requests, "post", side_effect=fake_post):
        ollama_client.generate_json("http://host:11434/", "model", "prompt")

    assert seen_urls == ["http://host:11434/api/generate"]
