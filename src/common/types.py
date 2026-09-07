"""
Shared data contracts used by both the retrieval and generation layers.

The document-era ``RetrievedChunk`` / ``CitationLocationType`` were deleted in
Phase 1 Step 1.4a once generation migrated to ``SessionRetrievedChunk`` /
``SessionCitationFormat``. See docs/schema_review.md for the cross-pipeline
review these session-era contracts were frozen against.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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


# ============================================================================
# Feature-handler entity contracts (Production Roadmap Phase 1 Step 1.5)
#
# Plain dataclasses that hydrate a row from the structured tables in
# db/migrations/0001_initial_schema.sql — ``reminders`` / ``todos`` /
# ``meeting_notes`` / ``schedule_items`` / ``summaries``. Fields mirror the
# writable base-table columns; ``deleted_at`` / ``sync_metadata`` are not
# surfaced (soft-delete is internal to the handler, sync is inert in v1).
# ============================================================================


@dataclass
class ActionItem:
    """One row of ``meeting_notes.action_items`` (a JSON array of these)."""

    task: str
    owner: str | None = None
    deadline: str | None = None


@dataclass
class Reminder:
    """A ``reminders`` row. ``scheduled_time`` is the intended fire time;
    ``fired_at`` / ``completed_at`` / ``dismissed_at`` track the lifecycle the
    on-launch reconciliation and the scheduler thread (Step 1.5b) read."""

    id: str
    title: str
    scheduled_time: str
    session_id: str | None = None
    notes: str = ""
    fired_at: str | None = None
    completed_at: str | None = None
    dismissed_at: str | None = None
    toast_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Todo:
    """A ``todos`` row. ``priority`` is ``"low"`` / ``"medium"`` / ``"high"``
    or ``None`` (the table's CHECK constraint). Completed todos are retained
    with ``completed_at`` set — never deleted."""

    id: str
    title: str
    session_id: str | None = None
    notes: str = ""
    priority: str | None = None
    category: str | None = None
    completed_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class MeetingNote:
    """A ``meeting_notes`` row. The JSON-array columns are hydrated to typed
    lists; ``searchable_text`` is the flat text the application layer writes
    for FTS5 (docs/schema_review.md §6). ``needs_review`` is set when neither
    ``decisions`` nor ``action_items`` came back from extraction."""

    id: str
    raw_transcript: str
    session_id: str | None = None
    attendees: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    follow_ups: list[str] = field(default_factory=list)
    needs_review: bool = False
    searchable_text: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ScheduleItem:
    """A ``schedule_items`` row. Belongs to a ``schedules`` row (one per
    date), created on demand by ``ScheduleHandler``."""

    id: str
    schedule_id: str
    title: str
    start_time: str
    end_time: str
    location: str = ""
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Summary:
    """A ``summaries`` row. ``summary_type`` is ``"daily"`` or ``"weekly"``.
    ``scheduled_at`` is the intended generation time (kept stable across
    regeneration); ``generated_at`` is when it was actually produced."""

    id: str
    summary_type: str
    period_start: str
    period_end: str
    content: str
    scheduled_at: str
    generated_at: str
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ScheduleConflict:
    """Returned (not raised) by ``ScheduleHandler`` when a new/updated item
    would overlap an existing commitment — the caller decides how to resolve
    it (the handler never silently overwrites, per project_logic.md §5)."""

    attempted: ScheduleItem
    conflicts_with: list[ScheduleItem] = field(default_factory=list)


@dataclass
class ReconciliationResult:
    """``ReminderHandler.reconcile_on_launch()`` — two independent lists for
    two independent failure modes (project_logic.md §9):

    - ``overdue``: ``scheduled_time`` passed but the reminder never fired (OS
      dropped the toast, Focus Assist, a reboot).
    - ``pending_acknowledgment``: it fired, but the app was closed when the
      user tapped the toast, so it was never completed or dismissed.
    """

    overdue: list[Reminder] = field(default_factory=list)
    pending_acknowledgment: list[Reminder] = field(default_factory=list)


# ============================================================================
# Backup / restore / export contracts (Production Roadmap Phase 2 Step 2.2)
# ============================================================================


@dataclass(frozen=True)
class BackupSnapshot:
    """One rolling daily backup file produced by ``BackupManager`` — a
    SQLCipher-encrypted single-file copy of the session database."""

    path: Path
    created_at: str  # ISO 8601 UTC, parsed from the filename timestamp
    size_bytes: int


@dataclass(frozen=True)
class RestoreResult:
    """Outcome of ``BackupManager.restore()`` / ``stage_restore()``.
    ``needs_restart`` is the seam the Phase 3 process supervisor acts on — the
    backend cannot restart itself.

    ``validated_snapshot_path`` is set only by ``stage_restore()`` (validate,
    do not swap): it is the absolute path of the snapshot the Rust supervisor
    should ``fs::rename`` into place once the backend is fully down (Phase 3
    Step 3.1 — resolves ``PHASE_2_AUDIT.md`` 2.3-C1). ``restore()`` (the
    Python-side swap) leaves it ``None``."""

    ok: bool
    needs_restart: bool
    detail: str
    validated_snapshot_path: str | None = None


@dataclass(frozen=True)
class ExportBadgeState:
    """``DataManager.export_badge_state()`` — everything the Settings nav
    badge and the "Last exported:" line need (project_logic.md §12). The
    frontend renders it; the backend computes it."""

    needs_export: bool  # last export is missing or older than 30 days
    last_exported_at: str | None
    days_since: int | None
    settings_line: str
