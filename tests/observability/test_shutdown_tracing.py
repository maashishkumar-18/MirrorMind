"""Phase 2 Step 2.3 — observability.tracing.shutdown_tracing()."""

import pytest

import observability.tracing as tracing

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    # tracing caches its client in module globals; isolate every test.
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_client_init_attempted", False, raising=False)
    yield
    tracing._client = None
    tracing._client_init_attempted = False


def test_noop_when_tracing_disabled(monkeypatch):
    monkeypatch.setattr(tracing, "get_langfuse", lambda: None)
    tracing.shutdown_tracing()  # must not raise


def test_calls_client_shutdown_once():
    calls: list[str] = []

    class FakeClient:
        def shutdown(self):
            calls.append("shutdown")

    tracing._client = FakeClient()
    tracing._client_init_attempted = True

    tracing.shutdown_tracing()
    assert calls == ["shutdown"]


def test_swallows_a_raising_shutdown(caplog):
    class BoomClient:
        def shutdown(self):
            raise RuntimeError("langfuse offline")

    tracing._client = BoomClient()
    tracing._client_init_attempted = True

    tracing.shutdown_tracing()  # must not raise
    assert "Langfuse shutdown failed" in caplog.text
