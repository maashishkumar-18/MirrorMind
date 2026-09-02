"""
Shared token counting for the session ingestion pipeline (Phase 1 Step 1.2).

Chunk-size bounds are derived from ``all-MiniLM-L6-v2``'s tokenizer — not
tiktoken (project_logic.md §6). The chunker, the enricher, and
``LocalEmbeddingProvider`` all count tokens through here so there is one
tokenizer instance and one definition of "a token" across ingestion.
"""

import threading
from typing import Any

_lock = threading.Lock()
_tokenizer: Any = None


def get_tokenizer() -> Any:
    """The bundled model's Hugging Face fast tokenizer (lazy, cached)."""
    global _tokenizer
    if _tokenizer is None:
        with _lock:
            if _tokenizer is None:
                from transformers import AutoTokenizer

                # Lazy: local_embedder -> embedder -> enricher -> chunker ->
                # tokenizer would be a module-load cycle if imported at top.
                from src.ingestion.local_embedder import resolve_model_path

                _tokenizer = AutoTokenizer.from_pretrained(str(resolve_model_path()))
    return _tokenizer


def count_tokens(text: str) -> int:
    """Number of tokens ``all-MiniLM-L6-v2`` sees for ``text`` (incl. special tokens)."""
    if not text:
        return 0
    return len(get_tokenizer().encode(text, add_special_tokens=True))
