"""
Generation Layer
Generates mode-specific answers from retrieved session context.

Architecture:
    GenerationRequest → PromptBuilder → LLMClient → GenerationResponse
    (Post-processing is applied by the orchestrator before returning)

Modes:
    - Context-Aware: precise, memory-grounded answers
    - Simple Explanation: easy-to-understand with analogies

Configuration-driven — all prompts, models, and settings from YAML.
Contracts are strongly typed with no business logic in dataclasses.
"""

__version__ = "2.0.0"

from src.generation.config import (
    AnswerFormat,
    # Core Contracts — Pure Data
    ChatTurn,
    Citation,
    # Formatter (separate from data contracts)
    CitationFormatter,
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
    UsageStats,
)

__all__ = [
    # Enums
    "GenerationMode",
    "AnswerFormat",
    "CitationStyle",
    "ConfidenceLevel",
    # Core Contracts
    "ChatTurn",
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
