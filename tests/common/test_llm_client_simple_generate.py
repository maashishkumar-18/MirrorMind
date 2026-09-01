"""
Characterization test for Phase 0 Step 0.2 Bug 2:
src.common.llm_client.simple_generate() (src/common/llm_client.py).

Before this fix, simple_generate() hardcoded `adapter = DeepSeekAdapter()`
directly, bypassing ProviderRegistry entirely -- inconsistent with
LLMClient.__init__'s own pattern (`self.registry.get(config.provider)`).

Note: the real ProviderRegistry method is `.get()`, not `.get_provider()`
(confirmed at src/common/llm_client.py -- ProviderRegistry.get). This test
asserts `registry.get`, matching the roadmap's intent even though its own
acceptance-criteria text names the wrong method.
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


def test_default_provider_resolves_through_registry_as_deepseek():
    registry, adapter = _fake_registry()

    result = simple_generate("hello", registry=registry)

    registry.get.assert_called_once_with("deepseek")
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


def test_no_registry_injected_still_works_and_defaults_to_deepseek():
    """Without an injected registry, simple_generate() must build its own
    ProviderRegistry() rather than raising -- this is the real, unmocked
    path every existing call site (chunker.py, metadata_extractor.py,
    reranker.py, retrieval_agent.py) exercises today."""
    registry = ProviderRegistry()
    assert "deepseek" in registry.list_providers()
