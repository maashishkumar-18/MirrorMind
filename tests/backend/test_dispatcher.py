"""Dispatcher error handling + routing (Phase 3 Step 3.1a)."""

from __future__ import annotations

from src.backend import handlers
from tests.backend.conftest import CollectingTransport, envelope


def _errors(transport: CollectingTransport):
    return [e["payload"] for e in transport.by_type("error")]


def test_version_mismatch_is_reported_not_crashed(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw(envelope("app.status", "r1", version=999))
    d.handle_raw(envelope("app.status", "r2"))  # loop survives, next msg still processed
    d.close(wait=True)

    errs = _errors(transport)
    assert errs and errs[0]["code"] == "version_mismatch"
    assert transport.by_type("error")[0]["request_id"] == "r1"
    assert transport.by_type("response")  # r2 got through


def test_version_mismatch_without_request_id_uses_placeholder(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw({"version": 2, "message_type": "request", "timestamp": "t", "payload": {}})
    assert transport.by_type("error")[0]["request_id"] == "-"


def test_malformed_envelope_is_validation_error(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw({"version": 1, "message_type": "banana", "request_id": "r1", "timestamp": "t"})
    assert _errors(transport)[0]["code"] == "validation_error"


def test_non_request_envelope_rejected(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw(
        {
            "version": 1,
            "message_type": "response",
            "request_id": "r1",
            "timestamp": "t",
            "payload": {"method": "app.status"},
        }
    )
    assert _errors(transport)[0]["code"] == "validation_error"


def test_unknown_method(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw(envelope("does.not.exist"))
    assert _errors(transport)[0]["code"] == "unknown_method"


def test_bad_params_is_validation_error(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw(envelope("app.status", params={"unexpected": 1}))
    d.close(wait=True)
    assert _errors(transport)[0]["code"] == "validation_error"


def test_none_line_is_validation_error(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw(None)
    assert _errors(transport)[0]["code"] == "validation_error"


def test_handler_crash_becomes_internal_error_and_loop_survives(dispatcher_factory, monkeypatch):
    d, transport, _ = dispatcher_factory()

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(handlers.HANDLERS, "model.catalog", boom)
    d.handle_raw(envelope("model.catalog", "r1"))
    d.handle_raw(envelope("app.status", "r2"))
    d.close(wait=True)

    assert _errors(transport)[0]["code"] == "internal_error"
    assert any(e["request_id"] == "r2" for e in transport.by_type("response"))


def test_degraded_mode_blocks_non_whitelisted_methods(dispatcher_factory):
    d, transport, _ = dispatcher_factory(degraded=True)
    d.handle_raw(envelope("health.check", "r1"))
    d.handle_raw(envelope("app.status", "r2"))
    d.close(wait=True)

    assert _errors(transport)[0]["code"] == "integrity_failed"
    assert transport.by_type("response")[0]["payload"]["method"] == "app.status"


def test_app_shutdown_acks_and_sets_flag(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw(envelope("app.shutdown", "r1"))
    assert d.shutdown_requested.is_set()
    resp = transport.by_type("response")[0]
    assert resp["payload"]["result"] == {"stopping": True}


def test_worker_method_is_routed_to_the_worker(dispatcher_factory):
    from tests.backend.conftest import FakeSessionWorker

    fake = FakeSessionWorker()
    d, transport, _ = dispatcher_factory(worker=fake)
    d.handle_raw(envelope("chat.new", "r1"))
    d.close(wait=True)
    resp = transport.by_type("response")[0]
    assert resp["payload"]["method"] == "chat.new"
    assert resp["payload"]["result"]["session_id"] == "session_0000"


def test_worker_method_without_a_worker_is_unavailable(dispatcher_factory):
    d, transport, _ = dispatcher_factory()  # no worker
    d.handle_raw(envelope("chat.send", "r1", {"text": "hi"}))
    d.close(wait=True)
    assert _errors(transport)[0]["code"] == "unavailable"
