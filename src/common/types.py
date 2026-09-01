"""
Shared data contracts used by both the retrieval and generation layers.

`RetrievedChunk` used to be defined twice — once in `src.retrieval.config`
and once in `src.generation.config` — with an application-layer adapter
function converting between the two on every request. Since both layers
represent the exact same concept (a chunk retrieved from the knowledge
base, carried through to generation), it's unified here as a single
source of truth. Both `src.retrieval.config` and `src.generation.config`
re-export it under the same name so existing imports keep working.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CitationLocationType(str, Enum):
    """Type of location in the source document."""

    PAGE = "page"
    SLIDE = "slide"
    SECTION = "section"
    CHAPTER = "chapter"
    UNKNOWN = "unknown"


@dataclass
class RetrievedChunk:
    """
    A single chunk retrieved from the knowledge base.

    Produced by the retrieval layer's ContextBuilder and consumed
    unchanged by the generation layer's GenerationRequest — no
    conversion step between the two.
    """

    chunk_id: str
    content: str  # Enriched content (with source prefix)
    score: float  # Final relevance score (post-rerank)

    # Source traceability
    course_name: str = ""
    chapter_title: str = ""
    topic: str = ""
    location_type: CitationLocationType = CitationLocationType.UNKNOWN
    location_start: int = 0
    location_end: int = 0
    filename: str = ""
    file_type: str = ""  # e.g. "pptx", "pdf" — determines location_type
    chunk_type: str = ""
    source_prefix: str = ""

    # Original content without source-prefix enrichment, when available
    raw_content: str = ""

    # Optional per-source component scores from hybrid search, when tracked
    semantic_score: float | None = None
    keyword_score: float | None = None
    metadata_score: float | None = None

    # Additional metadata (flexible for provider-specific data)
    metadata: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Session-era types (Production Roadmap Phase 0 Step 0.4)
#
# Introduced ALONGSIDE RetrievedChunk/CitationLocationType above, not
# replacing them yet. Retrieval migrates to SessionRetrievedChunk in
# Phase 1 Step 1.3; RetrievedChunk/CitationLocationType are deleted only
# in Phase 1 Step 1.4, once generation has migrated too. See
# docs/schema_review.md for the full cross-pipeline review this design
# was frozen against.
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
