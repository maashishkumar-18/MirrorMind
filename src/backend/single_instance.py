"""Single-instance guard for the frozen backend executable (Phase 2 Step 2.3).

**Defense in depth only.** The Tauri Rust shell owns canonical single-instance
enforcement and first-window focus via the same named mutex plus a WM_COPYDATA
/ named-event signal (project_logic.md §7). This guard exists so a stray second
``backend.exe`` — a packaging bug, a manual double-launch — fails fast instead
of two backends racing the same IPC socket once Phase 3 wires one up. It never
touches the database (WAL + single-writer already makes a second opener
harmless); it just refuses to run.

Windows only. On any other platform ``acquire()`` returns ``True`` and logs
once — the shipped build is Windows, but this keeps the ubuntu ``eval`` CI job
and dev-on-mac import paths working.
"""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

DEFAULT_MUTEX_NAME = "Global\\PersonalAICompanion_v1_SingleInstance"
_ERROR_ALREADY_EXISTS = 183


class AlreadyRunningError(RuntimeError):
    """Raised by ``SingleInstanceGuard.__enter__`` when another instance holds
    the mutex. Catch it in ``main()`` and exit quietly."""


class SingleInstanceGuard:
    def __init__(self, name: str = DEFAULT_MUTEX_NAME):
        self._name = name
        self._handle: int | None = None
        self._is_owner = False

    def acquire(self) -> bool:
        """``True`` if this process is the only instance. ``False`` if another
        already holds the mutex. Safe to call more than once."""
        if self._handle is not None:
            return self._is_owner

        if sys.platform != "win32":
            logger.info(
                "SingleInstanceGuard is a no-op on %s; the frozen build is Windows-only.",
                sys.platform,
            )
            self._is_owner = True
            return True

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.CreateMutexW(None, False, self._name)
        last_error = kernel32.GetLastError()
        self._handle = handle
        self._is_owner = handle != 0 and last_error != _ERROR_ALREADY_EXISTS
        if not self._is_owner:
            logger.warning("another instance already holds %s", self._name)
        return self._is_owner

    def release(self) -> None:
        """Close the mutex handle. Idempotent."""
        if self._handle and sys.platform == "win32":
            ctypes.windll.kernel32.CloseHandle(self._handle)  # type: ignore[attr-defined]
        self._handle = None
        self._is_owner = False

    def __enter__(self) -> SingleInstanceGuard:
        if not self.acquire():
            raise AlreadyRunningError(f"another instance already holds {self._name}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
