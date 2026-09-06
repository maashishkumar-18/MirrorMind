"""
Vector embedding for the session ingestion pipeline.

Converts enriched chunks into embedding vectors via the local
``all-MiniLM-L6-v2`` provider (Phase 1 Step 1.2 — the cloud Google/OpenAI
providers were removed with the document-RAG pipeline). Handles batching and
a bounded generic retry; no rate-limit / quota / cost logic (local in-process
inference needs none).
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from dotenv import load_dotenv

from src.ingestion.enricher import ChunkEnricher, EnrichedChunk

load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

# ============================================================================
# Provider Configuration
# ============================================================================


@dataclass
class ModelConfig:
    """Configuration for an embedding model."""

    max_tokens: int
    default_dimensions: int
    provider: str
    pricing_per_1m_tokens: float = 0.0
    supports_custom_dimensions: bool = False


# Model configurations. Local-only as of Phase 1 Step 1.2 — the cloud
# embedding providers (Google/OpenAI) were deleted with the document-RAG
# pipeline. `max_tokens` is all-MiniLM-L6-v2's real max sequence length; it is
# informational only (the model truncates longer inputs — the primary session
# chunk routinely exceeds it by design).
MODEL_CONFIGS = {
    "local": ModelConfig(
        max_tokens=256,
        default_dimensions=384,
        provider="local",
        pricing_per_1m_tokens=0.0,
        supports_custom_dimensions=False,
    ),
}

# ============================================================================
# Provider Interface
# ============================================================================


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    def __init__(self, model: str, dimensions: int | None = None):
        self.model = model
        self.dimensions = dimensions
        self.config = MODEL_CONFIGS.get(model)
        if not self.config:
            raise ValueError(f"Unknown model: {model}")

        # Track actual dimensions from response
        self._actual_dimensions = dimensions or self.config.default_dimensions

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]] | np.ndarray:
        """
        Embed a batch of texts.

        Args:
            texts: List of text strings to embed

        Returns:
            Embedding vectors — a list[list[float]] for the cloud providers,
            or an np.ndarray of shape (len(texts), dim) for
            LocalEmbeddingProvider (Phase 1 Step 1.1). Callers must use
            ``len(...)`` / indexing, never bare truthiness.
        """
        pass

    @abstractmethod
    def get_usage(self) -> dict[str, Any]:
        """Get usage statistics for the current session."""
        pass

    @abstractmethod
    def reset_usage(self) -> None:
        """Reset usage statistics."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider."""
        pass


# ============================================================================
# Provider Factory
# ============================================================================


class ProviderFactory:
    """Factory for creating embedding providers. Local-only (Phase 1 Step 1.2)."""

    @classmethod
    def _provider_class(cls, provider: str) -> type[EmbeddingProvider] | None:
        if provider == "local":
            # Lazy import: local_embedder imports EmbeddingProvider from this
            # module, so a top-level import here would be circular.
            from src.ingestion.local_embedder import LocalEmbeddingProvider

            return LocalEmbeddingProvider
        return None

    @classmethod
    def create(
        cls, model: str, dimensions: int | None = None, api_key: str | None = None
    ) -> EmbeddingProvider:
        """
        Create a provider instance for the given model.

        Args:
            model: Model name (e.g., "local", "text-embedding-3-small")
            dimensions: Desired output dimensions (optional)
            api_key: API key for the provider (optional)

        Returns:
            EmbeddingProvider instance
        """
        config = MODEL_CONFIGS.get(model)
        if not config:
            raise ValueError(f"Unsupported model: {model}")

        provider_class = cls._provider_class(config.provider)
        if not provider_class:
            raise ValueError(f"Unsupported provider: {config.provider}")

        return provider_class(model, dimensions, api_key)


# ============================================================================
# Main Embedding Generator
# ============================================================================


@dataclass
class EmbeddingResult:
    """A single embedding result tied to its chunk."""

    chunk_id: str
    embedding: list[float]
    token_count: int
    model: str = ""
    dimensions: int = 0
    batch_index: int | None = None


@dataclass
class EmbeddedChunk:
    """
    Complete chunk with embedding vector and all metadata.
    This is the final form before going to the vector database.
    """

    chunk: EnrichedChunk  # All enriched chunk data
    embedding: list[float]  # The vector embedding
    model: str = ""
    dimensions: int = 0
    version: str = "1.0"

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id

    @property
    def content(self) -> str:
        return self.chunk.content

    @property
    def metadata(self) -> dict[str, Any]:
        return ChunkEnricher.to_metadata(self.chunk)


@dataclass
class EmbeddingBatchResult:
    """Results from embedding a batch of chunks."""

    chunks: list[EmbeddedChunk]  # Successfully embedded chunks
    total_tokens: int
    total_chunks: int
    failed_chunks: list[str]  # chunk_ids that failed
    processing_time: float  # seconds
    model: str = ""
    provider_usage: dict[str, Any] = field(default_factory=dict)

    @property
    def success_count(self) -> int:
        return len(self.chunks)


class EmbeddingGenerator:
    """
    Generates vector embeddings for enriched chunks using configurable providers.
    Handles batching, retries, rate limiting, and token validation.
    """

    def __init__(
        self,
        model: str = "local",
        dimensions: int | None = None,
        batch_size: int = 20,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        api_key: str | None = None,
        enable_logging: bool = True,
    ):
        """
        Initialize the embedding generator.

        Args:
            model: Embedding model to use (see MODEL_CONFIGS)
            dimensions: Desired output dimensions. None uses model default.
            batch_size: Number of texts to embed per API call
            max_retries: Maximum retry attempts for failed requests
            retry_delay: Base delay between retries (exponential backoff applied)
            api_key: API key for the provider (optional, uses env var)
            enable_logging: Enable/disable logging output
        """
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.enable_logging = enable_logging

        # Get model configuration
        self.config = MODEL_CONFIGS.get(model)
        if not self.config:
            raise ValueError(f"Unsupported model: {model}. Available: {list(MODEL_CONFIGS.keys())}")

        # Create provider
        self.provider = ProviderFactory.create(model, dimensions, api_key)

        # Set dimensions from config if not specified
        if self.dimensions is None:
            self.dimensions = self.config.default_dimensions

        # Version tracking
        self.version = "1.0"

        if self.enable_logging:
            logger.info(
                f"Initialized EmbeddingGenerator with model={model}, dimensions={self.dimensions}, provider={self.provider.provider_name}"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_chunks(self, chunks: list[EnrichedChunk]) -> EmbeddingBatchResult:
        """
        Generate embeddings for all enriched chunks.

        Args:
            chunks: List of EnrichedChunk objects from Step 5

        Returns:
            EmbeddingBatchResult with EmbeddedChunk objects and diagnostics
        """
        if not chunks:
            return EmbeddingBatchResult(
                chunks=[],
                total_tokens=0,
                total_chunks=0,
                failed_chunks=[],
                processing_time=0.0,
                model=self.model,
            )

        start_time = time.time()

        # All chunks are embedded; over-limit ones are truncated by the model,
        # not skipped (see _validate_chunks).
        valid_chunks, oversized = self._validate_chunks(chunks)

        if oversized:
            logger.debug(
                "%d chunk(s) exceed the model's %d-token window and will be embedded truncated",
                len(oversized),
                self.config.max_tokens,
            )

        # Generate embeddings in batches
        all_embedded_chunks = []
        failed = []
        retry_attempts = defaultdict(int)

        for i in range(0, len(valid_chunks), self.batch_size):
            batch = valid_chunks[i : i + self.batch_size]

            embedded, failed_in_batch = self._embed_batch_with_retry(
                batch, batch_index=i // self.batch_size, retry_attempts=retry_attempts
            )

            all_embedded_chunks.extend(embedded)
            failed.extend(failed_in_batch)

        processing_time = time.time() - start_time

        # Update chunk metadata
        for embedded_chunk in all_embedded_chunks:
            chunk = embedded_chunk.chunk
            chunk.embedding_ready = True
            chunk.embedding_model = self.model
            chunk.embedding_version = self.version
            chunk.embedding_dimensions = embedded_chunk.dimensions

        # Mark failed chunks as not ready
        for chunk in chunks:
            if chunk.chunk_id in failed:
                chunk.embedding_ready = False

        return EmbeddingBatchResult(
            chunks=all_embedded_chunks,
            total_tokens=sum(ec.chunk.token_count for ec in all_embedded_chunks),
            total_chunks=len(chunks),
            failed_chunks=failed,
            processing_time=processing_time,
            model=self.model,
            provider_usage=self.provider.get_usage(),
        )

    async def embed_chunks_async(self, chunks: list[EnrichedChunk]) -> EmbeddingBatchResult:
        """
        Async version for use within FastAPI endpoints.
        """
        return await asyncio.to_thread(self.embed_chunks, chunks)

    def embed_single(self, chunk: EnrichedChunk) -> EmbeddedChunk | None:
        """
        Generate embedding for a single chunk (used for queries).
        """
        result = self._embed_batch_with_retry([chunk], batch_index=0)
        if result[0]:
            return result[0][0]
        return None

    def embed_query(self, query: str) -> np.ndarray | list[float] | None:
        """
        Generate an embedding vector for a user query.

        Unlike embed_single(), this method accepts a plain string instead of an
        EnrichedChunk and returns only the embedding vector, making it suitable
        for retrieval-time semantic search.

        Args:
            query: User query text.

        Returns:
            The embedding vector, or None if embedding fails. A ``(dim,)``
            ``np.ndarray`` from ``LocalEmbeddingProvider`` (the only registered
            provider as of Phase 1 Step 1.1); ``list[float]`` only from the
            unregistered cloud adapters. Callers must not use bare truthiness
            on the result — ``if vec is not None and len(vec)`` / ``vec.size``.
        """
        if not query or not query.strip():
            return None

        for attempt in range(self.max_retries):
            try:
                embeddings = self.provider.embed_batch([query])

                # `embeddings` may be a list[list[float]] (cloud providers) or
                # an np.ndarray of shape (1, dim) (LocalEmbeddingProvider) —
                # use len(), not truthiness, which is ambiguous for arrays.
                if embeddings is not None and len(embeddings) > 0:
                    return embeddings[0]

                return None

            except Exception as e:
                # Local in-process inference: no rate-limit / quota classes to
                # match on — a bounded generic retry covers transient hiccups.
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2**attempt)
                    logger.warning(
                        f"Query embedding retry {attempt + 1}/{self.max_retries} "
                        f"in {wait_time:.1f}s..."
                    )
                    time.sleep(wait_time)
                    continue

                logger.error(f"Failed to embed query: {e}")
                return None

        return None

    def embed_queries(self, queries: list[str]) -> list[np.ndarray | list[float] | None]:
        """
        Generate embedding vectors for multiple query strings in a single
        batched API call, instead of one round trip per query.

        Args:
            queries: List of query strings. Blank/empty entries map to None.

        Returns:
            List of embedding vectors (or None for blank/failed entries),
            in the same order as `queries`. Each vector is a ``(dim,)``
            ``np.ndarray`` from ``LocalEmbeddingProvider`` (see ``embed_query``).
        """
        indexed = [(i, q) for i, q in enumerate(queries) if q and q.strip()]
        results: list[np.ndarray | list[float] | None] = [None] * len(queries)

        if not indexed:
            return results

        texts = [q for _, q in indexed]

        for attempt in range(self.max_retries):
            try:
                embeddings = self.provider.embed_batch(texts)

                for (original_idx, _), embedding in zip(indexed, embeddings, strict=False):
                    results[original_idx] = embedding

                return results

            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2**attempt)
                    logger.warning(
                        f"Batch query embedding retry {attempt + 1}/{self.max_retries} "
                        f"in {wait_time:.1f}s..."
                    )
                    time.sleep(wait_time)
                    continue

                logger.error(f"Failed to embed query batch: {e}")
                return results

        return results

    # ------------------------------------------------------------------
    # Core Embedding Logic
    # ------------------------------------------------------------------

    def _embed_batch_with_retry(
        self,
        chunks: list[EnrichedChunk],
        batch_index: int,
        retry_attempts: dict[str, int] | None = None,
    ) -> tuple[list[EmbeddedChunk], list[str]]:
        """
        Embed a batch of chunks with retry logic.

        Args:
            chunks: List of up to batch_size chunks
            batch_index: Index of this batch for tracking
            retry_attempts: Dictionary to track retry attempts per chunk

        Returns:
            Tuple of (embedded_chunks, failed_chunk_ids)
        """
        if not chunks:
            return [], []

        texts = [chunk.content for chunk in chunks]
        chunk_ids = [chunk.chunk_id for chunk in chunks]

        for attempt in range(self.max_retries):
            try:
                # Call provider
                embeddings = self.provider.embed_batch(texts)

                # Build EmbeddedChunk objects
                embedded_chunks = []
                for i, embedding in enumerate(embeddings):
                    embedded_chunk = EmbeddedChunk(
                        chunk=chunks[i],
                        embedding=embedding,
                        model=self.model,
                        dimensions=len(embedding),  # Actual dimensions from response
                        version=self.version,
                    )
                    embedded_chunks.append(embedded_chunk)

                if self.enable_logging and len(embeddings) > 0:
                    logger.debug(f"Batch {batch_index}: embedded {len(embeddings)} chunks")

                return embedded_chunks, []

            except Exception as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2**attempt)
                    logger.warning(
                        f"Batch {batch_index} embedding retry "
                        f"{attempt + 1}/{self.max_retries} in {wait_time:.1f}s..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"Failed after {self.max_retries} attempts: {e}")
                    return [], chunk_ids

        return [], chunk_ids

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_chunks(
        self, chunks: list[EnrichedChunk]
    ) -> tuple[list[EnrichedChunk], list[tuple[str, int]]]:
        """
        Split chunks into (to_embed, over_model_limit) — the second list is
        for diagnostics only, NOT rejection. A local model silently truncates
        an over-length input to its max sequence length; there is no API limit
        or per-token cost to guard against, and the primary session chunk (the
        whole conversation) routinely exceeds the limit by design.
        """
        max_tokens = self.config.max_tokens
        over_limit = [(c.chunk_id, c.token_count) for c in chunks if c.token_count > max_tokens]
        return list(chunks), over_limit

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_embedding_summary(self, result: EmbeddingBatchResult) -> dict[str, Any]:
        """
        Generate a human-readable summary of the embedding process.
        """
        return {
            "model": result.model,
            "dimensions": result.chunks[0].dimensions if result.chunks else self.dimensions,
            "version": self.version,
            "provider": self.provider.provider_name,
            "total_chunks": result.total_chunks,
            "successful": result.success_count,
            "failed": len(result.failed_chunks),
            "failed_chunk_ids": result.failed_chunks[:10],
            "total_tokens": result.total_tokens,
            "processing_time_seconds": round(result.processing_time, 2),
            "tokens_per_second": (
                round(result.total_tokens / result.processing_time, 1)
                if result.processing_time > 0
                else 0
            ),
            "provider_usage": result.provider_usage,
        }

    def switch_model(self, new_model: str, dimensions: int | None = None) -> None:
        """
        Switch to a different model.

        Args:
            new_model: New model name
            dimensions: Optional new dimensions
        """
        self.model = new_model
        if dimensions is not None:
            self.dimensions = dimensions

        self.config = MODEL_CONFIGS.get(new_model)
        if not self.config:
            raise ValueError(f"Unsupported model: {new_model}")

        # Create new provider
        self.provider = ProviderFactory.create(new_model, self.dimensions)

        if self.enable_logging:
            logger.info(
                f"Switched to model={new_model}, dimensions={self.dimensions}, provider={self.provider.provider_name}"
            )

    def set_logging_level(self, level: int = logging.INFO) -> None:
        """Configure logging level for the embedder."""
        logger.setLevel(level)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            logger.addHandler(handler)
