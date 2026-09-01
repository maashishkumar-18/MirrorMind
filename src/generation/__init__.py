"""
Generation Layer
Generates mode-specific answers from retrieved context.

Architecture:
    GenerationRequest → PromptBuilder → LLMClient → GenerationResponse
    (Post-processing is applied by the orchestrator before returning)

Modes:
    - Context-Aware: Precise, citation-backed answers
    - Simple Explanation: Easy-to-understand with analogies

Configuration-driven — all prompts, models, and settings from YAML.
Contracts are strongly typed with no business logic in dataclasses.
Formatter utilities are separate from data contracts.
"""

__version__ = "1.0.0"

from src.generation.config import (
    AnswerFormat,
    # Core Contracts — Pure Data
    ChatTurn,
    Citation,
    # Formatter (separate from data contracts)
    CitationFormatter,
    CitationLocationType,
    CitationStyle,
    ConfidenceLevel,
    GeneratedAnswer,
    GenerationConfig,
    # Enums
    GenerationMode,
    GenerationRequest,
    GenerationResponse,
    ModeConfig,
    # Configuration
    ModelConfig,
    ModelInfo,
    PostProcessingConfig,
    # Prompt Contract
    Prompt,
    RetrievalMetadata,
    RetrievedChunk,
    UsageStats,
)

__all__ = [
    # Enums
    "GenerationMode",
    "AnswerFormat",
    "CitationStyle",
    "CitationLocationType",
    "ConfidenceLevel",
    # Core Contracts
    "ChatTurn",
    "RetrievedChunk",
    "RetrievalMetadata",
    "GenerationRequest",
    "UsageStats",
    "ModelInfo",
    "GeneratedAnswer",
    "Citation",
    "GenerationResponse",
    # Prompt Contract
    "Prompt",
    # Configuration
    "ModelConfig",
    "ModeConfig",
    "PostProcessingConfig",
    "GenerationConfig",
    # Formatter
    "CitationFormatter",
]
