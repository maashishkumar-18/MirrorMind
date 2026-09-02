"""
Shared configuration types for the retrieval layer.

Trimmed to the session companion's needs in Phase 1 Step 1.3a — the
document-era ``PipelineConfig`` / ``RequestType`` / ``AssembledContext`` /
``RetrievalResult`` / ``ConfidenceLevel`` and the ``RetrievedChunk`` re-export
went with the study-assistant orchestrator. ``ContextTemplate`` is
transitional (still consumed by ``context_builder.py`` until Step 1.3c
rewrites it for session-citation assembly).
"""

from dataclasses import dataclass
from enum import Enum


class ContextTemplate(str, Enum):
    """
    Predefined context assembly templates. Transitional — the document-era
    variants below are still referenced by the (not-yet-rewritten)
    ``context_builder.py``; Step 1.3c collapses this to session assembly.
    """

    LEARNING = "learning"
    REVISION = "revision"
    QA = "qa"
    PLANNER = "planner"
    QUIZ = "quiz"
    COMPARISON = "comparison"
    RAW = "raw"


@dataclass
class HybridWeights:
    """Weights for combining semantic, keyword, and metadata search results."""

    semantic: float = 0.6
    keyword: float = 0.2
    metadata: float = 0.2

    def __post_init__(self):
        total = self.semantic + self.keyword + self.metadata
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Hybrid weights must sum to 1.0, got {total}")
