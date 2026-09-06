"""Backend runtime / composition layer (Production Roadmap Phase 2 Step 2.3).

Phase 2 Step 2.3 ("Crash Recovery and Process Supervision") is mostly Tauri
Rust work that cannot exist until ``src-tauri/`` does — the exponential-backoff
supervisor, the single-instance mutex *enforcement*, the degraded-mode banner,
per-IPC-call timeouts, and the subprocess-kill fuzzing test are all Phase 3.

This package holds the backend's share:

- ``lifecycle``        — ``ShutdownCoordinator``: ordered, guarded, idempotent
                         clean shutdown. The seam the Phase 3 backend ``main()``
                         and the Tauri stop signal call.
- ``single_instance``  — ``SingleInstanceGuard``: a defense-in-depth Win32
                         named-mutex guard for the frozen ``backend.exe``
                         (canonical enforcement stays in the Rust shell).

Phase 3's ``main()`` and IPC dispatcher will join this package.
"""

from src.backend.lifecycle import ShutdownCoordinator, ShutdownStepResult
from src.backend.single_instance import AlreadyRunningError, SingleInstanceGuard

__all__ = [
    "AlreadyRunningError",
    "ShutdownCoordinator",
    "ShutdownStepResult",
    "SingleInstanceGuard",
]
