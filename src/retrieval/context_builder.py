"""
Context Builder (Phase 1 Step 1.3c — session companion).

Assembles the ``SessionRetrievedChunk`` list from ``RetrievalRouter.route()``
into a citation-tagged, token-budgeted context block for the generation
prompt. Citations are session id + approximate timestamp only — no document,
page, slide, course or chapter concepts (project_logic.md §12).

The study-assistant machinery (templates, regex chunk classification,
page/chapter diversity filtering, ``[Course | Chapter | Slide N]`` citations)
is gone. ``TokenCounter`` and the module-level ``TIKTOKEN_AVAILABLE`` flag are
kept byte-identical — pinned by tests/retrieval/test_context_builder_token_counter.py.

Config: config/retrieval/context_builder.yaml, env overrides via RAGPIPE_CONTEXT_*.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.common.types import SessionRetrievedChunk

# Optional imports for tokenization
try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

load_dotenv()


# ============================================================================
# Configuration
# ============================================================================


@dataclass
class ContextBuilderConfig:
    """Configuration for session context assembly — loaded from YAML."""

    max_total_tokens: int = 3000
    max_chunks: int = 6
    separator: str = "\n\n---\n\n"
    truncation_marker: str = "\n\n[earlier context truncated]"
    truncate_at_sentence: bool = True
    citations_enabled: bool = True
    citation_format: str = "[Session {session_id} · approx. {timestamp}]"
    include_citations_in_context: bool = True
    normalize_whitespace: bool = True
    remove_duplicate_lines: bool = True
    # tiktoken / gpt-4o here is deliberate: this counter is for context-window
    # BUDGETING (the gpt-4o encoding is a fine proxy even for Ollama models).
    # src/ingestion/tokenizer.py's AutoTokenizer is the separate tool for
    # chunk-size limits against the embedding model — do not unify them.
    tokenizer_model: str = "gpt-4o"

    @classmethod
    def from_yaml(cls, path: str | None = None) -> "ContextBuilderConfig":
        """
        Load configuration.

        Priority: explicit ``path`` > ``RAGPIPE_CONTEXT_BUILDER_CONFIG`` env var
        > ``config/retrieval/context_builder.yaml`` > built-in defaults.
        """
        config_path = (
            path
            or os.getenv("RAGPIPE_CONTEXT_BUILDER_CONFIG")
            or str(
                Path(__file__).parent.parent.parent
                / "config"
                / "retrieval"
                / "context_builder.yaml"
            )
        )

        data: dict = {}
        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        data = cls._apply_env_overrides(data)

        assembly = data.get("assembly", {})
        citations = data.get("citations", {})
        cleaning = data.get("cleaning", {})
        tokenization = data.get("tokenization", {})

        defaults = cls()
        return cls(
            max_total_tokens=int(assembly.get("max_total_tokens", defaults.max_total_tokens)),
            max_chunks=int(assembly.get("max_chunks", defaults.max_chunks)),
            separator=str(assembly.get("separator", defaults.separator)),
            truncation_marker=str(assembly.get("truncation_marker", defaults.truncation_marker)),
            truncate_at_sentence=bool(
                assembly.get("truncate_at_sentence", defaults.truncate_at_sentence)
            ),
            citations_enabled=bool(citations.get("enabled", defaults.citations_enabled)),
            citation_format=str(citations.get("format", defaults.citation_format)),
            include_citations_in_context=bool(
                citations.get("include_in_context", defaults.include_citations_in_context)
            ),
            normalize_whitespace=bool(
                cleaning.get("normalize_whitespace", defaults.normalize_whitespace)
            ),
            remove_duplicate_lines=bool(
                cleaning.get("remove_duplicate_lines", defaults.remove_duplicate_lines)
            ),
            tokenizer_model=str(tokenization.get("model", defaults.tokenizer_model)),
        )

    @classmethod
    def _apply_env_overrides(cls, data: dict) -> dict:
        """Apply ``RAGPIPE_CONTEXT_*`` environment variable overrides."""
        env_mapping = {
            "RAGPIPE_CONTEXT_MAX_TOKENS": ("assembly", "max_total_tokens"),
            "RAGPIPE_CONTEXT_MAX_CHUNKS": ("assembly", "max_chunks"),
            "RAGPIPE_CONTEXT_CITATIONS": ("citations", "enabled"),
            "RAGPIPE_CONTEXT_TOKENIZER": ("tokenization", "model"),
        }

        for env_var, (section, key) in env_mapping.items():
            value = os.getenv(env_var)
            if value is None:
                continue
            data.setdefault(section, {})
            if key == "enabled":
                data[section][key] = value.lower() in ("true", "1", "yes")
            else:
                try:
                    data[section][key] = int(value)
                except ValueError:
                    data[section][key] = value

        return data


# ============================================================================
# Token Counter
# ============================================================================


class TokenCounter:
    """
    Handles token counting for context assembly.

    Uses the specified tokenizer for accurate counting, with fallback
    to character-based estimation.
    """

    def __init__(self, model_name: str = "gpt-4o"):
        self.model_name = model_name
        self._encoder = None

        if TIKTOKEN_AVAILABLE:
            try:
                self._encoder = tiktoken.encoding_for_model(model_name)
            except Exception:
                # Fall back to cl100k_base if specific model encoding not found
                try:
                    self._encoder = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    self._encoder = None

    def count_tokens(self, text: str) -> int:
        """Count tokens in a string."""
        if not text:
            return 0

        if self._encoder is not None:
            try:
                return len(self._encoder.encode(text))
            except Exception:
                # Fall back to estimation if encoding fails
                return self._estimate_tokens(text)

        # Fall back to estimation
        return self._estimate_tokens(text)

    def _estimate_tokens(self, text: str) -> int:
        """Estimate tokens using character-based approximation."""
        # Rough approximation: 1 token ≈ 4 characters for English text
        return len(text) // 4

    def count_tokens_batch(self, texts: list[str]) -> list[int]:
        """Count tokens for multiple texts efficiently."""
        if self._encoder is not None:
            try:
                # Use batch encoding if available
                combined = "\n".join(texts)
                all_tokens = self._encoder.encode(combined)

                # This is a simplified approach; for precise per-text counts,
                # we'd need to track positions. For our use case, individual
                # counts are fine.
                return [self.count_tokens(t) for t in texts]
            except Exception:
                pass

        return [self._estimate_tokens(t) for t in texts]


# ============================================================================
# Context Builder
# ============================================================================


@dataclass
class AssembledContext:
    """Final assembled context, ready for the generation layer."""

    formatted_context: str
    chunks_used: int
    tokens_used: int
    truncated: bool
    citations: list[str] = field(default_factory=list)
    # Raw per-chunk data backing `citations`/`formatted_context`, in display
    # order. Downstream layers must use this, never parse the citation strings
    # for structure — they are display-only formatted text, not a data contract.
    chunks: list[SessionRetrievedChunk] = field(default_factory=list)


class ContextBuilder:
    """Assembles retrieved session chunks into a citation-tagged context block."""

    def __init__(self, config: ContextBuilderConfig | None = None, config_path: str | None = None):
        self.config = config or ContextBuilderConfig.from_yaml(config_path)
        self.token_counter = TokenCounter(self.config.tokenizer_model)

    def build(self, chunks: list[SessionRetrievedChunk]) -> AssembledContext:
        """Assemble ``chunks`` (already score-sorted by the router) into context."""
        if not chunks:
            return AssembledContext("", 0, 0, False)

        selected = list(chunks[: self.config.max_chunks])
        max_tokens = self.config.max_total_tokens
        sep_tokens = self.token_counter.count_tokens(self.config.separator)

        parts: list[str] = []
        citations: list[str] = []
        used_chunks: list[SessionRetrievedChunk] = []
        total_tokens = 0
        truncated = len(chunks) > len(selected)

        for chunk in selected:
            citation = self._citation(chunk)
            body = self._clean(chunk.content)
            block = f"{citation}\n{body}" if self._cite_in_context(citation) else body

            prefix_tokens = sep_tokens if parts else 0
            block_tokens = self.token_counter.count_tokens(block)

            if total_tokens + prefix_tokens + block_tokens <= max_tokens:
                parts.append(block)
                citations.append(citation)
                used_chunks.append(chunk)
                total_tokens += prefix_tokens + block_tokens
                continue

            # Doesn't fit whole. Try a sentence-boundary truncation of THIS
            # block only (never the joined string — that would clip separators).
            remaining = max_tokens - total_tokens - prefix_tokens
            if self.config.truncate_at_sentence and remaining > 0:
                partial, partial_tokens = self._truncate_at_sentence(block, remaining)
                if partial:
                    parts.append(partial)
                    citations.append(citation)
                    used_chunks.append(chunk)
                    total_tokens += prefix_tokens + partial_tokens
            truncated = True
            break

        formatted = self.config.separator.join(parts)
        if truncated and formatted:
            formatted += self.config.truncation_marker
            total_tokens += self.token_counter.count_tokens(self.config.truncation_marker)

        return AssembledContext(
            formatted_context=formatted,
            chunks_used=len(used_chunks),
            tokens_used=total_tokens,
            truncated=truncated,
            citations=citations,
            chunks=used_chunks,
        )

    def count_tokens(self, text: str) -> int:
        """Public token count using the configured tokenizer."""
        return self.token_counter.count_tokens(text)

    def estimate_context_size(self, chunks: list[SessionRetrievedChunk]) -> int:
        """Rough token size of the context ``build()`` would produce (pre-budget)."""
        if not chunks:
            return 0
        selected = chunks[: self.config.max_chunks]
        sep_tokens = self.token_counter.count_tokens(self.config.separator)
        total = 0
        for i, chunk in enumerate(selected):
            citation = self._citation(chunk)
            body = self._clean(chunk.content)
            block = f"{citation}\n{body}" if self._cite_in_context(citation) else body
            total += self.token_counter.count_tokens(block)
            if i > 0:
                total += sep_tokens
        return total

    # ------------------------------------------------------------------

    def _cite_in_context(self, citation: str) -> bool:
        return bool(
            self.config.citations_enabled and self.config.include_citations_in_context and citation
        )

    def _citation(self, chunk: SessionRetrievedChunk) -> str:
        """Session id + approximate timestamp only — see project_logic.md §12."""
        if not self.config.citations_enabled:
            return ""
        timestamp = chunk.timestamp or "unknown"
        if chunk.chunk_type == "structured_record":
            table = chunk.metadata.get("table", "record")
            return f"[{table} record · approx. {timestamp}]"
        return self.config.citation_format.format(
            session_id=chunk.session_id or "unknown", timestamp=timestamp
        )

    def _clean(self, text: str) -> str:
        if self.config.normalize_whitespace:
            text = self._normalize_whitespace(text)
        if self.config.remove_duplicate_lines:
            text = self._remove_duplicate_lines(text)
        return text

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """Normalize whitespace without collapsing intentional structure."""
        lines = [line.rstrip() for line in text.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _remove_duplicate_lines(text: str) -> str:
        """Remove duplicate consecutive lines."""
        unique = []
        prev = None
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped != prev or not stripped:
                unique.append(line)
            prev = stripped
        return "\n".join(unique)

    def _truncate_at_sentence(self, text: str, max_tokens: int) -> tuple[str, int]:
        """
        Truncate ``text`` at a sentence boundary within ``max_tokens``.

        Returns ``(truncated_text, token_count)``; ``("", 0)`` if nothing fits.
        """
        if max_tokens <= 0:
            return "", 0

        sentences = re.split(r"(?<=[.!?])\s+", text)
        kept: list[str] = []
        current = 0

        for sentence in sentences:
            sentence_tokens = self.token_counter.count_tokens(sentence)
            if current + sentence_tokens <= max_tokens:
                kept.append(sentence)
                current += sentence_tokens
            elif kept:
                break
            else:
                # Can't fit even one sentence — character-based cut.
                clipped = text[: max_tokens * 4]
                if clipped:
                    clipped = clipped + "..."
                    return clipped, self.token_counter.count_tokens(clipped)
                return "", 0

        if not kept:
            return "", 0
        result = " ".join(kept)
        return result, self.token_counter.count_tokens(result)
