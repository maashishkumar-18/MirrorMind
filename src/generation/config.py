"""
Generation Layer — Shared Contracts
All dataclasses, enums, and configuration types for the generation pipeline.

No implementation logic — pure data contracts.
No UUID generation inside contracts.
No business logic in dataclasses.

Phase 1 Step 1.4: session-shaped. Chunks are ``SessionRetrievedChunk``;
citations are session id + approximate timestamp (project_logic.md §12);
no document / page / slide / course / chapter concepts anywhere.
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from src.common.types import (
    SessionCitationFormat,
    SessionRetrievedChunk,
)  # noqa: F401 — re-exported

load_dotenv()


# ============================================================================
# Enums
# ============================================================================


class GenerationMode(str, Enum):
    """Supported generation modes."""

    CONTEXT_AWARE = "context_aware"  # Precise, memory-grounded
    SIMPLE_EXPLANATION = "simple_explanation"  # Analogies, simple language


class AnswerFormat(str, Enum):
    """Output format for generated answers."""

    MARKDOWN = "markdown"
    PLAIN_TEXT = "plain_text"
    STRUCTURED = "structured"  # Sections with headers


class CitationStyle(str, Enum):
    """How citations appear in the answer."""

    INLINE = "inline"  # [Session <id> · approx. <timestamp>]
    NONE = "none"


class ConfidenceLevel(str, Enum):
    """Confidence level for retrieval and grounding."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


# ============================================================================
# Core Data Contracts — Pure Data, No Logic
# ============================================================================


@dataclass
class ChatTurn:
    """A single turn in a conversation."""

    role: str  # "user", "assistant", "system"
    content: str
    timestamp: str | None = None  # ISO format timestamp


@dataclass
class RetrievalMetadata:
    """
    Metadata about the retrieval process.

    Owned by the retrieval layer, passed through generation.
    """

    confidence_score: float
    confidence_level: ConfidenceLevel
    retrieval_time_ms: float
    total_chunks_retrieved: int
    session_id: str = ""  # scoping session, "" for cross-session retrieval
    top_k: int = 5
    threshold: float | None = None
    retrieval_method: str | None = None  # "semantic" | "structured" | "hybrid"

    # Additional provider-specific metadata
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenerationRequest:
    """
    Input to the generation pipeline.

    This is the contract between the retrieval layer and generation layer.
    IDs are provided by the orchestrator, not generated here.
    """

    request_id: str  # Unique identifier (owned by orchestrator)
    query: str  # Original user message
    mode: GenerationMode

    # Retrieved context — raw session chunks from RetrievalRouter.route().
    chunks: list[SessionRetrievedChunk]
    retrieval_metadata: RetrievalMetadata

    # Recent history of the current session, injected as a first-class prompt
    # section (oldest first, role-labelled). project_logic.md §6.
    conversation_history: list[ChatTurn] | None = None

    # Optional free-form answer-shaping instructions (e.g.
    # {"length": "Target length: 250-350 words...", "style": "Structure the
    # answer as numbered steps."}). Each value is appended verbatim as a
    # line under an "## ANSWER REQUIREMENTS" section in the prompt.
    answer_hints: dict[str, str] | None = None


@dataclass
class UsageStats:
    """
    Token usage statistics from the LLM provider.

    Designed to be provider-agnostic while preserving provider-specific details.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    # Provider-specific metadata (e.g., cached_tokens, reasoning_tokens)
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelInfo:
    """
    Information about the LLM model used.

    Provider-agnostic representation.
    """

    provider: str  # "ollama" (v1); cloud providers are v1.1
    model_name: str  # e.g. "llama3.1:8b"
    api_version: str | None = None

    # Additional provider-specific details
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class GeneratedAnswer:
    """
    Raw output from the LLM before post-processing.

    Contains the full model response plus usage metadata.
    """

    content: str  # Raw LLM output
    model_info: ModelInfo  # Model used
    usage: UsageStats  # Token usage
    generation_time_ms: float  # Time spent in LLM call
    finish_reason: str = "stop"  # "stop", "length", "error", "tool_calls"

    # Populated only when finish_reason == "error" — lets callers show a
    # specific message instead of a bare empty answer. error_type is one of
    # "rate_limited", "credential_error", "timeout", "api_error", or None.
    error_type: str | None = None
    retry_after_seconds: float | None = None
    is_daily_quota: bool = False

    # Raw provider response (for debugging/tracing)
    raw_response: Any = None


@dataclass
class Citation:
    """
    A single source citation for the generated answer.

    Session-temporal (project_logic.md §12) — session id + approximate
    timestamp are the only location concepts. Pure data; formatting is
    handled by CitationFormatter.
    """

    index: int  # Citation number in the answer
    chunk_id: str  # Source chunk identifier (SessionRetrievedChunk.chunk_id)

    session_id: str = ""
    approximate_timestamp: str = ""  # ISO 8601, "" if unknown

    # Where the citation appears in the answer
    position_start: int = 0  # Character position in answer
    position_end: int = 0

    def to_format(self) -> SessionCitationFormat:
        """The shared session-citation shape (src/common/types.py)."""
        return SessionCitationFormat(
            session_id=self.session_id,
            approximate_timestamp=self.approximate_timestamp or "unknown",
        )


@dataclass
class GenerationResponse:
    """
    Complete output of the generation pipeline.

    This is the final contract — everything the caller receives.
    IDs are provided by the orchestrator.
    """

    # Core answer
    answer: str  # Final answer text (post-processed)
    mode: GenerationMode
    answer_format: AnswerFormat

    # Citations and grounding
    citations: list[Citation]
    is_grounded: bool
    grounding_confidence: float

    # Retrieval metadata (owned by retrieval, passed through)
    retrieval_metadata: RetrievalMetadata

    # Generation metadata
    model_info: ModelInfo
    usage: UsageStats
    generation_time_ms: float
    total_time_ms: float

    # Traceability — IDs owned by orchestrator
    request_id: str
    generation_id: str

    # Version information for reproducibility
    prompt_version: str | None = None
    config_version: str | None = None

    # Diagnostics
    warnings: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)

    # Populated only on generation failure (see GeneratedAnswer).
    error_type: str | None = None
    retry_after_seconds: float | None = None
    is_daily_quota: bool = False


# ============================================================================
# Prompt Contract
# ============================================================================


@dataclass
class Prompt:
    """
    Structured prompt object.

    Built by PromptBuilder from YAML templates.
    Pure data — no concatenation logic.
    """

    system_prompt: str
    user_prompt: str
    template_id: str

    context_token_count: int
    system_token_count: int
    user_token_count: int
    total_token_count: int

    prompt_id: str
    request_id: str

    template_version: str | None = None
    mode: GenerationMode | None = None
    assembled_at: str | None = None


# ============================================================================
# Configuration Types
# ============================================================================


@dataclass
class ModelConfig:
    """Configuration for an LLM model."""

    provider: str  # "ollama" (v1)
    model_name: str  # e.g. "llama3.1:8b"
    api_version: str | None = None
    temperature: float = 0.3
    max_output_tokens: int = 2048
    top_p: float = 0.95
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay_seconds: float = 1.0

    # Provider-specific configuration
    extra: dict[str, Any] = field(default_factory=dict)

    def to_model_info(self) -> ModelInfo:
        """Convert to ModelInfo (pure data transformation)."""
        return ModelInfo(
            provider=self.provider,
            model_name=self.model_name,
            api_version=self.api_version,
            extra=self.extra,
        )


@dataclass
class ModeConfig:
    """Configuration for a generation mode."""

    mode: GenerationMode
    prompt_id: str  # Identifier for prompt template (not path)
    model_key: str  # Key in models.yaml
    answer_format: AnswerFormat
    citation_style: CitationStyle
    include_context_header: bool
    max_context_tokens: int
    system_instructions: str  # Brief description for logging


@dataclass
class PostProcessingConfig:
    """Configuration for answer post-processing."""

    extract_citations: bool
    citation_style: CitationStyle
    validate_grounding: bool
    grounding_check_method: str  # "keyword_overlap", "llm_check", "both"
    remove_artifacts: bool  # Remove [HIDE], <THINK> tags
    normalize_whitespace: bool
    max_answer_length: int | None = None  # Truncate if exceeded
    format_as_markdown: bool = True

    # Grounding validation thresholds
    min_keyword_overlap: float = 0.3
    min_grounding_confidence: float = 0.6


@dataclass
class GenerationConfig:
    """
    Complete generation pipeline configuration.

    Loaded from config/generation/ YAML files.
    """

    config_version: str = "1.0.0"
    default_mode: GenerationMode = GenerationMode.CONTEXT_AWARE
    default_model_key: str = "ollama_default"
    models: dict[str, ModelConfig] = field(default_factory=dict)
    modes: dict[str, ModeConfig] = field(default_factory=dict)
    post_processing: PostProcessingConfig = field(
        default_factory=lambda: PostProcessingConfig(
            extract_citations=True,
            citation_style=CitationStyle.INLINE,
            validate_grounding=True,
            grounding_check_method="keyword_overlap",
            remove_artifacts=True,
            normalize_whitespace=True,
        )
    )

    # Version information for reproducibility
    prompt_version: str | None = None
    generation_version: str = "1.0.0"

    @classmethod
    def from_yaml(cls, config_dir: str | None = None) -> "GenerationConfig":
        """
        Load all generation configuration from YAML files.

        Priority:
        1. Explicit config_dir argument
        2. RAGPIPE_GENERATION_CONFIG_DIR env var
        3. config/generation/ (default)
        """
        base = Path(
            config_dir
            or os.getenv("RAGPIPE_GENERATION_CONFIG_DIR")
            or str(Path(__file__).parent.parent.parent / "config" / "generation")
        )

        # Load generation.yaml
        gen_path = base / "generation.yaml"
        gen_data: dict = {}
        if gen_path.exists():
            with open(gen_path) as f:
                gen_data = yaml.safe_load(f) or {}
        gen_data = cls._apply_env_overrides(gen_data)

        # Load models.yaml
        models_path = base / "models.yaml"
        models_data: dict = {}
        if models_path.exists():
            with open(models_path) as f:
                models_data = yaml.safe_load(f) or {}

        # Parse models. yaml.safe_load does no shell expansion — an Ollama
        # model with no explicit model_name resolves via $OLLAMA_DEFAULT_MODEL,
        # matching src/common/llm_client.py::simple_generate.
        models = {}
        for key, model_data in models_data.get("models", {}).items():
            provider = str(model_data.get("provider", "ollama"))
            model_name = model_data.get("model_name")
            if not model_name and provider == "ollama":
                model_name = os.getenv("OLLAMA_DEFAULT_MODEL", "llama3.1:8b")
            models[key] = ModelConfig(
                provider=provider,
                model_name=str(model_name or "llama3.1:8b"),
                api_version=model_data.get("api_version"),
                temperature=float(model_data.get("temperature", 0.3)),
                max_output_tokens=int(model_data.get("max_output_tokens", 2048)),
                top_p=float(model_data.get("top_p", 0.95)),
                timeout_seconds=int(model_data.get("timeout_seconds", 30)),
                max_retries=int(model_data.get("max_retries", 3)),
                retry_delay_seconds=float(model_data.get("retry_delay_seconds", 1.0)),
                extra=model_data.get("extra", {}),
            )

        # Parse modes
        modes = {}
        for mode_key, mode_data in gen_data.get("modes", {}).items():
            modes[mode_key] = ModeConfig(
                mode=GenerationMode(mode_data.get("mode", "context_aware")),
                prompt_id=str(mode_data.get("prompt_id", "context_aware")),
                model_key=str(mode_data.get("model_key", "ollama_default")),
                answer_format=AnswerFormat(mode_data.get("answer_format", "markdown")),
                citation_style=CitationStyle(mode_data.get("citation_style", "inline")),
                include_context_header=bool(mode_data.get("include_context_header", True)),
                max_context_tokens=int(mode_data.get("max_context_tokens", 3000)),
                system_instructions=str(mode_data.get("system_instructions", "")),
            )

        # Load post_processing.yaml
        pp_path = base / "post_processing.yaml"
        pp_data: dict = {}
        if pp_path.exists():
            with open(pp_path) as f:
                pp_data = yaml.safe_load(f) or {}

        post_processing = PostProcessingConfig(
            extract_citations=bool(pp_data.get("extract_citations", True)),
            citation_style=CitationStyle(pp_data.get("citation_style", "inline")),
            validate_grounding=bool(pp_data.get("validate_grounding", True)),
            grounding_check_method=str(pp_data.get("grounding_check_method", "keyword_overlap")),
            remove_artifacts=bool(pp_data.get("remove_artifacts", True)),
            normalize_whitespace=bool(pp_data.get("normalize_whitespace", True)),
            max_answer_length=pp_data.get("max_answer_length"),
            format_as_markdown=bool(pp_data.get("format_as_markdown", True)),
            min_keyword_overlap=float(pp_data.get("min_keyword_overlap", 0.3)),
            min_grounding_confidence=float(pp_data.get("min_grounding_confidence", 0.6)),
        )

        return cls(
            config_version=str(gen_data.get("config_version", "1.0.0")),
            default_mode=GenerationMode(gen_data.get("default_mode", "context_aware")),
            default_model_key=str(gen_data.get("default_model_key", "ollama_default")),
            models=models,
            modes=modes,
            post_processing=post_processing,
            prompt_version=gen_data.get("prompt_version"),
            generation_version=str(gen_data.get("generation_version", "1.0.0")),
        )

    @classmethod
    def _apply_env_overrides(cls, data: dict) -> dict:
        """Apply environment variable overrides."""
        env_mapping = {
            "RAGPIPE_GENERATION_DEFAULT_MODE": "default_mode",
            "RAGPIPE_GENERATION_DEFAULT_MODEL": "default_model_key",
        }
        for env_var, key in env_mapping.items():
            value = os.getenv(env_var)
            if value is not None:
                data[key] = value
        return data

    def get_model_config(self, model_key: str | None = None) -> ModelConfig:
        """Get model configuration by key."""
        key = model_key or self.default_model_key
        if key not in self.models:
            raise ValueError(f"Unknown model key: {key}. Available: {list(self.models.keys())}")
        return self.models[key]

    def get_mode_config(self, mode: GenerationMode) -> ModeConfig:
        """Get mode configuration."""
        key = mode.value
        if key not in self.modes:
            raise ValueError(f"Unknown mode: {key}. Available: {list(self.modes.keys())}")
        return self.modes[key]


# ============================================================================
# Formatter (Separate from Data Contracts)
# ============================================================================


class CitationFormatter:
    """
    Formats session-temporal citations from pure data objects.

    Utility class, not part of the core contracts.
    """

    @staticmethod
    def format_chunk(
        chunk: SessionRetrievedChunk, style: CitationStyle = CitationStyle.INLINE
    ) -> str:
        """Format a SessionRetrievedChunk as a citation string."""
        if style == CitationStyle.NONE:
            return ""
        ts = chunk.timestamp or "unknown"
        if chunk.chunk_type == "structured_record":
            table = chunk.metadata.get("table", "record")
            return f"[{table} record · approx. {ts}]"
        return f"[Session {chunk.session_id or 'unknown'} · approx. {ts}]"

    @staticmethod
    def format_citation(citation: Citation, style: CitationStyle = CitationStyle.INLINE) -> str:
        """Format a Citation object as a citation string."""
        if style == CitationStyle.NONE:
            return ""
        return (
            f"[Session {citation.session_id or 'unknown'} · "
            f"approx. {citation.approximate_timestamp or 'unknown'}]"
        )
