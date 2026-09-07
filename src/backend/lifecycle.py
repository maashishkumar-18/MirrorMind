"""Clean-shutdown coordination for the Python backend (Phase 2 Step 2.3).

When the Tauri shell (Phase 3) tells the backend to stop — or the backend's
own ``main()`` catches SIGTERM/SIGINT — every long-lived resource must be
released in order: the scheduler thread joined, buffered traces flushed, open
database connections closed. ``ShutdownCoordinator`` is that ordered teardown.

It is deliberately dumb: a list of ``(name, close-callable)`` pairs, torn down
in reverse registration order (like ``contextlib.ExitStack``), each step
guarded so one failure does not strand the rest, and idempotent so a double
signal is harmless.

What it is NOT (Phase 3 ``main()``'s job, documented here so it isn't
reinvented):

- Signal-handler registration (``signal.signal(SIGTERM, ...)``).
- A hard per-step timeout. Each ``close`` callable is expected to be
  self-bounded — ``SchedulerThread.stop()`` already takes a ``timeout`` and
  ``observability.tracing.shutdown_tracing()`` wraps Langfuse's own bounded
  ``shutdown()``. If a future resource can hang unbounded, wrap it before
  registering, don't add timeout machinery here.

Canonical registrations Phase 3 ``main()`` will make, in order::

    coordinator.register("tracing", observability.tracing.shutdown_tracing)
    coordinator.register("session_db", session_conn.close)
    coordinator.register("scheduler", lambda: scheduler.stop(timeout=5.0))

(scheduler registered last -> stopped first, before its connection closes.)
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShutdownStepResult:
    """Outcome of tearing down one registered resource."""

    name: str
    ok: bool
    elapsed_ms: float
    error: str | None = None


class ShutdownCoordinator:
    def __init__(self) -> None:
        self._steps: list[tuple[str, Callable[[], None]]] = []
        self._done = False
        # register() and shutdown() can race — a stop signal on one thread
        # while main() is still wiring on another.
        self._lock = threading.Lock()

    def register(self, name: str, close: Callable[[], None]) -> None:
        """Register a teardown callable. Teardown runs in reverse registration
        order, so register a resource *after* anything that depends on it."""
        with self._lock:
            if self._done:
                raise RuntimeError("ShutdownCoordinator has already shut down")
            self._steps.append((name, close))

    def shutdown(self) -> list[ShutdownStepResult]:
        """Tear down every registered resource, newest first. Each step is
        guarded — a raising ``close`` is logged and the rest still run.
        Idempotent: a second call is a no-op returning ``[]``."""
        with self._lock:
            if self._done:
                return []
            self._done = True
            steps = list(self._steps)

        results: list[ShutdownStepResult] = []
        for name, close in reversed(steps):
            start = time.monotonic()
            try:
                close()
                elapsed = (time.monotonic() - start) * 1000
                results.append(ShutdownStepResult(name, True, round(elapsed, 2)))
            except Exception as exc:  # noqa: BLE001 — one bad step must not strand the rest
                elapsed = (time.monotonic() - start) * 1000
                logger.exception("shutdown step %r failed", name)
                results.append(
                    ShutdownStepResult(
                        name, False, round(elapsed, 2), f"{type(exc).__name__}: {exc}"
                    )
                )
        return results
