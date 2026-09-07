"""SessionWorker — the project_logic §4 chat spine (Phase 3 Step 3.1b).

The model-bound bundle is fully injected (fake agent / router / orchestrator /
pipeline) so no all-MiniLM / cross-encoder model ever loads. The DB + repo +
vector store are real (tmp, keyed-off / plaintext).
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from db.connection import open_session_db
from src.backend.session_worker import ChatResult, SessionWorker, _minutes_between
from src.backend.wire import MethodError
from src.common.types import AgenticActionType, AgenticOutput, RetrievalRoute, SessionRetrievedChunk
from src.features.base import now_iso
from src.models.app_config import AppConfig


class FakeAgent:
    def __init__(self, output: AgenticOutput):
        self.output = output
        self.calls: list[tuple[str, list]] = []

    def reason(self, query, history=None):
        self.calls.append((query, list(history or [])))
        return self.output


class FakeRouter:
    def __init__(self, chunks=None):
        self.chunks = chunks or []
        self.calls = 0

    def route(self, ao, query):
        self.calls += 1
        return self.chunks


class FakeOrchestrator:
    def __init__(self, *, answer="generated answer", error_type=None):
        self.answer = answer
        self.error_type = error_type
        self.requests: list = []

    def generate(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            answer="" if self.error_type else self.answer,
            error_type=self.error_type,
            is_grounded=not self.error_type,
            grounding_confidence=0.8 if not self.error_type else 0.0,
            citations=[],
            warnings=[],
        )


class FakePipeline:
    def __init__(self, *, boom=False):
        self.calls: list[tuple[str, int]] = []
        self.boom = boom

    def ingest_session(self, session_id, messages, *, timestamp):
        if self.boom:
            raise RuntimeError("ingest exploded")
        self.calls.append((session_id, len(messages)))
        return SimpleNamespace(session_id=session_id)


class FakeSlotExtractor:
    def __init__(self, slots=None):
        self.slots = slots or {}
        self.calls: list = []

    def extract(self, action_type, utterance, now):
        self.calls.append((action_type, utterance))
        return dict(self.slots)


def _ao(
    *,
    action_type=AgenticActionType.CONVERSATION,
    retrieve_needed=False,
    route=RetrievalRoute.SEMANTIC,
    confidence=0.9,
    response="hi there",
):
    return AgenticOutput(
        action_type=action_type,
        confidence=confidence,
        retrieve_needed=retrieve_needed,
        retrieval_route=route,
        search_query="q" if retrieve_needed else None,
        response=response,
    )


def _worker(keyed_db, tmp_path, monkeypatch, *, agent, router=None, orch=None, pipeline=None, **kw):
    monkeypatch.setenv("RAGPIPE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAGPIPE_APP_CONFIG_PATH", str(tmp_path / "app_config.json"))
    AppConfig.load(None).set_active_model("llama3.1:8b")
    kw.setdefault("slot_extractor", FakeSlotExtractor())
    w = SessionWorker(
        keyed_db,
        app_config_path=str(tmp_path / "app_config.json"),
        agent=agent,
        router=router or FakeRouter(),
        orchestrator=orch or FakeOrchestrator(),
        pipeline=pipeline or FakePipeline(),
        **kw,
    )
    w.start()
    assert w.wait_ready(30)
    return w


def _call(w, fn):
    return w.submit(fn).result(timeout=30)


def test_send_persists_two_messages_and_short_circuits_without_retrieval(
    keyed_db, tmp_path, monkeypatch
):
    agent = FakeAgent(_ao(retrieve_needed=False, response="direct reply"))
    orch = FakeOrchestrator()
    w = _worker(keyed_db, tmp_path, monkeypatch, agent=agent, orch=orch)
    try:
        res: ChatResult = _call(w, lambda: w.send("hello"))
        assert res.answer == "direct reply"  # ao.response returned directly
        assert res.retrieve_needed is False and res.tier == 1
        assert orch.requests == []  # orchestrator NOT called on the short-circuit path
        _, rows = _call(w, lambda: w.history(res.session_id))
        assert [r["role"] for r in rows] == ["user", "assistant"]
        assert [r["content"] for r in rows] == ["hello", "direct reply"]
    finally:
        w.stop(timeout=30)


def test_send_with_retrieval_calls_router_and_orchestrator(keyed_db, tmp_path, monkeypatch):
    chunk = SessionRetrievedChunk(
        chunk_id="s1::primary", content="c", raw_content="c", session_id="s1"
    )
    agent = FakeAgent(_ao(retrieve_needed=True, route=RetrievalRoute.SEMANTIC))
    router = FakeRouter(chunks=[chunk])
    orch = FakeOrchestrator(answer="grounded answer")
    w = _worker(keyed_db, tmp_path, monkeypatch, agent=agent, router=router, orch=orch)
    try:
        res = _call(w, lambda: w.send("what did we say about X?"))
        assert router.calls == 1
        assert res.answer == "grounded answer" and res.is_grounded is True
        req = orch.requests[0]
        assert list(req.chunks) == [chunk]  # SessionRetrievedChunk passed through
        assert all(isinstance(c, SessionRetrievedChunk) for c in req.chunks)
    finally:
        w.stop(timeout=30)


def test_conversation_history_is_populated_on_the_second_turn(keyed_db, tmp_path, monkeypatch):
    agent = FakeAgent(_ao(retrieve_needed=True))
    orch = FakeOrchestrator()
    w = _worker(keyed_db, tmp_path, monkeypatch, agent=agent, router=FakeRouter([]), orch=orch)
    try:
        _call(w, lambda: w.send("first message"))
        _call(w, lambda: w.send("second message"))
        # agent saw prior turns on call 2; orchestrator got a non-empty history
        assert agent.calls[1][1] and agent.calls[1][1][0].content == "first message"
        assert orch.requests[1].conversation_history
        assert orch.requests[1].conversation_history[0].content == "first message"
    finally:
        w.stop(timeout=30)


def test_no_active_model_raises_method_error(keyed_db, tmp_path, monkeypatch):
    monkeypatch.setenv("RAGPIPE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAGPIPE_APP_CONFIG_PATH", str(tmp_path / "app_config.json"))
    w = SessionWorker(
        keyed_db,
        app_config_path=str(tmp_path / "app_config.json"),
        agent=FakeAgent(_ao()),
        router=FakeRouter(),
        orchestrator=FakeOrchestrator(),
        pipeline=FakePipeline(),
    )
    w.start()
    assert w.wait_ready(30)
    try:
        with pytest.raises(MethodError) as ei:
            _call(w, lambda: w.send("hi"))
        assert ei.value.code == "no_model_active"
    finally:
        w.stop(timeout=30)


def test_new_conversation_finalizes_and_returns_fresh_id(keyed_db, tmp_path, monkeypatch):
    w = _worker(keyed_db, tmp_path, monkeypatch, agent=FakeAgent(_ao()))
    try:
        first = _call(w, lambda: w.send("hi")).session_id
        second = _call(w, w.new_conversation)
        assert second != first
        third = _call(w, lambda: w.send("hi again")).session_id
        assert third == second
    finally:
        w.stop(timeout=30)


def test_reingest_runs_as_a_followup_and_updates_the_store(keyed_db, tmp_path, monkeypatch):
    pipeline = FakePipeline()
    w = _worker(keyed_db, tmp_path, monkeypatch, agent=FakeAgent(_ao()), pipeline=pipeline)
    try:
        res = _call(w, lambda: w.send("remember this"))
        # drain the follow-up
        _call(w, lambda: None)
        assert pipeline.calls and pipeline.calls[0][0] == res.session_id
    finally:
        w.stop(timeout=30)


def test_a_raising_reingest_is_swallowed(keyed_db, tmp_path, monkeypatch):
    w = _worker(
        keyed_db, tmp_path, monkeypatch, agent=FakeAgent(_ao()), pipeline=FakePipeline(boom=True)
    )
    try:
        _call(w, lambda: w.send("one"))
        _call(w, lambda: None)  # drain
        res = _call(w, lambda: w.send("two"))  # worker still serving
        assert res.answer
    finally:
        w.stop(timeout=30)


def test_stop_drains_a_pending_reingest(keyed_db, tmp_path, monkeypatch):
    pipeline = FakePipeline()
    w = _worker(keyed_db, tmp_path, monkeypatch, agent=FakeAgent(_ao()), pipeline=pipeline)
    _call(w, lambda: w.send("persist me"))
    assert w.stop(timeout=30) is True
    assert pipeline.calls  # the follow-up ran before the thread exited


# -- 3.1c: idle auto-close + reconciliation -------------------------------------


def test_idle_close_starts_a_new_session(keyed_db, tmp_path, monkeypatch):
    pipeline = FakePipeline()
    w = _worker(
        keyed_db, tmp_path, monkeypatch, agent=FakeAgent(_ao()), pipeline=pipeline, idle_minutes=30
    )
    try:
        first = _call(w, lambda: w.send("hello")).session_id
        _call(w, lambda: None)  # drain first re-ingest
        w._last_activity = "2020-01-01T00:00:00+00:00"  # force the gap wide open
        second = _call(w, lambda: w.send("i'm back")).session_id
        assert second != first
        _call(w, lambda: None)  # drain

        conn = open_session_db(keyed_db)
        try:
            row = conn.execute(
                "SELECT ended_at, close_reason FROM sessions WHERE id = ?", (first,)
            ).fetchone()
        finally:
            conn.close()
        assert row["ended_at"] is not None and row["close_reason"] == "idle_timeout"
        # the closed session got a final re-ingest keyed to its own id
        assert any(sid == first for sid, _ in pipeline.calls)
    finally:
        w.stop(timeout=30)


def test_not_idle_keeps_the_same_session(keyed_db, tmp_path, monkeypatch):
    w = _worker(keyed_db, tmp_path, monkeypatch, agent=FakeAgent(_ao()), idle_minutes=60)
    try:
        first = _call(w, lambda: w.send("a")).session_id
        second = _call(w, lambda: w.send("b")).session_id
        assert first == second
    finally:
        w.stop(timeout=30)


def test_last_activity_is_stamped_after_processing(keyed_db, tmp_path, monkeypatch):
    class SlowAgent(FakeAgent):
        def reason(self, query, history=None):
            time.sleep(0.3)
            return super().reason(query, history)

    w = _worker(keyed_db, tmp_path, monkeypatch, agent=SlowAgent(_ao()))
    try:
        before = now_iso()
        _call(w, lambda: w.send("slow one"))
        # the 0.3s spent in reason() is *before* _last_activity is stamped
        assert _minutes_between(before, w._last_activity) * 60 >= 0.25
    finally:
        w.stop(timeout=30)


def test_new_conversation_reingest_the_closed_session(keyed_db, tmp_path, monkeypatch):
    pipeline = FakePipeline()
    w = _worker(keyed_db, tmp_path, monkeypatch, agent=FakeAgent(_ao()), pipeline=pipeline)
    try:
        closed = _call(w, lambda: w.send("first")).session_id
        _call(w, lambda: None)  # drain the message re-ingest
        pipeline.calls.clear()
        _call(w, w.new_conversation)
        _call(w, lambda: None)  # drain the close re-ingest
        assert any(sid == closed for sid, _ in pipeline.calls)
    finally:
        w.stop(timeout=30)


def test_prologue_finalizes_a_dangling_session(keyed_db, tmp_path, monkeypatch):
    conn = open_session_db(keyed_db)
    conn.execute(
        "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES "
        "('orphan', 't', 't', 't')"
    )
    conn.commit()
    conn.close()

    w = _worker(keyed_db, tmp_path, monkeypatch, agent=FakeAgent(_ao()))
    try:
        conn = open_session_db(keyed_db)
        row = conn.execute(
            "SELECT ended_at, close_reason FROM sessions WHERE id = 'orphan'"
        ).fetchone()
        conn.close()
        assert row["ended_at"] is not None and row["close_reason"] == "app_shutdown"
    finally:
        w.stop(timeout=30)


def test_reconciliation_and_on_ready(keyed_db, tmp_path, monkeypatch):
    # seed one overdue reminder
    conn = open_session_db(keyed_db)
    conn.execute(
        "INSERT INTO reminders (id, title, scheduled_time, created_at, updated_at) VALUES "
        "('r1', 'call the dentist', '2000-01-01T00:00:00+00:00', 't', 't')"
    )
    conn.commit()
    conn.close()

    seen: list = []
    w = _worker(keyed_db, tmp_path, monkeypatch, agent=FakeAgent(_ao()), on_ready=seen.append)
    try:
        assert len(seen) == 1 and [r.id for r in seen[0].overdue] == ["r1"]
        recon = _call(w, w.reconciliation)
        assert [r.id for r in recon.overdue] == ["r1"]
    finally:
        w.stop(timeout=30)


# -- 3.1d: slot extraction + tier dispatch -------------------------------------

REM = AgenticActionType.REMINDER


def _rem_slots():
    return {"title": "call the dentist", "scheduled_time": "2026-09-10T15:00:00+00:00"}


def test_tier1_actionable_creates_the_entity_and_skips_generation(keyed_db, tmp_path, monkeypatch):
    orch = FakeOrchestrator()
    slot = FakeSlotExtractor(_rem_slots())
    w = _worker(
        keyed_db,
        tmp_path,
        monkeypatch,
        agent=FakeAgent(_ao(action_type=REM, confidence=0.95)),
        orch=orch,
        slot_extractor=slot,
    )
    try:
        res = _call(w, lambda: w.send("remind me to call the dentist thursday at 3"))
        assert res.tier == 1 and res.feature and res.feature["kind"] == "reminder"
        assert orch.requests == []  # no generation on the Tier-1 path
        assert slot.calls and slot.calls[0][0] == REM
        conn = open_session_db(keyed_db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1
            roles = [r[0] for r in conn.execute("SELECT role FROM messages ORDER BY turn_index")]
        finally:
            conn.close()
        assert roles == ["user", "assistant"]
    finally:
        w.stop(timeout=30)


def test_tier2_disambiguation_then_confirm(keyed_db, tmp_path, monkeypatch):
    w = _worker(
        keyed_db,
        tmp_path,
        monkeypatch,
        agent=FakeAgent(_ao(action_type=REM, confidence=0.78, response="I could set a reminder.")),
        slot_extractor=FakeSlotExtractor(_rem_slots()),
    )
    try:
        res = _call(w, lambda: w.send("dentist thursday"))
        assert res.tier == 2 and res.disambiguation
        pa_id = res.disambiguation["pending_action_id"]
        assert "reminder" in res.disambiguation["options"]
        conn = open_session_db(keyed_db)
        assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0
        conn.close()

        # stale id rejected
        with pytest.raises(MethodError):
            _call(w, lambda: w.confirm_action("pa-bogus", "reminder"))

        confirmed = _call(w, lambda: w.confirm_action(pa_id, "reminder"))
        assert confirmed.feature and confirmed.feature["kind"] == "reminder"
        conn = open_session_db(keyed_db)
        assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 1
        conn.close()
    finally:
        w.stop(timeout=30)


def test_confirm_action_dismiss_creates_nothing(keyed_db, tmp_path, monkeypatch):
    w = _worker(
        keyed_db,
        tmp_path,
        monkeypatch,
        agent=FakeAgent(_ao(action_type=REM, confidence=0.75, response="Just chatting?")),
        slot_extractor=FakeSlotExtractor(_rem_slots()),
    )
    try:
        res = _call(w, lambda: w.send("hmm dentist"))
        out = _call(
            w, lambda: w.confirm_action(res.disambiguation["pending_action_id"], "conversation")
        )
        assert out.answer == "Just chatting?" and out.feature is None
        conn = open_session_db(keyed_db)
        assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0
        conn.close()
    finally:
        w.stop(timeout=30)


def test_new_send_dismisses_a_pending_action(keyed_db, tmp_path, monkeypatch):
    w = _worker(
        keyed_db,
        tmp_path,
        monkeypatch,
        agent=FakeAgent(_ao(action_type=REM, confidence=0.78)),
        slot_extractor=FakeSlotExtractor(_rem_slots()),
    )
    try:
        first = _call(w, lambda: w.send("dentist thursday")).disambiguation["pending_action_id"]
        res2 = _call(w, lambda: w.send("actually never mind, how are you"))
        assert res2.dismissed_pending is True  # the first pending action was discarded
        # (this message re-triggers Tier 2 with a *new* id — a fresh classification)
        assert w._pending_action is not None and w._pending_action.id != first
    finally:
        w.stop(timeout=30)


def test_tier3_clarification_no_pending_no_row(keyed_db, tmp_path, monkeypatch):
    w = _worker(
        keyed_db,
        tmp_path,
        monkeypatch,
        agent=FakeAgent(_ao(action_type=REM, confidence=0.60)),
        slot_extractor=FakeSlotExtractor(_rem_slots()),
    )
    try:
        res = _call(w, lambda: w.send("something about the dentist maybe"))
        assert res.tier == 3 and res.disambiguation is None and "Remind me" in res.answer
        assert w._pending_action is None
        conn = open_session_db(keyed_db)
        assert conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0
        conn.close()
    finally:
        w.stop(timeout=30)


def test_pending_action_cleared_on_new_conversation(keyed_db, tmp_path, monkeypatch):
    w = _worker(
        keyed_db,
        tmp_path,
        monkeypatch,
        agent=FakeAgent(_ao(action_type=REM, confidence=0.78)),
        slot_extractor=FakeSlotExtractor(_rem_slots()),
    )
    try:
        _call(w, lambda: w.send("dentist"))
        assert w._pending_action is not None
        _call(w, w.new_conversation)
        assert w._pending_action is None
    finally:
        w.stop(timeout=30)
