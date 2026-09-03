"""
Unit tests for the session ContextBuilder (src/retrieval/context_builder.py)
— Phase 1 Step 1.3c. Pure assembly over synthetic SessionRetrievedChunk
lists; no DB, no model.
"""

import pytest

from src.common.types import SessionRetrievedChunk
from src.retrieval.context_builder import AssembledContext, ContextBuilder, ContextBuilderConfig

pytestmark = pytest.mark.unit


def _chunk(chunk_id="c1", *, session_id="s1", content="hello world.", **kw):
    return SessionRetrievedChunk(
        chunk_id=chunk_id,
        session_id=session_id,
        content=content,
        raw_content=content,
        timestamp=kw.get("timestamp", "2026-09-01T10:00:00Z"),
        chunk_type=kw.get("chunk_type", "primary"),
        score=kw.get("score", 0.9),
        source_prefix=kw.get("source_prefix", ""),
        metadata=kw.get("metadata", {}),
    )


def test_empty_input_returns_empty_context():
    result = ContextBuilder().build([])
    assert isinstance(result, AssembledContext)
    assert result.formatted_context == ""
    assert (result.chunks_used, result.tokens_used, result.truncated) == (0, 0, False)


def test_session_citation_format():
    result = ContextBuilder().build([_chunk(session_id="s42", timestamp="2026-08-15T09:30:00Z")])
    assert result.citations == ["[Session s42 · approx. 2026-08-15T09:30:00Z]"]
    assert "[Session s42 · approx. 2026-08-15T09:30:00Z]" in result.formatted_context
    assert result.chunks_used == 1


def test_structured_record_citation_format():
    sc = _chunk(
        "reminders:r1",
        session_id="",
        chunk_type="structured_record",
        content="Call the dentist",
        metadata={"table": "reminders", "record_id": "r1"},
    )
    result = ContextBuilder().build([sc])
    assert result.citations == ["[reminders record · approx. 2026-09-01T10:00:00Z]"]
    assert "Call the dentist" in result.formatted_context


def test_max_chunks_caps_the_number_assembled():
    cfg = ContextBuilderConfig(max_chunks=2, max_total_tokens=10_000)
    chunks = [_chunk(f"c{i}", content=f"chunk number {i}.") for i in range(5)]
    result = ContextBuilder(cfg).build(chunks)
    assert result.chunks_used == 2
    assert result.truncated is True


def test_token_budget_truncates_and_flags():
    cfg = ContextBuilderConfig(max_total_tokens=25, max_chunks=10, truncate_at_sentence=True)
    long = "This is sentence one. This is sentence two. This is sentence three. " * 3
    builder = ContextBuilder(cfg)
    chunks = [_chunk("a", content=long), _chunk("b", content=long)]
    result = builder.build(chunks)
    assert result.truncated is True
    assert result.formatted_context.endswith(cfg.truncation_marker)
    assert result.chunks_used < 2  # the second chunk did not fully fit
    body = result.formatted_context[: -len(cfg.truncation_marker)]
    assert builder.count_tokens(body) <= cfg.max_total_tokens


def test_no_truncation_when_everything_fits():
    cfg = ContextBuilderConfig(max_total_tokens=10_000, max_chunks=10)
    result = ContextBuilder(cfg).build(
        [_chunk("a", content="short one."), _chunk("b", content="short two.")]
    )
    assert result.truncated is False
    assert cfg.truncation_marker not in result.formatted_context
    assert result.chunks_used == 2


def test_build_uses_chunk_content_verbatim_not_source_prefix():
    # content and source_prefix are separate fields on SessionRetrievedChunk;
    # the builder must emit content as-is and never inject the derived prefix.
    sentinel = "ZZZ_DERIVED_PREFIX_SENTINEL_ZZZ"
    c = _chunk("c1", content="the actual conversation text.", source_prefix=sentinel)
    result = ContextBuilder().build([c])
    assert "the actual conversation text." in result.formatted_context
    assert sentinel not in result.formatted_context


def test_output_has_no_document_era_vocabulary():
    chunks = [
        _chunk("c1", content="we talked about the budget."),
        _chunk(
            "r1", chunk_type="structured_record", content="buy milk", metadata={"table": "todos"}
        ),
    ]
    text = ContextBuilder().build(chunks).formatted_context.lower()
    for word in ("course", "chapter", "slide", "page "):
        assert word not in text


def test_citations_disabled_omits_citation_lines():
    cfg = ContextBuilderConfig(citations_enabled=False)
    result = ContextBuilder(cfg).build([_chunk(content="just the body.")])
    assert result.citations == [""]
    assert "[Session" not in result.formatted_context
    assert "just the body." in result.formatted_context


def test_chunks_backing_data_is_returned_in_display_order():
    chunks = [_chunk("a", content="first."), _chunk("b", content="second.")]
    result = ContextBuilder(ContextBuilderConfig(max_total_tokens=10_000)).build(chunks)
    assert [c.chunk_id for c in result.chunks] == ["a", "b"]
    assert all(isinstance(c, SessionRetrievedChunk) for c in result.chunks)
