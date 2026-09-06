"""
Unit tests for RetrievalAgent (src/retrieval/retrieval_agent.py) — Phase 1
Step 1.3b. The single Ollama call is mocked; these pin the JSON parsing,
the markdown-fence + regex recovery, and the safe-fallback behavior.
"""

import json

import pytest

from src.common.types import AgenticActionType, AgenticOutput, RetrievalRoute, SessionMessage
from src.retrieval import retrieval_agent as agent_module
from src.retrieval.retrieval_agent import RetrievalAgent

pytestmark = pytest.mark.unit

CLEAN = {
    "action_type": "retrieval_query",
    "confidence": 0.9,
    "retrieve_needed": True,
    "retrieval_route": "semantic",
    "search_query": "what did we decide about the trip",
    "response": "Let me check what we said about that.",
}


def _patch_generate(monkeypatch, value):
    monkeypatch.setattr(agent_module, "simple_generate", lambda *a, **k: value)


def test_parses_a_clean_json_object(monkeypatch):
    _patch_generate(monkeypatch, json.dumps(CLEAN))
    out = RetrievalAgent().reason("what did we decide about the trip?")
    assert isinstance(out, AgenticOutput)
    assert out.action_type == AgenticActionType.RETRIEVAL_QUERY
    assert out.retrieve_needed is True
    assert out.retrieval_route == RetrievalRoute.SEMANTIC
    assert out.search_query == "what did we decide about the trip"


def test_strips_a_markdown_json_fence(monkeypatch):
    _patch_generate(monkeypatch, f"```json\n{json.dumps(CLEAN)}\n```")
    out = RetrievalAgent().reason("q")
    assert out.action_type == AgenticActionType.RETRIEVAL_QUERY


def test_recovers_json_embedded_in_prose(monkeypatch):
    _patch_generate(
        monkeypatch, f"Here is the result you asked for:\n{json.dumps(CLEAN)}\nHope that helps!"
    )
    out = RetrievalAgent().reason("q")
    assert out.confidence == 0.9


def test_malformed_response_falls_back_to_safe_conversation(monkeypatch):
    _patch_generate(monkeypatch, "not json at all, sorry")
    out = RetrievalAgent().reason("q")
    assert out.action_type == AgenticActionType.CONVERSATION
    assert out.retrieve_needed is False
    assert out.confidence == 0.0
    assert out.response == "not json at all, sorry"


def test_schema_violating_json_falls_back(monkeypatch):
    _patch_generate(monkeypatch, json.dumps({**CLEAN, "confidence": 5.0}))
    out = RetrievalAgent().reason("q")
    assert out.action_type == AgenticActionType.CONVERSATION
    assert out.retrieve_needed is False


@pytest.mark.parametrize(
    "raw",
    [
        "[1, 2, 3]",  # JSON array — old scrub only caught leading '{'
        '{"retrieval_route": "semantic", "search_query": "x"',  # truncated object
        'garbled {"a":1} {"b":2} {"c":3} output',  # brace-heavy prose
    ],
)
def test_envelope_looking_garbage_is_not_echoed_to_the_user(monkeypatch, raw):
    """Phase 1 audit 1.3-C4: the fallback must not surface raw machine-contract
    text as the user-facing response."""
    _patch_generate(monkeypatch, raw)
    out = RetrievalAgent().reason("q")
    assert out.action_type == AgenticActionType.CONVERSATION
    assert out.retrieve_needed is False
    assert out.response == "Sorry, I had trouble understanding that — could you rephrase?"


def test_provider_exception_falls_back(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("ollama not running")

    monkeypatch.setattr(agent_module, "simple_generate", _boom)
    out = RetrievalAgent().reason("q")
    assert out.action_type == AgenticActionType.CONVERSATION
    assert "trouble understanding" in out.response


def test_conversation_history_is_included_in_the_prompt(monkeypatch):
    captured = {}

    def _capture(prompt, *a, **k):
        captured["prompt"] = prompt
        return json.dumps(CLEAN)

    monkeypatch.setattr(agent_module, "simple_generate", _capture)
    RetrievalAgent().reason(
        "and what time?",
        conversation_history=[
            SessionMessage("user", "let's meet Sarah on Tuesday"),
            SessionMessage("assistant", "sure, I'll note that"),
        ],
    )
    assert "let's meet Sarah on Tuesday" in captured["prompt"]
    assert "and what time?" in captured["prompt"]


def test_retrieve_needed_false_is_passed_through(monkeypatch):
    _patch_generate(
        monkeypatch,
        json.dumps(
            {
                **CLEAN,
                "action_type": "conversation",
                "retrieve_needed": False,
                "search_query": None,
                "response": "Hey! How's it going?",
            }
        ),
    )
    out = RetrievalAgent().reason("hey there")
    assert out.retrieve_needed is False
    assert out.response == "Hey! How's it going?"
