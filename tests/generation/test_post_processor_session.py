"""
Integration tests for the session-shaped PostProcessor
(src/generation/post_processor.py) — Phase 1 Step 1.4a.

Session-temporal citation extraction + GroundingValidator.llm_check with
simple_generate monkeypatched (never a real Ollama call). Replaces the
deleted __main__ fixture.
"""

import pytest

from src.common.types import SessionRetrievedChunk
from src.generation import post_processor as pp_module
from src.generation.config import (
    GeneratedAnswer,
    GenerationMode,
    ModelInfo,
    PostProcessingConfig,
    RetrievalMetadata,
    UsageStats,
)
from src.generation.post_processor import GroundingValidator, PostProcessor

pytestmark = pytest.mark.integration


def _chunks():
    return [
        SessionRetrievedChunk(
            chunk_id="s1::primary",
            session_id="s1",
            content="We decided the launch moves to Friday and Priya owns the checklist.",
            raw_content="We decided the launch moves to Friday and Priya owns the checklist.",
            timestamp="2026-08-20T14:00:00Z",
        )
    ]


def _generated(content):
    return GeneratedAnswer(
        content=content,
        model_info=ModelInfo(provider="ollama", model_name="llama3.1:8b"),
        usage=UsageStats(input_tokens=10, output_tokens=8, total_tokens=18),
        generation_time_ms=30.0,
    )


def _cfg(**kw):
    base = dict(
        extract_citations=True,
        citation_style=pp_module.CitationStyle.INLINE,
        validate_grounding=True,
        grounding_check_method="keyword_overlap",
        remove_artifacts=True,
        normalize_whitespace=True,
    )
    base.update(kw)
    return PostProcessingConfig(**base)


def _meta():
    from src.generation.config import ConfidenceLevel

    return RetrievalMetadata(
        confidence_score=0.9,
        confidence_level=ConfidenceLevel.HIGH,
        retrieval_time_ms=1.0,
        total_chunks_retrieved=1,
        session_id="s1",
    )


def _process(config, chunks=None):
    return PostProcessor(config=config).process(
        generated=_generated(
            "The launch moves to Friday. [Session s1 · approx. 2026-08-20T14:00:00Z]"
        ),
        chunks=_chunks() if chunks is None else chunks,
        retrieval_metadata=_meta(),
        mode=GenerationMode.CONTEXT_AWARE,
        request_id="r",
        generation_id="g",
    )


def test_extracts_and_matches_a_session_citation():
    resp = _process(_cfg())
    assert len(resp.citations) == 1
    c = resp.citations[0]
    assert c.session_id == "s1"
    assert c.approximate_timestamp == "2026-08-20T14:00:00Z"
    assert c.chunk_id == "s1::primary"  # matched to the source chunk by session_id


def test_keyword_overlap_grounding_default():
    resp = _process(_cfg(grounding_check_method="keyword_overlap"))
    assert resp.is_grounded is True
    assert resp.grounding_confidence > 0


def test_llm_check_grounding_uses_simple_generate(monkeypatch):
    calls = {}

    def _fake(prompt, model=None, **kw):
        calls["prompt"] = prompt
        calls["kw"] = kw
        return '{"grounded": true, "confidence": 0.88}'

    monkeypatch.setattr("src.common.llm_client.simple_generate", _fake)
    validator = GroundingValidator(method="llm_check")
    grounded, conf = validator.validate("the launch moves to friday", _chunks())
    assert grounded is True
    assert conf == pytest.approx(0.88)
    assert "launch moves to Friday" in calls["prompt"]
    # Phase 4 Step 4.3: a CPU 7B/8B grounding check needs > 30s; the default
    # timeout silently degrades every faithfulness score to keyword overlap.
    assert calls["kw"]["timeout_seconds"] == 120


def test_llm_check_falls_back_to_keyword_overlap_on_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr("src.common.llm_client.simple_generate", _boom)
    validator = GroundingValidator(method="llm_check")
    grounded, conf = validator.validate(
        "the launch moves to friday and priya owns the checklist", _chunks()
    )
    # keyword-overlap fallback still returns a real verdict
    assert isinstance(grounded, bool)
    assert conf > 0
