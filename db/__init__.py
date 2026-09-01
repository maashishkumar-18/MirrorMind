"""
Session database package (Production Roadmap Phase 0 Step 0.4).

Top-level, parallel to config/ — shared infrastructure, not internal to
any one src/ subpackage. Both ingestion (writes) and retrieval (reads)
depend on this package in Phase 1; neither imports a concrete Pinecone-
style client directly.
"""
