"""Shared test fixtures."""

from __future__ import annotations

import pytest

from database import db as db_module


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point every test at a throwaway SQLite file instead of the real one."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_internships.db")
    yield
