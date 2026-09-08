"""Unit tests for src/models/ollama_manager.py (Phase 1 Step 1.6).

Runs a tiny stub HTTP server in a background thread — no real Ollama daemon
(same approach as tests/common/test_ollama_adapter.py).
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.models.ollama_manager import OllamaManager, normalize_model_name
from src.models.types import ModelStatus

pytestmark = pytest.mark.unit

_TAGS = {
    "models": [
        {
            "name": "llama3.1:8b",
            "size": 4920753328,
            "digest": "abc123",
            "modified_at": "2026-09-04T17:48:47Z",
            "details": {
                "family": "llama",
                "parameter_size": "8.0B",
                "quantization_level": "Q4_K_M",
            },
        },
        {
            "name": "mistral:latest",
            "size": 4110000000,
            "digest": "def456",
            "modified_at": "2026-09-01T00:00:00Z",
            "details": {"family": "llama", "parameter_size": "7.2B", "quantization_level": "Q4_0"},
        },
    ]
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self):
        status, body = self.server.responder(self.command, self.path)  # type: ignore[attr-defined]
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        self._reply()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._reply()

    def do_DELETE(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self._reply()


def _default_responder(method, path):
    if method == "GET" and path == "/api/tags":
        return 200, json.dumps(_TAGS)
    if method == "POST" and path == "/api/show":
        return 200, json.dumps({"details": {"family": "llama"}, "model_info": {}})
    if method == "DELETE" and path == "/api/delete":
        return 200, "{}"
    return 404, "{}"


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.responder = _default_responder  # type: ignore[attr-defined]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd
    httpd.shutdown()


@pytest.fixture
def manager(server):
    host = f"http://127.0.0.1:{server.server_address[1]}"
    return OllamaManager(host=host, timeout_seconds=5)


def test_normalize_model_name():
    assert normalize_model_name("mistral") == "mistral:latest"
    assert normalize_model_name("llama3.1:8b") == "llama3.1:8b"


def test_is_running_true(manager):
    assert manager.is_running() is True


def test_is_running_false_on_dead_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert OllamaManager(host=f"http://127.0.0.1:{port}", timeout_seconds=2).is_running() is False


def test_get_installed_models_parses_details(manager):
    models = manager.get_installed_models()
    assert {m.name for m in models} == {"llama3.1:8b", "mistral:latest"}
    llama = next(m for m in models if m.name == "llama3.1:8b")
    assert llama.size_bytes == 4920753328
    assert llama.family == "llama"
    assert llama.parameter_size == "8.0B"
    assert llama.quantization_level == "Q4_K_M"


def test_get_model_status_active_vs_available(manager):
    assert manager.get_model_status("llama3.1:8b", active_model="llama3.1:8b") == ModelStatus.ACTIVE
    assert (
        manager.get_model_status("mistral:latest", active_model="llama3.1:8b")
        == ModelStatus.AVAILABLE
    )
    # tagless request still matches the tagged installed name
    assert manager.get_model_status("mistral", active_model="mistral") == ModelStatus.ACTIVE


def test_get_model_status_not_installed(manager):
    assert (
        manager.get_model_status("gemma2:9b", active_model="llama3.1:8b")
        == ModelStatus.NOT_INSTALLED
    )


def test_get_model_status_downloading_from_tracker(server):
    tracker = {"gemma2:9b"}
    mgr = OllamaManager(
        host=f"http://127.0.0.1:{server.server_address[1]}",
        timeout_seconds=5,
        active_downloads=tracker,
    )
    assert mgr.get_model_status("gemma2:9b", active_model="llama3.1:8b") == ModelStatus.DOWNLOADING


def test_get_model_statuses_makes_one_tags_call(server):
    """Regression: `_model_status` used to call `get_model_status` per name,
    each re-fetching `/api/tags` — ~16s for the catalog on a slow localhost."""
    calls: list[str] = []
    base = _default_responder

    def counting(method, path):
        if method == "GET" and path == "/api/tags":
            calls.append(path)
        return base(method, path)

    server.responder = counting  # type: ignore[attr-defined]
    mgr = OllamaManager(host=f"http://127.0.0.1:{server.server_address[1]}", timeout_seconds=5)

    result = mgr.get_model_statuses(
        ["llama3.1:8b", "mistral:latest", "gemma2:9b", "qwen2.5:7b"],
        active_model="llama3.1:8b",
    )
    assert result["llama3.1:8b"] == ModelStatus.ACTIVE
    assert result["mistral:latest"] == ModelStatus.AVAILABLE
    assert result["gemma2:9b"] == ModelStatus.NOT_INSTALLED
    assert result["qwen2.5:7b"] == ModelStatus.NOT_INSTALLED
    assert len(calls) == 1

    # and none at all when the caller supplies the installed set
    calls.clear()
    mgr.get_model_statuses(["llama3.1:8b"], active_model="llama3.1:8b", installed={"llama3.1:8b"})
    assert calls == []


def test_delete_model_200_and_404(manager, server):
    assert manager.delete_model("llama3.1:8b") is True
    server.responder = lambda m, p: (404, "{}")  # type: ignore[attr-defined]
    assert manager.delete_model("ghost:1b") is False


def test_show_model_returns_body_or_none(manager, server):
    assert manager.show_model("llama3.1:8b") is not None
    server.responder = lambda m, p: (404, "{}")  # type: ignore[attr-defined]
    assert manager.show_model("ghost:1b") is None
