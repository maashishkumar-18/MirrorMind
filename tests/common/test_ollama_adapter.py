"""
Unit tests for OllamaAdapter (src/common/llm_client.py) — Phase 1 Step 1.1.

Runs a tiny stub HTTP server in a background thread (no real Ollama daemon).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.common.llm_client import (
    ModelNotDownloadedError,
    ModelNotLoadedError,
    OllamaAdapter,
    OllamaNotRunningError,
    ProviderAPIError,
    ProviderRegistry,
    ProviderTimeoutError,
    simple_generate,
)
from src.generation.config import ModelConfig, Prompt

pytestmark = pytest.mark.unit


def _prompt(user="hello", system=""):
    return Prompt(
        system_prompt=system,
        user_prompt=user,
        template_id="t",
        template_version="1.0.0",
        context_token_count=0,
        system_token_count=0,
        user_token_count=0,
        total_token_count=0,
        prompt_id="p",
        request_id="r",
    )


def _config(model="llama3.1:8b"):
    return ModelConfig(provider="ollama", model_name=model)


class _Handler(BaseHTTPRequestHandler):
    # set by each test via the server instance
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        status, body = self.server.responder()  # type: ignore[attr-defined]
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    httpd.responder = lambda: (200, "{}")  # type: ignore[attr-defined]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd
    httpd.shutdown()


@pytest.fixture
def adapter(server):
    host = f"http://127.0.0.1:{server.server_address[1]}"
    return OllamaAdapter(host=host)


def test_host_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert OllamaAdapter().host == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_HOST", "http://box:1234/")
    assert OllamaAdapter().host == "http://box:1234"


def test_happy_path_returns_generated_answer(server, adapter):
    server.responder = lambda: (  # type: ignore[attr-defined]
        200,
        json.dumps(
            {
                "message": {"role": "assistant", "content": "hi there"},
                "prompt_eval_count": 12,
                "eval_count": 5,
                "done_reason": "stop",
            }
        ),
    )
    answer = adapter.generate(_prompt(), _config(), timeout_seconds=5)
    assert answer.content == "hi there"
    assert answer.usage.input_tokens == 12
    assert answer.usage.output_tokens == 5
    assert answer.usage.total_tokens == 17
    assert answer.finish_reason == "stop"


def test_model_not_found_maps_to_model_not_downloaded(server, adapter):
    server.responder = lambda: (404, json.dumps({"error": 'model "x" not found, try pulling it'}))  # type: ignore[attr-defined]
    with pytest.raises(ModelNotDownloadedError) as ei:
        adapter.generate(_prompt(), _config("x"), timeout_seconds=5)
    assert "isn't downloaded" in str(ei.value)


def test_bare_404_without_model_message_is_generic_api_error(server, adapter):
    # e.g. an nginx 404 page in front of a wrong host — must NOT claim the
    # model needs downloading.
    server.responder = lambda: (404, "<html><body>404 Not Found</body></html>")  # type: ignore[attr-defined]
    with pytest.raises(ProviderAPIError) as ei:
        adapter.generate(_prompt(), _config(), timeout_seconds=5)
    assert not isinstance(ei.value, ModelNotDownloadedError)
    assert "404" in str(ei.value)


def test_loading_body_maps_to_model_not_loaded(server, adapter):
    server.responder = lambda: (503, json.dumps({"error": "model is currently loading"}))  # type: ignore[attr-defined]
    with pytest.raises(ModelNotLoadedError):
        adapter.generate(_prompt(), _config(), timeout_seconds=5)


def test_connection_refused_maps_to_ollama_not_running():
    import socket

    # Bind then release a port so nothing is listening on it -> connection refused.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()

    adapter = OllamaAdapter(host=f"http://127.0.0.1:{free_port}")
    with pytest.raises(OllamaNotRunningError):
        adapter.generate(_prompt(), _config(), timeout_seconds=5)


def test_slow_response_maps_to_timeout(server, adapter):
    import time

    def slow():
        time.sleep(1.5)
        return (200, "{}")

    server.responder = slow  # type: ignore[attr-defined]
    with pytest.raises(ProviderTimeoutError):
        adapter.generate(_prompt(), _config(), timeout_seconds=1)


def test_validate_credentials_is_true():
    assert OllamaAdapter().validate_credentials() is True


def test_registry_registers_only_ollama():
    reg = ProviderRegistry()
    assert reg.list_providers() == ["ollama"]
    assert isinstance(reg.get("ollama"), OllamaAdapter)


def test_simple_generate_routes_to_ollama(server):
    server.responder = lambda: (  # type: ignore[attr-defined]
        200,
        json.dumps({"message": {"content": "pong"}, "prompt_eval_count": 1, "eval_count": 1}),
    )
    reg = ProviderRegistry()
    reg.register("ollama", OllamaAdapter(host=f"http://127.0.0.1:{server.server_address[1]}"))
    out = simple_generate("ping", registry=reg, timeout_seconds=5)
    assert out == "pong"
