"""Shared wire helpers for the IPC layer (Phase 3 Step 3.1a).

Envelope builders, the handler-context struct, and the typed error a handler
raises to produce an ``error`` envelope instead of a ``response``. Kept in its
own module so ``dispatcher`` and ``handlers`` can both import it without a
cycle.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from src.common.ipc.envelope import CURRENT_IPC_VERSION
from src.features.backup_manager import BackupManager
from src.features.base import now_iso
from src.models.model_manager import ModelManager
from src.models.ollama_manager import OllamaManager

EventEmitter = Callable[[str, dict, str], None]  # (method, params, request_id)


class MethodError(Exception):
    """Raised by a handler to return a structured ``error`` envelope. ``code``
    is a stable slug; ``message`` is user-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class HandlerContext:
    """Everything a handler is allowed to touch. One instance per backend
    process, shared across worker threads (each primitive is either read-only
    here or does its own locking / short-lived connections)."""

    conn: sqlite3.Connection
    ollama: OllamaManager
    models: ModelManager
    backups: BackupManager
    app_config_path: str | None
    degraded: bool = False
    #: wired by the Dispatcher after construction (always set in real use;
    #: tests that exercise a streaming / lifecycle handler pass their own).
    emit_event: EventEmitter | None = None
    request_shutdown: Callable[[], None] | None = None
    request_restart: Callable[[str], None] | None = None


def _base(message_type: str, request_id: str, payload: dict) -> dict:
    return {
        "version": CURRENT_IPC_VERSION,
        "message_type": message_type,
        "request_id": request_id,
        "timestamp": now_iso(),
        "payload": payload,
    }


def make_response(request_id: str, method: str, result: dict) -> dict:
    return _base("response", request_id, {"method": method, "result": result})


def make_error(request_id: str, code: str, message: str, method: str | None = None) -> dict:
    payload: dict = {"code": code, "message": message}
    if method is not None:
        payload["method"] = method
    return _base("error", request_id, payload)


def make_event(method: str, params: dict, request_id: str = "-") -> dict:
    return _base("event", request_id, {"method": method, "params": params})


def new_request_id() -> str:
    return f"be-{uuid.uuid4().hex[:12]}"
