"""SessionWorker — the project_logic §4 chat spine (Phase 3 Step 3.1b).

The model-bound bundle is fully injected (fake agent / router / orchestrator /
pipeline) so no all-MiniLM / cross-encoder model ever loads. The DB + repo +
vector store are real (tmp, keyed-off / plaintext).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.backend.session_worker import ChatResult, SessionWorker
from src.backend.wire import MethodError
from src.common.types import AgenticActionType, AgenticOutput, RetrievalRoute, SessionRetrievedChunk
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


def _ao(
    *, retrieve_needed=False, route=RetrievalRoute.SEMANTIC, confidence=0.9, response="hi there"
):
    return AgenticOutput(
        action_type=AgenticActionType.CONVERSATION,
        confidence=confidence,
        retrieve_needed=retrieve_needed,
        retrieval_route=route,
        search_query="q" if retrieve_needed else None,
        response=response,
    )


def _worker(keyed_db, tmp_path, monkeypatch, *, agent, router=None, orch=None, pipeline=None):
    monkeypatch.setenv("RAGPIPE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAGPIPE_APP_CONFIG_PATH", str(tmp_path / "app_config.json"))
    AppConfig.load(None).set_active_model("llama3.1:8b")
    w = SessionWorker(
        keyed_db,
        app_config_path=str(tmp_path / "app_config.json"),
        agent=agent,
        router=router or FakeRouter(),
        orchestrator=orch or FakeOrchestrator(),
        pipeline=pipeline or FakePipeline(),
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
