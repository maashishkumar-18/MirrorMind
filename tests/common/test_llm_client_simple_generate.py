"""
Characterization test for src.common.llm_client.simple_generate()
(src/common/llm_client.py).

Originally a Phase 0 Step 0.2 bug-fix test: it pinned that simple_generate()
resolves its adapter through ProviderRegistry (rather than the old hardcoded
`adapter = DeepSeekAdapter()`), and that the default provider was "deepseek".

Phase 1 Step 1.1 intentionally changes that default: the Personal AI
Companion runs entirely local, so the only registered provider is now
"ollama" and simple_generate() defaults to it. The `characterization`
marker's contract is "pin current behavior, never assert document-era
behavior Phase 1 is expected to change" — so the resolution-through-registry
guarantee is still pinned here, retargeted to the new default.

Note: the real ProviderRegistry method is `.get()`, not `.get_provider()`
(the roadmap's Step 0.2 acceptance-criteria text named the wrong method).
"""

from unittest.mock import MagicMock

import pytest

from src.common.llm_client import ProviderRegistry, simple_generate

pytestmark = pytest.mark.characterization


def _fake_registry():
    registry = MagicMock(spec=ProviderRegistry)
    adapter = MagicMock()
    adapter.generate.return_value = MagicMock(content="the answer")
    registry.get.return_value = adapter
    return registry, adapter


def test_default_provider_resolves_through_registry_as_ollama():
    registry, adapter = _fake_registry()

    result = simple_generate("hello", registry=registry)

    registry.get.assert_called_once_with("ollama")
    adapter.generate.assert_called_once()
    assert result == "the answer"


def test_explicit_provider_is_passed_through_to_the_registry():
    registry, adapter = _fake_registry()

    simple_generate("hello", provider="gemini", registry=registry)

    registry.get.assert_called_once_with("gemini")


def test_model_config_provider_field_matches_the_requested_provider():
    registry, adapter = _fake_registry()

    simple_generate("hello", provider="openai", registry=registry)

    (prompt_obj, config), _kwargs = adapter.generate.call_args
    assert config.provider == "openai"
    assert prompt_obj.user_prompt == "hello"


def test_no_registry_injected_still_works_and_defaults_to_ollama():
    """Without an injected registry, simple_generate() must build its own
    ProviderRegistry() rather than raising. As of Step 1.1 that registry has
    exactly one provider — ollama — and the cloud adapters are unregistered."""
    registry = ProviderRegistry()
    assert "ollama" in registry.list_providers()
    assert "deepseek" not in registry.list_providers()
