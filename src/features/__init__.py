"""
Feature handlers (Production Roadmap Phase 1 Step 1.5).

CRUD capabilities over the structured tables — ``todos`` / ``meeting_notes`` /
``schedule_items`` / ``summaries`` (Step 1.5a) and ``reminders`` + the local
scheduler thread (Step 1.5b). Each handler takes structured input; the
natural-language slot extraction and the ``AgenticOutput`` → handler dispatch
belong to the Phase 3 backend orchestrator.
"""
