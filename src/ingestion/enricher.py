"""
Chunk enrichment for the session ingestion pipeline (Phase 1 Step 1.2).

Each chunk gets a short source-traceability prefix
``[Session: <id> | <approx timestamp> | Topic: <primary topic>]`` prepended
to a COPY of its text that is used only as the embedding input. The prefix is
never persisted — ``session_chunks.content`` stores the raw session text
(``docs/schema_review.md`` §4).
"""

from dataclasses import dataclass, field
from typing import Any

from src.ingestion.chunker import Chunk
from src.ingestion.tokenizer import count_tokens

ENRICHMENT_VERSION = "2.0"
METADATA_VERSION = "2.0"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
DEFAULT_MAX_PREFIX_LENGTH = 200


@dataclass
class EnricherConfig:
    add_source_prefix: bool = True
    validate_chunks: bool = True
    max_prefix_length: int = DEFAULT_MAX_PREFIX_LENGTH


@dataclass
class ValidationReport:
    valid: bool
    total_chunks: int
    errors: list[str]
    warnings: list[str]


@dataclass
class EnrichedChunk:
    """A chunk with its embedding-input text and session metadata, ready to embed."""

    chunk_id: str
    content: str  # raw session text + source prefix (embedding input only)
    raw_content: str  # the persisted session text (no prefix)
    chunk_type: str
    parent_chunk_id: str
    token_count: int  # tokens of `content`

    session_id: str
    message_roles: list[str] = field(default_factory=list)
    message_indices: list[int] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    action_types: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    sentiment: str = ""
    timestamp: str = ""

    source_prefix: str | None = None
    enrichment_version: str = ENRICHMENT_VERSION
    metadata_version: str = METADATA_VERSION

    # Populated by EmbeddingGenerator.
    embedding_ready: bool = False
    embedding_model: str = EMBEDDING_MODEL
    embedding_dimensions: int | None = None
    embedding_version: str | None = None


class ChunkEnricher:
    """Adds source-prefix enrichment and validates chunk completeness."""

    def __init__(self, config: EnricherConfig | None = None):
        self.config = config or EnricherConfig()
        self._last_validation_report: ValidationReport | None = None

    def enrich(self, chunks: list[Chunk]) -> list[EnrichedChunk]:
        enriched = [self._enrich_single(c) for c in chunks]
        if self.config.validate_chunks:
            self._last_validation_report = self.get_validation_report(enriched)
        return enriched

    def _enrich_single(self, chunk: Chunk) -> EnrichedChunk:
        prefix = self._build_source_prefix(chunk) if self.config.add_source_prefix else None
        content = f"{prefix}\n{chunk.content}" if prefix else chunk.content
        return EnrichedChunk(
            chunk_id=chunk.chunk_id,
            content=content,
            raw_content=chunk.content,
            chunk_type=chunk.chunk_type,
            parent_chunk_id=chunk.parent_chunk_id,
            token_count=count_tokens(content),
            session_id=chunk.session_id,
            message_roles=list(chunk.message_roles),
            message_indices=list(chunk.message_indices),
            topics=list(chunk.topics),
            action_types=list(chunk.action_types),
            entities=list(chunk.entities),
            sentiment=chunk.sentiment,
            timestamp=chunk.timestamp,
            source_prefix=prefix,
        )

    def _build_source_prefix(self, chunk: Chunk) -> str:
        topic = chunk.topics[0] if chunk.topics else "general"
        topic = self._truncate(topic, self.config.max_prefix_length // 2)
        prefix = f"[Session: {chunk.session_id} | {chunk.timestamp} | Topic: {topic}]"
        if len(prefix) > self.config.max_prefix_length:
            prefix = prefix[: self.config.max_prefix_length - 4] + "...]"
        return prefix

    @staticmethod
    def _truncate(text: str, max_length: int) -> str:
        return text if len(text) <= max_length else text[: max_length - 3] + "..."

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def get_validation_report(self, enriched: list[EnrichedChunk]) -> ValidationReport:
        if not enriched:
            return ValidationReport(False, 0, ["No chunks to validate"], [])

        errors: list[str] = []
        warnings: list[str] = []
        for c in enriched:
            if not c.chunk_id:
                errors.append("Missing chunk_id")
            if not c.session_id:
                errors.append(f"{c.chunk_id}: missing session_id")
            if not c.raw_content or len(c.raw_content.strip()) < 1:
                errors.append(f"{c.chunk_id}: empty content")
            if c.token_count == 0:
                warnings.append(f"{c.chunk_id}: token count is 0")
            if not c.topics:
                warnings.append(f"{c.chunk_id}: no topics extracted")
            if self.config.add_source_prefix and not c.source_prefix:
                errors.append(f"{c.chunk_id}: missing source prefix")

        return ValidationReport(len(errors) == 0, len(enriched), errors, warnings)

    def get_last_validation_report(self) -> ValidationReport | None:
        return self._last_validation_report

    def get_enrichment_summary(self, enriched: list[EnrichedChunk]) -> dict[str, Any]:
        if not enriched:
            return {"total_chunks": 0}
        total_tokens = sum(c.token_count for c in enriched)
        return {
            "total_chunks": len(enriched),
            "primary_chunks": sum(1 for c in enriched if c.chunk_type == "primary"),
            "sub_chunks": sum(1 for c in enriched if c.chunk_type == "sub_chunk"),
            "total_tokens": total_tokens,
            "avg_tokens": total_tokens / len(enriched),
            "unique_topics": sorted({t for c in enriched for t in c.topics}),
            "sessions": sorted({c.session_id for c in enriched}),
            "enrichment_version": ENRICHMENT_VERSION,
            "embedding_model": EMBEDDING_MODEL,
        }

    @staticmethod
    def to_metadata(chunk: EnrichedChunk) -> dict[str, Any]:
        """Session-shaped metadata dict (consumed by EmbeddedChunk.metadata)."""
        return {
            "chunk_id": chunk.chunk_id,
            "content": chunk.content,
            "raw_content": chunk.raw_content,
            "session_id": chunk.session_id,
            "chunk_type": chunk.chunk_type,
            "parent_chunk_id": chunk.parent_chunk_id,
            "token_count": chunk.token_count,
            "source_prefix": chunk.source_prefix,
            "topics": chunk.topics,
            "action_types": chunk.action_types,
            "entities": chunk.entities,
            "sentiment": chunk.sentiment,
            "message_roles": chunk.message_roles,
            "message_indices": chunk.message_indices,
            "timestamp": chunk.timestamp,
            "enrichment_version": chunk.enrichment_version,
            "metadata_version": chunk.metadata_version,
        }
