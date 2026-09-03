"""
Integration tests for the session-shaped PromptBuilder
(src/generation/prompt_builder.py) — Phase 1 Step 1.4a.

Real TemplateLoader (the shipped config/generation/prompts/), real
ContextBuilder; no DB, no model. Replaces the deleted __main__ fixture and
pins the roadmap acceptance criterion: build() output differs with vs.
without conversation_history.
"""

import pytest

from src.common.types import SessionRetrievedChunk
from src.generation.config import (
    AnswerFormat,
    ChatTurn,
    CitationStyle,
    ConfidenceLevel,
    GenerationMode,
    GenerationRequest,
    ModeConfig,
    RetrievalMetadata,
)
from src.generation.prompt_builder import PromptBuilder

pytestmark = pytest.mark.integration

MODE = ModeConfig(
    mode=GenerationMode.CONTEXT_AWARE,
    prompt_id="context_aware",
    model_key="ollama_default",
    answer_format=AnswerFormat.MARKDOWN,
    citation_style=CitationStyle.INLINE,
    include_context_header=True,
    max_context_tokens=3000,
    system_instructions="test",
)


def _chunk(cid="c1", session_id="s1"):
    return SessionRetrievedChunk(
        chunk_id=cid,
        session_id=session_id,
        content="We agreed to move the launch to Friday.",
        raw_content="We agreed to move the launch to Friday.",
        topics=["launch planning"],
        timestamp="2026-08-20T14:00:00Z",
        chunk_type="primary",
        score=0.9,
    )


def _request(history=None):
    return GenerationRequest(
        request_id="req-1",
        query="when did we move the launch to?",
        mode=GenerationMode.CONTEXT_AWARE,
        chunks=[_chunk()],
        retrieval_metadata=RetrievalMetadata(
            confidence_score=0.9,
            confidence_level=ConfidenceLevel.HIGH,
            retrieval_time_ms=10.0,
            total_chunks_retrieved=1,
            session_id="s1",
        ),
        conversation_history=history,
    )


def test_builds_a_session_framed_prompt():
    prompt = PromptBuilder().build(_request(), MODE)
    text = (prompt.system_prompt + prompt.user_prompt).lower()
    assert "move the launch to friday" in text
    assert "[session s1 · approx. 2026-08-20t14:00:00z]" in text
    assert "launch planning" in text  # {topics}
    for word in ("course", "chapter", "slide", "page "):
        assert word not in text
    assert prompt.template_id == "context_aware"
    assert prompt.template_version == "2.0.0"


def test_conversation_history_changes_the_prompt():
    without = PromptBuilder().build(_request(history=None), MODE)
    with_hist = PromptBuilder().build(
        _request(
            history=[
                ChatTurn(role="user", content="let's talk about the launch"),
                ChatTurn(role="assistant", content="sure, what about it?"),
            ]
        ),
        MODE,
    )
    assert with_hist.user_prompt != without.user_prompt
    assert "RECENT CONVERSATION" in with_hist.user_prompt
    assert "let's talk about the launch" in with_hist.user_prompt
    assert "RECENT CONVERSATION" not in without.user_prompt


def test_action_type_templates_load_by_prompt_id():
    # 5 net-new templates selected purely by prompt_id — no GenerationMode value.
    for pid in (
        "reminder_confirmation",
        "todo_confirmation",
        "meeting_summary",
        "schedule_confirmation",
        "conflict_alert",
    ):
        mode = ModeConfig(
            mode=GenerationMode.CONTEXT_AWARE,
            prompt_id=pid,
            model_key="ollama_default",
            answer_format=AnswerFormat.MARKDOWN,
            citation_style=CitationStyle.INLINE,
            include_context_header=False,
            max_context_tokens=2000,
            system_instructions="",
        )
        prompt = PromptBuilder().build(_request(), mode)
        assert prompt.template_id == pid
        assert prompt.system_prompt
