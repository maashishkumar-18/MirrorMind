"""
Session ingestion pipeline (Phase 1 Step 1.2).

Turns a conversation transcript into ``session_chunks`` rows:

    clean -> extract session metadata -> chunk -> enrich -> embed -> upsert

The pipeline depends only on ``VectorStoreInterface`` — the concrete
``SQLiteVectorStore`` is injected by the caller (the application composition
root; tests pass a tmp-DB instance). Chunk ids are stable
(``{session_id}::primary`` / ``{session_id}::sub::{start}-{end}``) so
re-ingesting a session updates its rows in place.
"""

from dataclasses import dataclass, field

import numpy as np

from src.common.types import SessionChunkRecord, SessionMessage
from src.common.vector_store import VectorStoreInterface
from src.ingestion.chunker import SessionChunker
from src.ingestion.cleaner import TextCleaner
from src.ingestion.embedder import EmbeddedChunk, EmbeddingGenerator
from src.ingestion.enricher import ChunkEnricher
from src.ingestion.metadata_extractor import MetadataExtractor, SessionMetadata
from src.ingestion.tokenizer import count_tokens


@dataclass
class IngestionResult:
    session_id: str
    chunk_ids: list[str] = field(default_factory=list)
    primary_count: int = 0
    sub_chunk_count: int = 0
    failed_chunk_ids: list[str] = field(default_factory=list)
    metadata: SessionMetadata | None = None


class SessionIngestionPipeline:
    def __init__(
        self,
        store: VectorStoreInterface,
        *,
        cleaner: TextCleaner | None = None,
        extractor: MetadataExtractor | None = None,
        chunker: SessionChunker | None = None,
        enricher: ChunkEnricher | None = None,
        embedder: EmbeddingGenerator | None = None,
    ):
        self.store = store
        self.cleaner = cleaner or TextCleaner()
        self.extractor = extractor or MetadataExtractor()
        self.chunker = chunker or SessionChunker()
        self.enricher = enricher or ChunkEnricher()
        self.embedder = embedder or EmbeddingGenerator(enable_logging=False)

    def ingest_session(
        self,
        session_id: str,
        messages: list[SessionMessage],
        *,
        timestamp: str,
    ) -> IngestionResult:
        messages = self.cleaner.clean_messages(messages)
        metadata = self.extractor.extract_with_retry(session_id, messages, timestamp)
        chunks = self.chunker.chunk(session_id, messages, metadata, timestamp)
        enriched = self.enricher.enrich(chunks)
        embedded = self.embedder.embed_chunks(enriched)

        result = IngestionResult(session_id=session_id, metadata=metadata)
        result.failed_chunk_ids = list(embedded.failed_chunks)

        # Upsert-only: this never deletes chunk ids absent from the new run, so
        # re-ingesting a session that SHRANK below a sub-chunk window boundary
        # (e.g. 25 -> 10 messages) leaves its now-stale `<id>::sub::*` rows in
        # session_chunks. Deferred to the "re-chunk after every message" trigger
        # wiring; `test_reingest_is_idempotent` only covers same-size re-ingest
        # (audit 1.2-F4).
        for ec in embedded.chunks:
            record = _to_session_record(ec)
            self.store.upsert(record)
            result.chunk_ids.append(record.chunk_id)
            if record.chunk_type == "primary":
                result.primary_count += 1
            else:
                result.sub_chunk_count += 1

        return result


def _to_session_record(embedded: EmbeddedChunk) -> SessionChunkRecord:
    chunk = embedded.chunk
    indices = chunk.message_indices
    is_primary = chunk.chunk_type == "primary"
    return SessionChunkRecord(
        chunk_id=chunk.chunk_id,
        session_id=chunk.session_id,
        # Persist the RAW session text — the `[Session: …]` enrichment prefix
        # is embedding-input only (docs/schema_review.md §4).
        content=chunk.raw_content,
        embedding=np.asarray(embedded.embedding, dtype=np.float32).reshape(-1),
        token_count=count_tokens(chunk.raw_content),
        chunk_type=chunk.chunk_type,
        window_start_message_idx=None if is_primary or not indices else indices[0],
        window_end_message_idx=None if is_primary or not indices else indices[-1],
        topics=list(chunk.topics),
        action_types=list(chunk.action_types),
        entities=list(chunk.entities),
        message_roles=list(chunk.message_roles),
        sentiment=chunk.sentiment,
    )
