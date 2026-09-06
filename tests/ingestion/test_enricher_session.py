"""
Tests for the session ChunkEnricher (src/ingestion/enricher.py) — Phase 1
Step 1.2. `integration`: token_count loads the real tokenizer.
"""

import pytest

from src.ingestion.chunker import Chunk
from src.ingestion.enricher import ChunkEnricher, EnrichedChunk, ValidationReport

pytestmark = pytest.mark.integration


def _chunk(**kw) -> Chunk:
    base = dict(
        chunk_id="s1::primary",
        content="[User]: hi\n[Assistant]: hello",
        chunk_type="primary",
        token_count=0,
        parent_chunk_id="",
        session_id="s1",
        message_roles=["user", "assistant"],
        message_indices=[0, 1],
        topics=["greetings"],
        action_types=[],
        entities=[],
        sentiment="positive",
        timestamp="2026-09-02T10:00:00Z",
    )
    base.update(kw)
    return Chunk(**base)


@pytest.fixture
def enricher():
    return ChunkEnricher()


def test_source_prefix_is_session_shaped(enricher):
    (ec,) = enricher.enrich([_chunk()])
    assert isinstance(ec, EnrichedChunk)
    assert ec.source_prefix == "[Session: s1 | 2026-09-02T10:00:00Z | Topic: greetings]"
    assert ec.content.startswith(ec.source_prefix + "\n")
    assert ec.raw_content == "[User]: hi\n[Assistant]: hello"  # unprefixed
    assert ec.token_count > 0


def test_no_topics_falls_back_to_general(enricher):
    (ec,) = enricher.enrich([_chunk(topics=[])])
    assert "Topic: general]" in ec.source_prefix


def test_empty_timestamp_yields_unknown_not_a_blank_field(enricher):
    """Phase 1 audit 1.2-F2: an empty timestamp must not produce
    '[Session: s1 |  | Topic: …]' (double space, empty middle field)."""
    (ec,) = enricher.enrich([_chunk(timestamp="")])
    assert ec.source_prefix == "[Session: s1 | unknown | Topic: greetings]"
    assert " |  | " not in ec.source_prefix


def test_to_metadata_is_session_shaped(enricher):
    (ec,) = enricher.enrich([_chunk()])
    md = ChunkEnricher.to_metadata(ec)
    assert set(md) == {
        "chunk_id",
        "content",
        "raw_content",
        "session_id",
        "chunk_type",
        "parent_chunk_id",
        "token_count",
        "source_prefix",
        "topics",
        "action_types",
        "entities",
        "sentiment",
        "message_roles",
        "message_indices",
        "timestamp",
        "enrichment_version",
        "metadata_version",
    }
    for doc_key in ("course_name", "chapter_title", "page_start", "filename", "slide"):
        assert doc_key not in md


def test_validation_report_still_produced(enricher):
    enricher.enrich([_chunk()])
    report = enricher.get_last_validation_report()
    assert isinstance(report, ValidationReport)
    assert report.valid is True and report.total_chunks == 1

    bad = enricher.get_validation_report(enricher.enrich([_chunk(session_id="", chunk_id="")]))
    assert bad.valid is False and bad.errors


def test_enrichment_summary(enricher):
    enriched = enricher.enrich(
        [
            _chunk(),
            _chunk(chunk_id="s1::sub::0-9", chunk_type="sub_chunk", parent_chunk_id="s1::primary"),
        ]
    )
    summary = enricher.get_enrichment_summary(enriched)
    assert summary["primary_chunks"] == 1 and summary["sub_chunks"] == 1
    assert summary["unique_topics"] == ["greetings"]
