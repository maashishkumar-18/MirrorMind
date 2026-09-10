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


_STUB_INGEST_ENV = "RAGPIPE_E2E_STUB_INGEST"
_STUB_RERANK_ENV = "RAGPIPE_E2E_STUB_RERANK"


class _FakeCrossEncoder:
    """Deterministic stand-in for the sentence-transformers CrossEncoder — no
    HuggingFace download. Mirrors ``eval/run_eval.py::_FakeCrossEncoder``. Used
    by the memory-retrieval e2e flow so a cold CI runner never has to fetch
    ``cross-encoder/ms-marco-MiniLM-L-6-v2`` mid-test."""

    def predict(self, pairs: Any, **_: Any) -> Any:
        import numpy as np

        return np.arange(len(pairs), 0, -1, dtype=float)


def maybe_stub_reranker(reranker: Any) -> None:
    """When ``RAGPIPE_E2E_STUB_RERANK`` is set, swap the cross-encoder model on
    ``reranker`` for :class:`_FakeCrossEncoder` (real embedding + hybrid search
    + store stay intact). No-op otherwise."""
    if not os.getenv(_STUB_RERANK_ENV) or reranker is None:
        return
    backend = getattr(reranker, "backend", None)
    if backend is not None and hasattr(backend, "_model"):
        backend._model = _FakeCrossEncoder()


def enabled() -> bool:
    return bool(os.getenv(_ENV_VAR))


def session_worker_kwargs() -> dict[str, Any]:
    """Injection kwargs for ``SessionWorker``.

    * ``RAGPIPE_FAKE_RETRIEVAL`` → stub the whole pipeline (agent / router /
      orchestrator / slot extractor / ingest) for a sub-second warm-up (the
      kill-fuzz harness).
    * ``RAGPIPE_E2E_STUB_INGEST`` → stub **only** re-ingestion, keeping the real
      agent (fake LLM) / router / orchestrator. The async ``_reingest`` followup
      otherwise blocks the next user call for seconds on a cold CI runner — the
      e2e flows that don't test memory retrieval set this.
    * neither → ``{}``.
    """
    if enabled():
        return {
            "agent": _StubAgent(),
            "router": _StubRouter(),
            "orchestrator": _StubOrchestrator(),
            "pipeline": _StubPipeline(),
            "slot_extractor": _StubSlotExtractor(),
        }
    if os.getenv(_STUB_INGEST_ENV):
        return {"pipeline": _StubPipeline()}
    return {}
