"""Backend runtime / composition layer (Production Roadmap Phase 2 Step 2.3).

Phase 2 Step 2.3 ("Crash Recovery and Process Supervision") is mostly Tauri
Rust work that cannot exist until ``src-tauri/`` does — the exponential-backoff
supervisor, the single-instance mutex *enforcement*, the degraded-mode banner,
per-IPC-call timeouts, and the subprocess-kill fuzzing test are all Phase 3.

This package holds the backend's share:

- ``lifecycle``        — ``ShutdownCoordinator``: ordered, guarded, idempotent
                         clean shutdown.
- ``single_instance``  — ``SingleInstanceGuard``: a defense-in-depth Win32
                         named-mutex guard for the frozen ``backend.exe``
                         (canonical enforcement stays in the Rust shell).
- ``main``             — the entrypoint the Tauri shell spawns as a stdio
                         sidecar (Phase 3 Step 3.1a).
- ``transport`` / ``dispatcher`` / ``handlers`` / ``wire`` — the IPC layer:
                         newline-delimited JSON envelopes in, typed handlers,
                         structured error envelopes out.
"""

from src.backend.dispatcher import Dispatcher
from src.backend.lifecycle import ShutdownCoordinator, ShutdownStepResult
from src.backend.session_repository import SessionRepository
from src.backend.session_worker import ChatResult, SessionWorker
from src.backend.single_instance import AlreadyRunningError, SingleInstanceGuard
from src.backend.transport import StdioTransport
from src.backend.wire import HandlerContext, MethodError

# NB: ``src.backend.main`` (the entrypoint) is intentionally *not* re-exported
# here — importing the ``main`` function into this namespace would shadow the
# submodule (``src.backend.main`` would resolve to the function, not the
# module). Import it as ``from src.backend.main import main``.

__all__ = [
    "AlreadyRunningError",
    "ChatResult",
    "Dispatcher",
    "HandlerContext",
    "MethodError",
    "SessionRepository",
    "SessionWorker",
    "ShutdownCoordinator",
    "ShutdownStepResult",
    "SingleInstanceGuard",
    "StdioTransport",
]
