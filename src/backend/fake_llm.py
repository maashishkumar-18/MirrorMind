"""Deterministic LLM stub for end-to-end tests (Phase 4 Step 4.1).

``RAGPIPE_FAKE_LLM`` is a **test seam** — same spirit as ``RAGPIPE_DB_KEY`` in
:mod:`src.backend.keys`. When it points at a fixture JSON file,
:func:`install_if_configured` replaces the ``ollama`` provider adapter
process-wide with a :class:`FakeOllamaAdapter` that answers from the fixture
instead of calling a real Ollama daemon. The packaged app never sets it.

One seam covers the whole inference surface: ``simple_generate`` (retrieval
agent, slot extraction, meeting-note extraction, session-metadata extraction,
the ``llm_check`` grounding validator) and ``LLMClient.generate`` (the
generation orchestrator) both resolve their adapter through
``ProviderRegistry().get("ollama")``. Patching
``ProviderRegistry._register_defaults`` — the *class* method, not an instance —
means every ``ProviderRegistry()`` built afterwards (and each one is built
lazily, per call or per component) picks up the fake, regardless of the order
components are constructed in ``main()``.

Fixture format::

    {
      "rules": [
        {"when": {"any": ["<substring>", ...]}, "respond": "<verbatim completion>"},
        {"when": {"all": ["<substring>", ...]}, "respond": "..."}
      ],
      "default": "<completion used when no rule matches>"
    }

The combined ``system_prompt + "\\n" + user_prompt`` is matched against each
rule in order; the first match wins. ``respond`` is returned verbatim as the
completion text — the caller does its own JSON recovery / cleanup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENV_VAR = "RAGPIPE_FAKE_LLM"

# `{{regex:PATTERN}}` inside a `respond` string is replaced by capture group 1 of
# PATTERN matched against the ORIGINAL-CASE combined prompt (empty on no match).
# Lets a canned generation echo the real `[Session <id> · approx. <ts>]` header
# that ContextBuilder injected — the id isn't knowable when the fixture is written.
_TEMPLATE_RE = re.compile(r"\{\{regex:(.+?)\}\}")


class _Rule:
    __slots__ = ("any_", "all_", "respond")

    def __init__(self, spec: dict[str, Any]) -> None:
        when = spec.get("when") or {}
        self.any_: list[str] = [s.lower() for s in when.get("any", [])]
        self.all_: list[str] = [s.lower() for s in when.get("all", [])]
        if "respond" not in spec:
            raise ValueError(f"fake-LLM rule missing 'respond': {spec!r}")
        self.respond: str = spec["respond"]

    def matches(self, haystack: str) -> bool:
        if self.all_ and not all(tok in haystack for tok in self.all_):
            return False
        if self.any_ and not any(tok in haystack for tok in self.any_):
            return False
        # A rule with neither any/all is a catch-all (rare; prefer `default`).
        return bool(self.any_ or self.all_)


class FakeLLMFixture:
    """The parsed ``RAGPIPE_FAKE_LLM`` fixture."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._rules = [_Rule(r) for r in data.get("rules", [])]
        self._default: str | None = data.get("default")

    @classmethod
    def load(cls, path: str | Path) -> FakeLLMFixture:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(raw)

    @staticmethod
    def _expand(respond: str, prompt: str) -> str:
        def _sub(m: re.Match[str]) -> str:
            found = re.search(m.group(1), prompt)
            return found.group(1) if found and found.groups() else ""

        return _TEMPLATE_RE.sub(_sub, respond)

    def respond_to(self, system_prompt: str, user_prompt: str) -> str:
        prompt = f"{system_prompt}\n{user_prompt}"
        haystack = prompt.lower()
        for rule in self._rules:
            if rule.matches(haystack):
                return self._expand(rule.respond, prompt)
        if self._default is not None:
            logger.warning(
                "fake-LLM: no rule matched; using default. prompt head=%r", user_prompt[:200]
            )
            return self._default
        raise RuntimeError(
            "fake-LLM: no rule matched and no 'default' in the fixture. "
            f"prompt head={user_prompt[:200]!r}"
        )


class FakeOllamaAdapter:
    """Drop-in for :class:`src.common.llm_client.OllamaAdapter` — answers from a
    fixture, never touches the network. Implements the full ``ProviderAdapter``
    surface (``generate`` / ``validate_credentials``)."""

    def __init__(self, fixture: FakeLLMFixture) -> None:
        self._fixture = fixture

    def generate(self, prompt: Any, config: Any, timeout_seconds: int = 30) -> Any:
        from src.generation.config import GeneratedAnswer, UsageStats

        start = time.time()
        content = self._fixture.respond_to(
            getattr(prompt, "system_prompt", "") or "",
            getattr(prompt, "user_prompt", "") or "",
        )
        usage = UsageStats(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            provider_metadata={"model": config.model_name, "fake": True},
        )
        return GeneratedAnswer(
            content=content,
            model_info=config.to_model_info(),
            usage=usage,
            generation_time_ms=round((time.time() - start) * 1000, 2),
            finish_reason="stop",
            raw_response={"fake": True},
        )

    def validate_credentials(self) -> bool:
        return True


def install_if_configured() -> bool:
    """If ``RAGPIPE_FAKE_LLM`` is set, patch ``ProviderRegistry`` so every
    ``ollama`` adapter is a :class:`FakeOllamaAdapter`. Returns ``True`` when the
    patch was applied. Call once, before any component is constructed."""
    path = os.getenv(_ENV_VAR)
    if not path:
        return False

    fixture = FakeLLMFixture.load(path)

    from src.common import llm_client

    original = llm_client.ProviderRegistry._register_defaults

    def _register_fake(self: Any) -> None:
        original(self)  # keep any real defaults, then override ollama
        self.register("ollama", FakeOllamaAdapter(fixture))

    llm_client.ProviderRegistry._register_defaults = _register_fake  # type: ignore[method-assign]
    logger.warning("RAGPIPE_FAKE_LLM active — all Ollama inference served from %s", path)
    return True
