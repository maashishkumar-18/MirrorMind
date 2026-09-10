"""``src/backend/fake_llm.py`` — the Phase 4 e2e LLM stub seam."""

import json

import pytest

from src.backend import fake_llm
from src.common.llm_client import ProviderRegistry, simple_generate

pytestmark = pytest.mark.unit


@pytest.fixture
def fixture_file(tmp_path):
    def _write(data: dict) -> str:
        path = tmp_path / "fake_llm.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    return _write


def test_matches_first_rule_by_any_substring(fixture_file):
    fx = fake_llm.FakeLLMFixture.load(
        fixture_file(
            {
                "rules": [
                    {"when": {"any": ["classify this"]}, "respond": "AGENT-JSON"},
                    {"when": {"any": ["ground"]}, "respond": "GROUNDING"},
                ],
                "default": "DEFAULT",
            }
        )
    )
    assert fx.respond_to("You classify THIS message", "hello") == "AGENT-JSON"
    assert fx.respond_to("", "please GROUND the answer") == "GROUNDING"
    assert fx.respond_to("", "nothing relevant") == "DEFAULT"


def test_all_requires_every_token(fixture_file):
    fx = fake_llm.FakeLLMFixture.load(
        fixture_file({"rules": [{"when": {"all": ["remind", "thursday"]}, "respond": "R"}]})
    )
    assert fx.respond_to("", "remind me on thursday") == "R"
    with pytest.raises(RuntimeError):  # no default, "thursday" alone doesn't match
        fx.respond_to("", "remind me later")


def test_install_if_configured_noop_without_env(monkeypatch):
    monkeypatch.delenv("RAGPIPE_FAKE_LLM", raising=False)
    assert fake_llm.install_if_configured() is False


def test_install_patches_provider_registry_process_wide(monkeypatch, fixture_file):
    original = ProviderRegistry._register_defaults
    try:
        monkeypatch.setenv(
            "RAGPIPE_FAKE_LLM",
            fixture_file(
                {"rules": [{"when": {"any": ["hi"]}, "respond": "canned"}], "default": "d"}
            ),
        )
        assert fake_llm.install_if_configured() is True
        # a registry built AFTER the patch serves the fake for "ollama"
        adapter = ProviderRegistry().get("ollama")
        assert isinstance(adapter, fake_llm.FakeOllamaAdapter)
        assert simple_generate("hi there", provider="ollama") == "canned"
    finally:
        ProviderRegistry._register_defaults = original
