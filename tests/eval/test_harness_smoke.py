"""
Always-on CI smoke test for the session golden-eval harness
(eval/run_eval.py) — Phase 1 Step 1.4b.

Runs every commit. Validates the shape of the committed fixtures
(golden_qa_set.json + seed_corpus.json), then bootstraps the real pipeline
against the seed corpus and pushes two golden items through it with the LLM
calls stubbed — the agent, the generation model, the cross-encoder and the
llm_check grounding call are all faked, so there is no Ollama and no network.
The gated calibration run (real model) is the separate workflow_dispatch
``eval`` job in .github/workflows/ci.yml.
"""

import json

import numpy as np
import pytest

from eval.run_eval import (
    GOLDEN_SET_PATH,
    SEED_CORPUS_PATH,
    aggregate,
    bootstrap_pipeline,
    build_report,
    render_markdown,
    run_pipeline_phase,
    sample_items,
    score_records,
)
from src.common.types import (
    AgenticActionType,
    AgenticOutput,
    RetrievalRoute,
)
from src.generation.config import (
    GeneratedAnswer,
    ModelInfo,
    UsageStats,
)

pytestmark = pytest.mark.integration

_VALID_CATEGORIES = {
    "conversation_recall",
    "reminder",
    "todo",
    "meeting",
    "schedule",
    "summary_request",
    "paraphrase_recall",
    "action_request",
    "conversation",
    "unanswerable",
}
_ITEM_KEYS = {
    "id",
    "question",
    "ground_truth",
    "category",
    "answerable",
    "expected_retrieve_needed",
    "expected_route",
    "expected_action_type",
    "disambiguation_tier",
    "is_temporal",
    "source_ref",
    "notes",
}


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_golden_set_schema_and_coverage():
    data = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))
    items = data["items"]
    routes = {r.value for r in RetrievalRoute}
    actions = {a.value for a in AgenticActionType}

    ids = set()
    for it in items:
        assert set(it) == _ITEM_KEYS, (it["id"], set(it) ^ _ITEM_KEYS)
        assert it["id"] not in ids
        ids.add(it["id"])
        assert it["category"] in _VALID_CATEGORIES, it["id"]
        assert it["expected_action_type"] in actions, it["id"]
        assert it["expected_route"] in routes or it["expected_route"] is None, it["id"]
        assert isinstance(it["answerable"], bool)
        if it["answerable"]:
            assert it["ground_truth"], it["id"]
        else:
            assert it["ground_truth"] is None, it["id"]
        if it["expected_retrieve_needed"]:
            assert it["expected_route"] in routes, it["id"]
        else:
            assert it["expected_route"] is None, it["id"]
        assert it["disambiguation_tier"] in (1, 2, 3, 4, None), it["id"]

    counts = data["_meta"]["counts"]
    assert counts["total"] == len(items)
    assert counts["answerable"] == sum(1 for i in items if i["answerable"])
    assert counts["unanswerable"] == sum(1 for i in items if i["category"] == "unanswerable")
    assert counts["paraphrase_recall"] == sum(
        1 for i in items if i["category"] == "paraphrase_recall"
    )
    assert counts["temporal"] == sum(1 for i in items if i["is_temporal"])

    # Roadmap Step 1.4 coverage deliverables.
    assert {i["category"] for i in items} >= {
        "reminder",
        "todo",
        "meeting",
        "schedule",
        "summary_request",
        "paraphrase_recall",
        "unanswerable",
    }
    assert {i["disambiguation_tier"] for i in items} >= {1, 2, 3, 4}
    assert counts["paraphrase_recall"] >= 10
    assert counts["unanswerable"] >= 17
    assert counts["temporal"] >= 10


def test_seed_corpus_schema():
    corpus = json.loads(SEED_CORPUS_PATH.read_text(encoding="utf-8"))
    session_ids = {s["id"] for s in corpus["sessions"]}
    assert session_ids

    for s in corpus["sessions"]:
        assert s["started_at"]
        assert s["messages"] and all(m["role"] in ("user", "assistant") for m in s["messages"])
        meta = s["metadata"]
        assert isinstance(meta["topics"], list) and meta["topics"]
        assert isinstance(meta["action_types"], list)

    for table in ("reminders", "todos", "meeting_notes"):
        for row in corpus.get(table, []):
            assert row["id"]
            assert row.get("session_id") is None or row["session_id"] in session_ids

    schedule_ids = {sc["id"] for sc in corpus.get("schedules", [])}
    for si in corpus.get("schedule_items", []):
        assert si["schedule_id"] in schedule_ids


# ---------------------------------------------------------------------------
# Wiring smoke — bootstrap + 2-item pass, fully stubbed
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Returns a canned AgenticOutput keyed off a keyword in the question."""

    def reason(self, query, conversation_history=None):
        q = query.lower()
        if "good morning" in q or "how's your day" in q:
            return AgenticOutput(
                action_type=AgenticActionType.CONVERSATION,
                confidence=0.2,
                retrieve_needed=False,
                retrieval_route=RetrievalRoute.SEMANTIC,
                search_query=None,
                response="Hello!",
            )
        return AgenticOutput(
            action_type=AgenticActionType.RETRIEVAL_QUERY,
            confidence=0.9,
            retrieve_needed=True,
            retrieval_route=RetrievalRoute.HYBRID,
            search_query=query,
            response="",
        )


class _FakeLLMClient:
    def generate(self, prompt, model_config):
        return GeneratedAnswer(
            content="Here is what I found. [Session s1 · approx. 2026-02-02T09:15:00Z]",
            model_info=ModelInfo(provider="ollama", model_name="stub"),
            usage=UsageStats(input_tokens=10, output_tokens=8, total_tokens=18),
            generation_time_ms=1.0,
        )


class _FakeGrounding:
    method = "llm_check"

    def validate(self, answer, chunks):
        return True, 0.8


class _FakeEmbedder:
    def embed_query(self, text):
        rng = np.random.default_rng(abs(hash(text)) % (2**32))
        return rng.random(384, dtype=np.float32)


def test_bootstrap_and_two_item_pass():
    from src.generation.orchestrator import GenerationOrchestrator

    pipeline = bootstrap_pipeline(
        agent=_FakeAgent(),
        orchestrator=GenerationOrchestrator(llm_client=_FakeLLMClient()),
        grounding_validator=_FakeGrounding(),
        embedder=_FakeEmbedder(),
        stub_rerank=True,
    )
    try:
        items = json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))["items"]
        subset = [
            next(i for i in items if i["id"] == "q001"),
            next(i for i in items if i["id"] == "q058"),
        ]

        records = run_pipeline_phase(pipeline, subset, env="test")
        score_records(pipeline, records)
        assert len(records) == 2

        agg = aggregate(records)
        assert set(agg) >= {
            "faithfulness_mean",
            "agentic_routing",
            "refusal_rate",
            "temporal_accuracy",
        }
        assert 0.0 <= agg["agentic_routing"] <= 1.0

        class _Args:
            total_items = len(items)
            seed = 42
            golden_set = GOLDEN_SET_PATH
            calibrate = True

        report = build_report(records, agg, _Args(), 0.1, None, ["stub"])
        assert render_markdown(report)
        assert len(report["items"]) == 2
    finally:
        pipeline.close()


def test_sample_items_is_deterministic():
    items = [{"id": f"q{i}"} for i in range(50)]
    assert sample_items(items, 10, 7) == sample_items(items, 10, 7)
    assert len(sample_items(items, 10, 7)) == 10
    assert sample_items(items, None, 7) == items
