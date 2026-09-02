"""
Retrieval Layer

Session-companion retrieval: a single agentic Ollama call produces an
``AgenticOutput``; the ``RetrievalRouter`` (Phase 1 Step 1.3b) dispatches to
the semantic path (``HybridSearch`` + ``Reranker`` over the local
``SQLiteVectorStore``), the structured path (``StructuredTableSearch`` over
the FTS5 tables), or both.
"""

__version__ = "2.0.0"
