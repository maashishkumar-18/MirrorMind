"""
End-to-end integration test for GenerationOrchestrator
(src/generation/orchestrator.py) — Phase 1 Step 1.4a.

Real config (the shipped config/generation/), real PromptBuilder +
ContextBuilder + PostProcessor; only LLMClient is mocked (no Ollama). A
SessionRetrievedChunk request flows through to a response carrying a
session-timestamp citation.
"""

import pytest

from src.common.types import SessionRetrievedChunk
from src.generation.config import (
    ConfidenceLevel,
    GeneratedAnswer,
    GenerationConfig,
    GenerationMode,
    GenerationRequest,
    ModelInfo,
    RetrievalMetadata,
    UsageStats,
)
from src.generation.orchestrator import GenerationOrchestrator

pytestmark = pytest.mark.integration


class _FakeLLMClient:
    def __init__(self, content):
        self._content = content

    def generate(self, prompt, model_config):
        return GeneratedAnswer(
            content=self._content,
            model_info=ModelInfo(provider="ollama", model_name="llama3.1:8b"),
            usage=UsageStats(input_tokens=50, output_tokens=20, total_tokens=70),
            generation_time_ms=25.0,
        )


def _request():
    return GenerationRequest(
        request_id="req-e2e-1",
        query="what did we decide about the trip budget?",
        mode=GenerationMode.CONTEXT_AWARE,
        chunks=[
            SessionRetrievedChunk(
                chunk_id="s7::primary",
                session_id="s7",
                content="We capped the trip budget at $1200 and Sam books the hotel.",
                raw_content="We capped the trip budget at $1200 and Sam books the hotel.",
                topics=["trip budget"],
                timestamp="2026-07-10T18:30:00Z",
            )
        ],
        retrieval_metadata=RetrievalMetadata(
            confidence_score=0.9,
            confidence_level=ConfidenceLevel.HIGH,
            retrieval_time_ms=12.0,
            total_chunks_retrieved=1,
            session_id="s7",
            retrieval_method="semantic",
        ),
    )


def test_generate_produces_a_session_cited_response():
    answer = (
        "You capped the trip budget at $1200 and Sam is booking the hotel. "
        "[Session s7 · approx. 2026-07-10T18:30:00Z]"
    )
    orch = GenerationOrchestrator(
        config=GenerationConfig.from_yaml(),
        llm_client=_FakeLLMClient(answer),
    )
    response = orch.generate(_request())

    assert response.error_type is None
    assert "$1200" in response.answer
    assert response.answer_format.value == "markdown"
    assert len(response.citations) == 1
    assert response.citations[0].session_id == "s7"
    assert response.citations[0].chunk_id == "s7::primary"
    assert response.prompt_version == "2.0.0"
    for word in ("course", "chapter", "slide", "page "):
        assert word not in response.answer.lower()


def test_empty_llm_response_yields_error_response():
    orch = GenerationOrchestrator(
        config=GenerationConfig.from_yaml(),
        llm_client=_FakeLLMClient(""),
    )
    response = orch.generate(_request())
    assert response.answer == ""
    assert any("empty or error" in w for w in response.warnings)
