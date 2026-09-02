"""
Tests for the session SessionChunker (src/ingestion/chunker.py) — Phase 1
Step 1.2. Written against docs/schema_review.md §3 (the frozen sliding-window
spec), not the other way round.

`integration`: `token_count` loads the real all-MiniLM-L6-v2 tokenizer.
"""

import pytest

from src.common.types import SessionMessage
from src.ingestion.chunker import (
    Chunk,
    SessionChunker,
    SessionChunkerConfig,
    sub_chunk_windows,
)
from src.ingestion.metadata_extractor import SessionMetadata

pytestmark = pytest.mark.integration

META = SessionMetadata(
    session_id="s1",
    timestamp="2026-09-02T10:00:00Z",
    topics=["planning"],
    action_types=["todo"],
    entities=["Priya"],
    sentiment="neutral",
)


def _messages(n: int) -> list[SessionMessage]:
    return [
        SessionMessage("user" if i % 2 == 0 else "assistant", f"message number {i}")
        for i in range(n)
    ]


@pytest.fixture
def chunker():
    return SessionChunker(SessionChunkerConfig())  # defaults: W=10 S=5 threshold=20


# --- pure algorithm (no model) -------------------------------------------


@pytest.mark.unit
def test_sub_chunk_windows_matches_frozen_spec():
    assert sub_chunk_windows(25, 10, 5) == [(0, 9), (5, 14), (10, 19), (15, 24)]
    assert sub_chunk_windows(22, 10, 5) == [(0, 9), (5, 14), (10, 19)]  # trailing drop
    assert sub_chunk_windows(20, 10, 5) == [(0, 9), (5, 14), (10, 19)]
    assert sub_chunk_windows(9, 10, 5) == []


# --- chunking behaviour -------------------------------------------------


def test_ten_message_session_is_primary_only(chunker):
    chunks = chunker.chunk("s1", _messages(10), META, META.timestamp)
    assert len(chunks) == 1
    (primary,) = chunks
    assert isinstance(primary, Chunk)
    assert primary.chunk_id == "s1::primary"
    assert primary.chunk_type == "primary"
    assert primary.parent_chunk_id == ""
    assert primary.message_indices == list(range(10))
    assert primary.message_roles[0] == "user" and primary.message_roles[1] == "assistant"
    assert primary.topics == ["planning"] and primary.action_types == ["todo"]
    assert primary.token_count > 0
    assert primary.content.startswith("[User]: message number 0")


def test_exactly_threshold_is_primary_only(chunker):
    chunks = chunker.chunk("s1", _messages(20), META, META.timestamp)
    assert [c.chunk_type for c in chunks] == ["primary"]


def test_25_message_session_primary_plus_four_sub_chunks(chunker):
    chunks = chunker.chunk("s1", _messages(25), META, META.timestamp)
    assert chunks[0].chunk_type == "primary"
    subs = chunks[1:]
    assert [c.chunk_id for c in subs] == [
        "s1::sub::0-9",
        "s1::sub::5-14",
        "s1::sub::10-19",
        "s1::sub::15-24",
    ]
    assert [c.message_indices for c in subs] == [
        list(range(0, 10)),
        list(range(5, 15)),
        list(range(10, 20)),
        list(range(15, 25)),
    ]
    assert all(c.chunk_type == "sub_chunk" for c in subs)
    assert all(c.parent_chunk_id == "s1::primary" for c in subs)
    assert all(c.token_count > 0 for c in subs)
    # overlap between consecutive windows is exactly `stride` (=5) messages
    for a, b in zip(subs, subs[1:], strict=False):
        assert len(set(a.message_indices) & set(b.message_indices)) == 5
    # primary still covers the whole session including the tail
    assert chunks[0].message_indices == list(range(25))


def test_22_message_session_drops_the_trailing_window(chunker):
    chunks = chunker.chunk("s1", _messages(22), META, META.timestamp)
    sub_ids = [c.chunk_id for c in chunks if c.chunk_type == "sub_chunk"]
    assert sub_ids == ["s1::sub::0-9", "s1::sub::5-14", "s1::sub::10-19"]
    assert "s1::sub::15-24" not in sub_ids  # would be undersized (15+10=25 > 22)


def test_sub_chunk_content_is_only_its_window(chunker):
    chunks = chunker.chunk("s1", _messages(25), META, META.timestamp)
    second = chunks[2]  # s1::sub::5-14
    assert second.content.startswith("[Assistant]: message number 5")
    assert "message number 4" not in second.content
    assert "message number 15" not in second.content


# --- config -----------------------------------------------------------


@pytest.mark.unit
def test_config_from_yaml_reads_defaults():
    cfg = SessionChunkerConfig.from_yaml()
    assert (cfg.window_size, cfg.stride, cfg.sub_chunk_threshold) == (10, 5, 20)
    assert cfg.max_chunk_tokens == 256


@pytest.mark.unit
def test_config_env_override(monkeypatch):
    monkeypatch.setenv("RAGPIPE_CHUNK_STRIDE", "7")
    monkeypatch.setenv("RAGPIPE_SUBCHUNK_THRESHOLD", "4")
    cfg = SessionChunkerConfig.from_yaml()
    assert cfg.stride == 7 and cfg.sub_chunk_threshold == 4
    assert sub_chunk_windows(12, cfg.window_size, cfg.stride) == [(0, 9)]
