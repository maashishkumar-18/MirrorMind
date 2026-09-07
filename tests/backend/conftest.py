"""Shared fakes + fixtures for the IPC-layer tests (Phase 3 Step 3.1a)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from db.connection import open_session_db
from db.migration_runner import MigrationRunner
from src.backend.dispatcher import Dispatcher
from src.backend.transport import StdioTransport
from src.backend.wire import HandlerContext
from src.models.types import InstalledModel, ModelStatus


class FakeOllama:
    host = "http://fake:11434"

    def __init__(self, *, running: bool = True, installed: dict[str, int] | None = None):
        self._running = running
        self._installed = dict(installed or {})

    def is_running(self) -> bool:
        return self._running

    def get_installed_models(self) -> list[InstalledModel]:
        return [InstalledModel(name=n, size_bytes=s) for n, s in self._installed.items()]

    def get_model_status(self, name: str, *, active_model: str | None = None) -> ModelStatus:
        if name not in self._installed:
            return ModelStatus.NOT_INSTALLED
        if active_model == name:
            return ModelStatus.ACTIVE
        return ModelStatus.AVAILABLE

    def show_model(self, name: str) -> dict | None:
        return {"details": {}} if name in self._installed else None


class FakeModelManager:
    def __init__(self, *, catalog=None, installed=None, download_error: str | None = None):
        from src.models.types import ModelCatalogEntry

        self._catalog = catalog or [
            ModelCatalogEntry("llama3.1:8b", "Llama 3.1 8B", 4_700_000_000, "desc", 8.0, True),
            ModelCatalogEntry("qwen2.5:7b", "Qwen 2.5 7B", 4_400_000_000, "desc", 8.0, False),
        ]
        self._installed = set(installed or ())
        self._download_error = download_error
        self.activated: str | None = None

    def get_model_catalog(self):
        return self._catalog

    def verify_model_integrity(self, name: str) -> bool:
        return name in self._installed

    def switch_active_model(self, name: str) -> None:
        from src.models.types import ModelDownloadError

        if name not in self._installed:
            raise ModelDownloadError(f"Can't switch to '{name}' — it isn't installed.")
        self.activated = name

    def download_model(self, name: str, progress_callback):
        from src.models.types import DownloadProgress, DownloadResult, ModelDownloadError

        progress_callback(DownloadProgress(phase="manifest", percent=0.0))
        progress_callback(DownloadProgress(phase="downloading", percent=50.0, speed_mbps=10.0))
        if self._download_error:
            raise ModelDownloadError(self._download_error)
        progress_callback(DownloadProgress(phase="complete", percent=100.0))
        self._installed.add(name)
        return DownloadResult(model_name=name, status="complete", verified=True)


def _default_chat_result(text: str):
    from src.backend.session_worker import ChatResult

    return ChatResult(
        session_id="session_0000",
        turn_index=0,
        answer=f"echo: {text}",
        confidence=0.9,
        tier=1,
        action_type="conversation",
        retrieve_needed=False,
        retrieval_route=None,
        is_grounded=False,
        grounding_confidence=0.0,
    )


class FakeSessionWorker:
    """Stand-in for SessionWorker — synchronous, no thread, no model loads.
    ``submit(fn)`` runs ``fn`` inline so the dispatcher's worker path is
    exercised without spinning a real thread."""

    def __init__(
        self,
        *,
        health_ok: bool = True,
        send_result=None,
        no_model: bool = False,
        on_ready=None,
        reconciliation=None,
    ):
        from db.health import IntegrityResult
        from src.common.types import ReconciliationResult

        self._health = IntegrityResult(ok=health_ok, details=["ok"] if health_ok else ["bad"])
        self._send_result = send_result
        self._no_model = no_model
        self._on_ready = on_ready
        self._reconciliation = reconciliation or ReconciliationResult()
        self.sessions: list[str] = []
        self.sent: list[str] = []
        self.started = False
        self.stopped = False

    # lifecycle (used when patched into main)
    def start(self) -> None:
        self.started = True
        if self._on_ready is not None:
            self._on_ready(self._reconciliation)

    def reconciliation(self):
        return self._reconciliation

    def wait_ready(self, timeout=None) -> bool:
        return True

    def stop(self, timeout: float = 15.0) -> bool:
        self.stopped = True
        return True

    def submit(self, fn):
        from concurrent.futures import Future

        fut: Future = Future()
        try:
            fut.set_result(fn())
        except BaseException as exc:  # noqa: BLE001
            fut.set_exception(exc)
        return fut

    def health(self):
        return self._health

    def new_conversation(self) -> str:
        sid = f"session_{len(self.sessions):04d}"
        self.sessions.append(sid)
        return sid

    def history(self, session_id):
        return session_id or "session_0000", []

    def send(self, text: str):
        from src.backend.wire import MethodError

        self.sent.append(text)
        if self._no_model:
            raise MethodError("no_model_active", "no model")
        return (
            self._send_result(text)
            if callable(self._send_result)
            else (self._send_result or _default_chat_result(text))
        )


@pytest.fixture
def keyed_db(tmp_path: Path) -> str:
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "snap").run()
    return path


@pytest.fixture
def ctx_factory(keyed_db, tmp_path):
    """Build a HandlerContext over a real migrated (plaintext) DB + fakes."""
    from src.features.backup_manager import BackupConfig, BackupManager

    conns: list = []

    def _make(
        *, degraded: bool = False, ollama=None, models=None, app_config_path=None, worker=None
    ):
        conn = open_session_db(keyed_db)
        conns.append(conn)
        backups = BackupManager(
            keyed_db, backup_dir=tmp_path / "backups", config=BackupConfig(retention=3)
        )
        return HandlerContext(
            conn=conn,
            ollama=ollama or FakeOllama(),
            models=models or FakeModelManager(),
            backups=backups,
            app_config_path=app_config_path,
            degraded=degraded,
            worker=worker,
        )

    yield _make
    for c in conns:
        c.close()


class CollectingTransport(StdioTransport):
    """A transport that records every sent envelope instead of writing bytes."""

    def __init__(self, lines_in: list[dict] | None = None):
        payload = b"".join((json.dumps(m) + "\n").encode() for m in (lines_in or []))
        super().__init__(io.BytesIO(payload), io.BytesIO())
        self.sent: list[dict] = []

    def send(self, envelope: dict) -> None:  # type: ignore[override]
        self.sent.append(envelope)

    def by_type(self, message_type: str) -> list[dict]:
        return [e for e in self.sent if e["message_type"] == message_type]


@pytest.fixture
def dispatcher_factory(ctx_factory):
    made: list[Dispatcher] = []

    def _make(**ctx_kwargs) -> tuple[Dispatcher, CollectingTransport, HandlerContext]:
        transport = CollectingTransport()
        ctx = ctx_factory(**ctx_kwargs)
        d = Dispatcher(transport, ctx, max_workers=2, worker=ctx_kwargs.get("worker"))
        made.append(d)
        return d, transport, ctx

    yield _make
    for d in made:
        d.close(wait=True)


def envelope(method: str, request_id: str = "r1", params: dict | None = None, version: int = 1):
    return {
        "version": version,
        "message_type": "request",
        "request_id": request_id,
        "timestamp": "2026-09-08T00:00:00Z",
        "payload": {"method": method, "params": params or {}},
    }
