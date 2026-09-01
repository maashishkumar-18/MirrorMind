"""
Tests for AgenticOutput (src/common/types.py), the Pydantic model for the
structured JSON output of the single agentic-reasoning Ollama call
(Production Roadmap Phase 0 Step 0.4).

Pydantic, deliberately -- this validates untrusted LLM JSON and must
reject malformed input, not just hold it (unlike SessionRetrievedChunk,
a plain dataclass -- see test_types_session.py).
"""

import pytest
from pydantic import ValidationError

from src.common.types import AgenticActionType, AgenticOutput, RetrievalRoute

pytestmark = pytest.mark.characterization

VALID = {
    "action_type": "reminder",
    "confidence": 0.9,
    "retrieve_needed": False,
    "retrieval_route": "semantic",
    "search_query": None,
    "response": "I've set a reminder for tomorrow at 3pm.",
}


class TestValidInput:
    def test_accepts_a_well_formed_payload(self):
        output = AgenticOutput.model_validate(VALID)
        assert output.action_type == AgenticActionType.REMINDER
        assert output.confidence == 0.9
        assert output.retrieval_route == RetrievalRoute.SEMANTIC

    @pytest.mark.parametrize(
        "action_type",
        [
            "conversation",
            "reminder",
            "todo",
            "meeting_note",
            "schedule",
            "summary_request",
            "retrieval_query",
            "none",
        ],
    )
    def test_accepts_every_documented_action_type(self, action_type):
        output = AgenticOutput.model_validate({**VALID, "action_type": action_type})
        assert output.action_type.value == action_type

    @pytest.mark.parametrize("route", ["semantic", "structured", "hybrid"])
    def test_accepts_every_documented_retrieval_route(self, route):
        output = AgenticOutput.model_validate({**VALID, "retrieval_route": route})
        assert output.retrieval_route.value == route

    def test_search_query_none_is_valid(self):
        output = AgenticOutput.model_validate({**VALID, "search_query": None})
        assert output.search_query is None

    def test_search_query_can_be_a_string(self):
        output = AgenticOutput.model_validate({**VALID, "search_query": "last week's meeting"})
        assert output.search_query == "last week's meeting"

    def test_search_query_omitted_defaults_to_none(self):
        payload = {k: v for k, v in VALID.items() if k != "search_query"}
        output = AgenticOutput.model_validate(payload)
        assert output.search_query is None

    @pytest.mark.parametrize("confidence", [0.0, 1.0, 0.5])
    def test_confidence_boundary_and_midpoint_values_are_valid(self, confidence):
        output = AgenticOutput.model_validate({**VALID, "confidence": confidence})
        assert output.confidence == confidence


class TestInvalidInput:
    def test_rejects_unknown_action_type(self):
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate({**VALID, "action_type": "not_a_real_type"})

    def test_rejects_unknown_retrieval_route(self):
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate({**VALID, "retrieval_route": "not_a_real_route"})

    def test_rejects_confidence_above_one(self):
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate({**VALID, "confidence": 1.5})

    def test_rejects_confidence_below_zero(self):
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate({**VALID, "confidence": -0.1})

    def test_rejects_missing_response(self):
        payload = {k: v for k, v in VALID.items() if k != "response"}
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate(payload)

    def test_rejects_missing_action_type(self):
        payload = {k: v for k, v in VALID.items() if k != "action_type"}
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate(payload)

    def test_rejects_missing_retrieve_needed(self):
        payload = {k: v for k, v in VALID.items() if k != "retrieve_needed"}
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate(payload)

    def test_rejects_missing_retrieval_route(self):
        payload = {k: v for k, v in VALID.items() if k != "retrieval_route"}
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate(payload)

    def test_rejects_non_boolean_retrieve_needed_that_cannot_coerce(self):
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate({**VALID, "retrieve_needed": "not a bool at all"})

    def test_rejects_completely_malformed_payload(self):
        with pytest.raises(ValidationError):
            AgenticOutput.model_validate({"not": "an agentic output"})
