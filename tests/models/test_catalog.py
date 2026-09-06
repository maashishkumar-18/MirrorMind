"""Unit tests for src/models/catalog.py (Phase 1 Step 1.6)."""

import json

import pytest

from src.models.catalog import load_model_catalog

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_catalog_env(monkeypatch):
    monkeypatch.delenv("RAGPIPE_MODEL_CATALOG", raising=False)


def test_bundled_catalog_loads_and_is_wellformed():
    entries = load_model_catalog()
    assert entries, "bundled catalog should not be empty"
    for e in entries:
        assert e.name
        assert e.display_name
        assert e.size_bytes > 0
        assert e.description
        assert e.min_ram_gb > 0


def test_bundled_catalog_has_recommended_default():
    entries = load_model_catalog()
    by_name = {e.name: e for e in entries}
    assert "llama3.1:8b" in by_name
    assert by_name["llama3.1:8b"].recommended is True
    assert sum(1 for e in entries if e.recommended) == 1


def test_env_override_reads_a_custom_file(tmp_path, monkeypatch):
    custom = tmp_path / "cat.json"
    custom.write_text(
        json.dumps(
            {
                "version": 1,
                "models": [
                    {
                        "name": "test:1b",
                        "display_name": "Test 1B",
                        "size_bytes": 123,
                        "description": "d",
                        "min_ram_gb": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RAGPIPE_MODEL_CATALOG", str(custom))
    entries = load_model_catalog()
    assert [e.name for e in entries] == ["test:1b"]
    assert entries[0].recommended is False


def test_missing_file_returns_empty_list(tmp_path):
    assert load_model_catalog(str(tmp_path / "nope.json")) == []
