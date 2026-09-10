"""
Shared token counting for the session ingestion pipeline (Phase 1 Step 1.2).

Chunk-size bounds are derived from ``all-MiniLM-L6-v2``'s tokenizer — not
tiktoken (project_logic.md §6). The chunker, the enricher, and
``LocalEmbeddingProvider`` all count tokens through here so there is one
tokenizer instance and one definition of "a token" across ingestion.
"""

import os
import threading
from typing import Any

_lock = threading.Lock()
_tokenizer: Any = None

# e2e seam (Phase 4 CI hardening). When set, `count_tokens` uses a cheap
# character-ratio estimate instead of loading the Hugging Face tokenizer — which
# means the very first `import transformers` never happens in the sidecar. On a
# memory-pressured CI runner (node + chromium + this process) that cold import
# balloons to 60-240s and hangs the memory-retrieval re-ingest. The packaged app
# never sets it; chunk-boundary math only needs an approximate count.
_STUB_TOKENIZER = "RAGPIPE_E2E_STUB_TOKENIZER"


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
    if os.getenv(_STUB_TOKENIZER):
        # ~4 chars/token for English + 2 special tokens ([CLS]/[SEP]).
        return (len(text) + 3) // 4 + 2
    return len(get_tokenizer().encode(text, add_special_tokens=True))
