"""
Session chunking (Phase 1 Step 1.2).

Every session gets one **primary** chunk (the full conversation). Sessions
longer than ``sub_chunk_threshold`` messages also get overlapping
**sub_chunk** windows, following the sliding-window algorithm frozen in
``docs/schema_review.md`` §3:

    start = 0
    while start + window_size <= N:      # full windows only
        emit [start, start + window_size - 1]
        start += stride

A trailing window that would be undersized is dropped, not padded — the
primary chunk (always present, always covering the tail) is the better home
for tail content than a degraded partial window.

``window_size`` / ``stride`` / ``sub_chunk_threshold`` and the token bounds
are config (``config/ingestion/chunker.yaml``), not hardcoded.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.common.types import SessionMessage
from src.ingestion.metadata_extractor import SessionMetadata
from src.ingestion.tokenizer import count_tokens

# NLTK is optional — its data (punkt/punkt_tab, renamed between versions) may
# not be present even when the package is. Any failure degrades to the regex
# splitter rather than crashing the import chain.
try:
    import nltk
    from nltk.tokenize import sent_tokenize

    nltk.data.find("tokenizers/punkt")
    NLTK_AVAILABLE = True
except (ImportError, LookupError):
    NLTK_AVAILABLE = False


_DEFAULTS = {
    "window_size": 10,
    "stride": 5,
    "sub_chunk_threshold": 20,
    "max_chunk_tokens": 256,  # all-MiniLM-L6-v2's real max sequence length
    "min_chunk_tokens": 32,
}

_ENV_OVERRIDES = {
    "RAGPIPE_CHUNK_WINDOW_SIZE": "window_size",
    "RAGPIPE_CHUNK_STRIDE": "stride",
    "RAGPIPE_SUBCHUNK_THRESHOLD": "sub_chunk_threshold",
    "RAGPIPE_MAX_CHUNK_TOKENS": "max_chunk_tokens",
    "RAGPIPE_MIN_CHUNK_TOKENS": "min_chunk_tokens",
}


@dataclass
class Chunk:
    """A session chunk, ready for enrichment and embedding."""

    chunk_id: str
    content: str
    chunk_type: str  # "primary" | "sub_chunk"
    token_count: int = 0
    parent_chunk_id: str = ""

    session_id: str = ""
    message_roles: list[str] = field(default_factory=list)
    message_indices: list[int] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    action_types: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    sentiment: str = ""
    timestamp: str = ""


@dataclass
class SessionChunkerConfig:
    window_size: int = 10
    stride: int = 5
    sub_chunk_threshold: int = 20
    max_chunk_tokens: int = 256
    min_chunk_tokens: int = 32

    @classmethod
    def from_yaml(cls, path: str | None = None) -> "SessionChunkerConfig":
        """
        Load config. Priority: explicit ``path`` > ``RAGPIPE_CHUNKER_CONFIG``
        env var > ``config/ingestion/chunker.yaml`` > built-in defaults. Env
        overrides (``RAGPIPE_CHUNK_*``) are applied last.
        """
        config_path = (
            path
            or os.getenv("RAGPIPE_CHUNKER_CONFIG")
            or str(Path(__file__).parents[2] / "config" / "ingestion" / "chunker.yaml")
        )
        data = dict(_DEFAULTS)
        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            data.update({k: loaded[k] for k in _DEFAULTS if k in loaded})
        data = cls._apply_env_overrides(data)
        return cls(**{k: int(data[k]) for k in _DEFAULTS})

    @staticmethod
    def _apply_env_overrides(data: dict) -> dict:
        for env_var, key in _ENV_OVERRIDES.items():
            value = os.getenv(env_var)
            if value is not None:
                data[key] = value
        return data


class SessionChunker:
    """Turns a conversation transcript into primary + sub_chunk :class:`Chunk`s."""

    def __init__(self, config: SessionChunkerConfig | None = None):
        self.config = config or SessionChunkerConfig.from_yaml()
        self.use_nltk = NLTK_AVAILABLE

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def chunk(
        self,
        session_id: str,
        messages: list[SessionMessage],
        metadata: SessionMetadata,
        timestamp: str,
    ) -> list[Chunk]:
        n = len(messages)
        primary_id = f"{session_id}::primary"

        chunks = [
            self._build_chunk(
                chunk_id=primary_id,
                chunk_type="primary",
                parent_chunk_id="",
                indices=list(range(n)),
                messages=messages,
                session_id=session_id,
                metadata=metadata,
                timestamp=timestamp,
            )
        ]

        if n > self.config.sub_chunk_threshold:
            for start, end in sub_chunk_windows(n, self.config.window_size, self.config.stride):
                chunks.append(
                    self._build_chunk(
                        chunk_id=f"{session_id}::sub::{start}-{end}",
                        chunk_type="sub_chunk",
                        parent_chunk_id=primary_id,
                        indices=list(range(start, end + 1)),
                        messages=messages,
                        session_id=session_id,
                        metadata=metadata,
                        timestamp=timestamp,
                    )
                )

        return chunks

    def _build_chunk(
        self,
        *,
        chunk_id: str,
        chunk_type: str,
        parent_chunk_id: str,
        indices: list[int],
        messages: list[SessionMessage],
        session_id: str,
        metadata: SessionMetadata,
        timestamp: str,
    ) -> Chunk:
        window = [messages[i] for i in indices]
        content = self._render(window)
        return Chunk(
            chunk_id=chunk_id,
            content=content,
            chunk_type=chunk_type,
            token_count=count_tokens(content),
            parent_chunk_id=parent_chunk_id,
            session_id=session_id,
            message_roles=[m.role for m in window],
            message_indices=indices,
            topics=list(metadata.topics),
            action_types=list(metadata.action_types),
            entities=list(metadata.entities),
            sentiment=metadata.sentiment,
            timestamp=timestamp,
        )

    @staticmethod
    def _render(messages: list[SessionMessage]) -> str:
        """Role-labelled turns, one per line — the text that gets embedded."""
        out = []
        for m in messages:
            label = "User" if m.role == "user" else "Assistant"
            out.append(f"[{label}]: {m.content}")
        return "\n".join(out)

    # ------------------------------------------------------------------
    # Sentence splitting — preserved from the document-era chunker,
    # domain-agnostic and reusable (not on the primary chunking path).
    # ------------------------------------------------------------------

    def _split_into_sentences(self, text: str) -> list[str]:
        if self.use_nltk:
            try:
                return sent_tokenize(text)
            except Exception:
                return self._fallback_sentence_split(text)
        return self._fallback_sentence_split(text)

    def _fallback_sentence_split(self, text: str) -> list[str]:
        abbreviations = (
            r"\b(?:Dr|Mr|Mrs|Ms|Prof|Rev|Hon|St|Ave|Blvd|Rd|Jr|Sr|vs|etc|e\.g|i\.e|"
            r"U\.S|U\.S\.A|U\.K|U\.N)\b"
        )
        placeholder = "___ABBR___"
        abbr_map: dict[str, str] = {}

        def replace_abbr(match: "re.Match[str]") -> str:
            abbr = match.group(0)
            key = f"{placeholder}_{len(abbr_map)}"
            abbr_map[key] = abbr
            return key

        text_with_placeholders = re.sub(abbreviations, replace_abbr, text)
        sentences = re.split(r"(?<=[.!?])\s+", text_with_placeholders)
        return [self._restore_abbreviations(s, abbr_map) for s in sentences if s.strip()]

    def _restore_abbreviations(self, text: str, abbr_map: dict[str, str]) -> str:
        result = text
        for placeholder, abbr in abbr_map.items():
            result = result.replace(placeholder, abbr)
        return result


def sub_chunk_windows(n: int, window_size: int, stride: int) -> list[tuple[int, int]]:
    """
    Inclusive ``(start, end)`` message-index ranges for a session of ``n``
    messages — the frozen ``docs/schema_review.md`` §3 algorithm. Full windows
    only; the trailing undersized window is dropped.
    """
    windows = []
    start = 0
    while start + window_size <= n:
        windows.append((start, start + window_size - 1))
        start += stride
    return windows


# Transitional alias: the document-era retrieval/eval bootstrap code still
# imports ``SemanticChunker``. Removed when that code is rewritten (Step 1.3/1.4).
SemanticChunker = SessionChunker
