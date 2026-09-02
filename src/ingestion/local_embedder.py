"""
LocalEmbeddingProvider (Production Roadmap Phase 1 Step 1.1).

The ``EmbeddingProvider`` implementation for the bundled ``all-MiniLM-L6-v2``
model (384-dim, CPU). Registered in ``ProviderFactory`` as ``"local"`` and
the default for ``EmbeddingGenerator``.

No retry/backoff (local in-process inference doesn't need it) and no cost
estimation. Token counting uses a Hugging Face ``AutoTokenizer`` loaded from
the same bundled path — not tiktoken.
"""

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

import numpy as np

from src.ingestion.embedder import EmbeddingProvider

logger = logging.getLogger(__name__)

MODEL_DIR_NAME = "all-MiniLM-L6-v2"
# Repo layout: src/ingestion/local_embedder.py -> parents[2] == rag-pipeline/
_BUNDLED_MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / MODEL_DIR_NAME


def resolve_model_path() -> Path:
    """
    Locate the bundled ``all-MiniLM-L6-v2`` directory.

    Precedence:
      1. ``sys._MEIPASS`` (PyInstaller frozen build) -> ``<_MEIPASS>/models/<name>``
      2. ``RAGPIPE_EMBEDDING_MODEL_PATH`` environment override
      3. the repo-vendored ``rag-pipeline/models/<name>`` directory
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "models" / MODEL_DIR_NAME
    elif os.getenv("RAGPIPE_EMBEDDING_MODEL_PATH"):
        candidate = Path(os.environ["RAGPIPE_EMBEDDING_MODEL_PATH"])
    else:
        candidate = _BUNDLED_MODEL_PATH

    if not (candidate / "config.json").is_file():
        raise FileNotFoundError(
            f"all-MiniLM-L6-v2 not found at {candidate} (no config.json). "
            "Expected the model vendored at rag-pipeline/models/all-MiniLM-L6-v2/, "
            "or RAGPIPE_EMBEDDING_MODEL_PATH pointing at it."
        )
    return candidate


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local sentence-transformers embedding provider (all-MiniLM-L6-v2)."""

    def __init__(
        self,
        model: str = "local",
        dimensions: int | None = None,
        api_key: str | None = None,  # accepted for ProviderFactory call-shape parity; ignored
    ):
        super().__init__(model, dimensions)
        self._model_path = resolve_model_path()
        self._st_model: Any = None
        self._lock = threading.Lock()
        self._total_texts = 0
        self._total_tokens = 0

    @property
    def provider_name(self) -> str:
        return "local"

    # ------------------------------------------------------------------
    # Lazy model / tokenizer load
    # ------------------------------------------------------------------

    def _get_model(self) -> Any:
        if self._st_model is None:
            with self._lock:
                if self._st_model is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                    except ImportError as exc:
                        raise ImportError(
                            "sentence-transformers is required for LocalEmbeddingProvider. "
                            "Install with: pip install sentence-transformers"
                        ) from exc
                    logger.info("Loading all-MiniLM-L6-v2 from %s", self._model_path)
                    self._st_model = SentenceTransformer(str(self._model_path), device="cpu")
        return self._st_model

    # ------------------------------------------------------------------
    # EmbeddingProvider interface
    # ------------------------------------------------------------------

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """Embed ``texts`` -> float32 array of shape ``(len(texts), 384)``."""
        if not texts:
            return np.zeros((0, self._actual_dimensions), dtype=np.float32)
        vectors = (
            self._get_model()
            .encode(
                list(texts),
                convert_to_numpy=True,
                normalize_embeddings=False,
                show_progress_bar=False,
            )
            .astype(np.float32)
        )
        self._actual_dimensions = vectors.shape[1]
        self._total_texts += len(texts)
        self._total_tokens += sum(self.count_tokens(t) for t in texts)
        return vectors

    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single string -> float32 array of shape ``(384,)``."""
        return self.embed_batch([text])[0]

    def count_tokens(self, text: str) -> int:
        """Token count via the model's own Hugging Face tokenizer (not tiktoken)."""
        # Lazy import: src.ingestion.tokenizer imports resolve_model_path from
        # this module, so a top-level import here would be circular.
        from src.ingestion.tokenizer import count_tokens

        return count_tokens(text)

    def get_usage(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "total_texts": self._total_texts,
            "total_tokens": self._total_tokens,
            "dimensions": self._actual_dimensions,
        }

    def reset_usage(self) -> None:
        self._total_texts = 0
        self._total_tokens = 0
