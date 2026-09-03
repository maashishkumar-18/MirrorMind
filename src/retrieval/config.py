"""
Shared configuration types for the retrieval layer.

Trimmed to the session companion's needs across Phase 1 Step 1.3 — the
document-era ``PipelineConfig`` / ``RequestType`` / ``AssembledContext`` /
``RetrievalResult`` / ``ConfidenceLevel`` went with the study-assistant
orchestrator (1.3a), and ``ContextTemplate`` went with the template-driven
``ContextBuilder`` (1.3c). ``HybridWeights`` is all that remains — the
semantic/keyword/metadata blend for ``HybridSearch``.
"""

from dataclasses import dataclass


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
