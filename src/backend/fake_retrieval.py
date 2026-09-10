"""Skip the heavy retrieval/generation warm-up for reliability tests
(Phase 4 Step 4.4).

``RAGPIPE_FAKE_RETRIEVAL`` is a test seam. When set, :func:`session_worker_kwargs`
returns stub ``agent`` / ``router`` / ``orchestrator`` / ``pipeline`` /
``slot_extractor`` objects so the ``SessionWorker`` never loads
``all-MiniLM-L6-v2`` or the cross-encoder — a backend spawns in well under a
second, which matters when the kill-fuzz harness relaunches it 100 times. The
packaged app never sets it.

The stubs make ``chat.send`` persist a user + assistant message (the property the
fuzz asserts) and make ``_reingest`` a no-op. They are NOT the ``RAGPIPE_FAKE_LLM``
seam — that one keeps the real pipeline and only swaps the Ollama adapter.
"""

from __future__ import annotations

import os
from typing import Any

_ENV_VAR = "RAGPIPE_FAKE_RETRIEVAL"


class _StubAgent:
    def reason(self, query: str, conversation_history: Any = None) -> Any:
        from src.common.types import AgenticActionType, AgenticOutput, RetrievalRoute

        return AgenticOutput(
            action_type=AgenticActionType.CONVERSATION,
            confidence=0.2,
            retrieve_needed=False,
            retrieval_route=RetrievalRoute.SEMANTIC,
            search_query=None,
            response=f"ack: {query[:80]}",
        )


class _StubRouter:
    def route(self, agentic_output: Any, query: str) -> list:
        return []


class _StubOrchestrator:
    # Never actually invoked: _StubAgent always returns retrieve_needed=False, so
    # SessionWorker.send() short-circuits to ao.response and skips generation.
    def generate(self, request: Any) -> Any:  # pragma: no cover
        raise AssertionError("stub orchestrator should not be called (retrieve_needed=False)")


class _StubPipeline:
    def ingest_session(
        self, session_id: str, messages: Any, *, timestamp: str | None = None
    ) -> None:
        return None


class _StubSlotExtractor:
    def extract(self, action_type: Any, utterance: str, now: str) -> dict:
        return {}


def enabled() -> bool:
    return bool(os.getenv(_ENV_VAR))


def session_worker_kwargs() -> dict[str, Any]:
    """Injection kwargs for ``SessionWorker`` — ``{}`` unless the seam is set."""
    if not enabled():
        return {}
    return {
        "agent": _StubAgent(),
        "router": _StubRouter(),
        "orchestrator": _StubOrchestrator(),
        "pipeline": _StubPipeline(),
        "slot_extractor": _StubSlotExtractor(),
    }
