"""
Shared data contracts used by both the retrieval and generation layers.

The document-era ``RetrievedChunk`` / ``CitationLocationType`` were deleted in
Phase 1 Step 1.4a once generation migrated to ``SessionRetrievedChunk`` /
``SessionCitationFormat``. See docs/schema_review.md for the cross-pipeline
review these session-era contracts were frozen against.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

# ============================================================================
# Session-era types (Production Roadmap Phase 0 Step 0.4)
# ============================================================================


@dataclass
class SessionCitationFormat:
    """
    Replaces CitationLocationType for session-era citations.

    No PAGE, SLIDE, SECTION, or CHAPTER — a session id and an approximate
    timestamp are the only location concepts that exist once source
    material is conversational rather than document-shaped.
    """

    session_id: str
    approximate_timestamp: str  # ISO 8601


@dataclass
class SessionRetrievedChunk:
    """
    Session-era counterpart to RetrievedChunk.

    Persisted vs. derived-at-query-time fields are documented in
    docs/schema_review.md's field-by-field consumer map — in short:
    topics/action_types/entities/sentiment/message_roles are columns on
    `session_chunks` (db/migrations/0001_initial_schema.sql); timestamp,
    source_prefix, and parent_chunk_id are computed by the retrieval layer
    at query time, not stored as raw columns.
    """

    chunk_id: str
    session_id: str
    content: str
    raw_content: str
    topics: list[str] = field(default_factory=list)
    action_types: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    sentiment: str = ""
    timestamp: str = ""  # approximate ISO 8601 — derived, see docs/schema_review.md
    message_roles: list[str] = field(default_factory=list)
    chunk_type: str = "primary"  # "primary" | "sub_chunk" | "structured_record"
    parent_chunk_id: str | None = None  # derived at query time, see docs/schema_review.md
    token_count: int = 0
    score: float = 0.0
    semantic_score: float | None = None
    keyword_score: float | None = None
    metadata_score: float | None = None
    source_prefix: str = ""  # derived at query time, see docs/schema_review.md
    metadata: dict[str, Any] = field(default_factory=dict)


class AgenticActionType(str, Enum):
    """What the user's message is classified as, by the single agentic
    reasoning LLM call (project_logic.md §3)."""

    CONVERSATION = "conversation"
    REMINDER = "reminder"
    TODO = "todo"
    MEETING_NOTE = "meeting_note"
    SCHEDULE = "schedule"
    SUMMARY_REQUEST = "summary_request"
    RETRIEVAL_QUERY = "retrieval_query"
    NONE = "none"


class RetrievalRoute(str, Enum):
    """Which path the Retrieval Router dispatches to (project_logic.md §6)."""

    SEMANTIC = "semantic"
    STRUCTURED = "structured"
    HYBRID = "hybrid"


class AgenticOutput(BaseModel):
    """
    Structured JSON output of the single agentic-reasoning Ollama call.

    The interface between the agentic reasoning layer and every
    downstream component (Retrieval Router, generation). Pydantic (not a
    dataclass, unlike SessionRetrievedChunk above) deliberately — this
    validates untrusted LLM output and must reject malformed input, not
    just hold it.
    """

    action_type: AgenticActionType
    confidence: float = Field(ge=0.0, le=1.0)
    retrieve_needed: bool
    retrieval_route: RetrievalRoute
    search_query: str | None = None
    response: str


# ============================================================================
# Local vector-store contracts (Production Roadmap Phase 1 Step 1.1)
#
# The write-side record and the two diagnostic result types for
# VectorStoreInterface (src/common/vector_store.py). Introduced here, in the
# shared-contracts module, so that neither the ingestion pipeline (writes) nor
# the retrieval pipeline (reads) has to import the other — or the concrete
# SQLiteVectorStore — to name these types.
# ============================================================================


@dataclass
class SessionChunkRecord:
    """
    A chunk to be written to the vector store via
    ``VectorStoreInterface.upsert()``.

    Fields mirror the writable columns of ``session_chunks``
    (db/migrations/0001_initial_schema.sql). ``chunk_id`` maps to the
    ``id`` column; ``window_start_message_idx`` / ``window_end_message_idx``
    are ``None`` for primary chunks (which cover the whole session).
    ``embedding`` is a 1-D float32 array of length 384 (all-MiniLM-L6-v2);
    the store serializes it to the ``embedding`` BLOB as a raw float32 buffer.
    """

    chunk_id: str
    session_id: str
    content: str
    embedding: np.ndarray
    token_count: int
    chunk_type: str = "primary"  # "primary" | "sub_chunk"
    window_start_message_idx: int | None = None
    window_end_message_idx: int | None = None
    topics: list[str] = field(default_factory=list)
    action_types: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    message_roles: list[str] = field(default_factory=list)
    sentiment: str = ""


@dataclass
class UpsertResult:
    """Outcome of a single ``VectorStoreInterface.upsert()`` call."""

    chunk_id: str
    operation: str  # "inserted" | "updated"
    total_chunks_in_store: int


@dataclass
class StoreStats:
    """Snapshot of the vector store's contents (``get_stats()``)."""

    total_chunks: int
    total_sessions: int
    embedding_dim: int
    primary_chunks: int
    sub_chunks: int


@dataclass
class SessionMessage:
    """
    One turn of a conversation session — the input unit for the ingestion
    pipeline (Production Roadmap Phase 1 Step 1.2) and the feature handlers
    (Step 1.5).

    ``role`` is ``"user"`` or ``"assistant"`` (mirrors ``messages.role`` in
    db/migrations/0001_initial_schema.sql). A message's turn index is its
    position in the ``list[SessionMessage]`` passed to the pipeline, not a
    field here.
    """

    role: str
    content: str
