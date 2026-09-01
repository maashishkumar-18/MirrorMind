"""
Tests for the session-era dataclasses in src/common/types.py (Production
Roadmap Phase 0 Step 0.4): SessionRetrievedChunk, SessionCitationFormat.

These are plain dataclasses, not Pydantic models -- see
test_agentic_output.py for AgenticOutput, which is Pydantic deliberately
because it validates untrusted LLM JSON.
"""

import dataclasses

import pytest

from src.common.types import (
    CitationLocationType,
    RetrievedChunk,
    SessionCitationFormat,
    SessionRetrievedChunk,
)

pytestmark = pytest.mark.characterization


class TestSessionCitationFormat:
    def test_constructs_with_required_fields(self):
        citation = SessionCitationFormat(
            session_id="s1", approximate_timestamp="2026-01-01T00:00:00Z"
        )
        assert citation.session_id == "s1"
        assert citation.approximate_timestamp == "2026-01-01T00:00:00Z"

    def test_round_trips_through_asdict(self):
        citation = SessionCitationFormat(
            session_id="s1", approximate_timestamp="2026-01-01T00:00:00Z"
        )
        as_dict = dataclasses.asdict(citation)
        assert as_dict == {"session_id": "s1", "approximate_timestamp": "2026-01-01T00:00:00Z"}


class TestSessionRetrievedChunk:
    def test_constructs_with_only_required_fields(self):
        chunk = SessionRetrievedChunk(
            chunk_id="c1", session_id="s1", content="hello", raw_content="hello"
        )
        assert chunk.chunk_id == "c1"
        assert chunk.session_id == "s1"

    def test_defaults_match_the_frozen_design(self):
        chunk = SessionRetrievedChunk(
            chunk_id="c1", session_id="s1", content="hello", raw_content="hello"
        )
        assert chunk.topics == []
        assert chunk.action_types == []
        assert chunk.entities == []
        assert chunk.sentiment == ""
        assert chunk.timestamp == ""
        assert chunk.message_roles == []
        assert chunk.chunk_type == "primary"
        assert chunk.parent_chunk_id is None
        assert chunk.token_count == 0
        assert chunk.score == 0.0
        assert chunk.semantic_score is None
        assert chunk.keyword_score is None
        assert chunk.metadata_score is None
        assert chunk.source_prefix == ""
        assert chunk.metadata == {}

    def test_default_mutable_fields_are_independent_per_instance(self):
        """A classic dataclass footgun -- confirms field(default_factory=list)
        is used correctly, not a shared mutable default."""
        chunk_a = SessionRetrievedChunk(chunk_id="a", session_id="s", content="x", raw_content="x")
        chunk_b = SessionRetrievedChunk(chunk_id="b", session_id="s", content="y", raw_content="y")
        chunk_a.topics.append("reminder")
        assert chunk_b.topics == []

    def test_round_trips_through_asdict(self):
        chunk = SessionRetrievedChunk(
            chunk_id="c1",
            session_id="s1",
            content="hello",
            raw_content="hello",
            topics=["reminder"],
            chunk_type="sub_chunk",
            parent_chunk_id="primary-1",
            score=0.9,
        )
        as_dict = dataclasses.asdict(chunk)
        assert as_dict["chunk_id"] == "c1"
        assert as_dict["topics"] == ["reminder"]
        assert as_dict["chunk_type"] == "sub_chunk"
        assert as_dict["parent_chunk_id"] == "primary-1"

    def test_chunk_type_accepts_all_three_documented_values(self):
        for chunk_type in ("primary", "sub_chunk", "structured_record"):
            chunk = SessionRetrievedChunk(
                chunk_id="c",
                session_id="s",
                content="x",
                raw_content="x",
                chunk_type=chunk_type,
            )
            assert chunk.chunk_type == chunk_type


class TestOldRetrievedChunkUntouched:
    """The existing RetrievedChunk/CitationLocationType must remain exactly
    as they were -- Step 0.4 introduces new types alongside them, it does
    not modify or replace them yet (that's Phase 1 Step 1.4)."""

    def test_retrieved_chunk_still_constructs_with_document_era_fields(self):
        chunk = RetrievedChunk(
            chunk_id="c1",
            content="text",
            score=0.9,
            course_name="CS101",
            location_type=CitationLocationType.SLIDE,
        )
        assert chunk.course_name == "CS101"
        assert chunk.location_type == CitationLocationType.SLIDE

    def test_citation_location_type_still_has_all_document_era_variants(self):
        assert {e.value for e in CitationLocationType} == {
            "page",
            "slide",
            "section",
            "chapter",
            "unknown",
        }
