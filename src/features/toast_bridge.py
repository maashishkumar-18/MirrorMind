"""
ToastBridge (Phase 1 Step 1.5b).

The seam between the Python backend (``ReminderHandler``, the scheduler thread)
and the Windows notification layer. Follows the ``VectorStoreInterface``
pattern — an ABC with concrete implementations injected at the application
composition root.

The **real** implementation is Phase 3 Step 3.1: the Tauri Rust shell registers
a ``ScheduledToastNotification`` with Windows (WinRT via the ``windows`` crate)
and the Python backend talks to it over the IPC bridge — the backend never
touches WinRT directly, and today ``src/common/ipc/`` is schema-only (no
outbound transport). Until then, ``NoOpToastBridge`` keeps the reminder path
working headless and ``InMemoryToastBridge`` is the test double.
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

from src.features.base import new_id

logger = logging.getLogger(__name__)

#: Test seam (Phase 4 Step 4.1) — same spirit as ``RAGPIPE_DB_KEY``. When set to
#: a file path, the backend uses a :class:`FileRecordingToastBridge` that appends
#: every call as one JSON line, so an e2e test can assert toast registration
#: without a real WinRT bridge. The packaged app never sets it.
_FAKE_TOAST_ENV = "RAGPIPE_FAKE_TOAST"


class ToastBridge(ABC):
    """Register / cancel / fire Windows toast notifications for reminders."""

    @abstractmethod
    def register_toast(self, reminder_id: str, scheduled_time: str, body: str) -> str:
        """Schedule a future Windows toast for ``reminder_id`` at
        ``scheduled_time``. Returns an opaque ``toast_id`` the caller stores on
        the reminder row so the toast can later be cancelled."""

    @abstractmethod
    def cancel_toast(self, toast_id: str) -> None:
        """Cancel a previously registered toast. A no-op if it already fired or
        was already cancelled."""

    @abstractmethod
    def fire_toast(self, reminder_id: str, body: str) -> None:
        """Surface a reminder in-app right now — the scheduler's fallback for a
        reminder whose time has arrived while the app is running."""

    @abstractmethod
    def cancel_all(self) -> None:
        """Clear every scheduled toast registration. Used by full-wipe
        (Phase 2 Step 2.2). The real WinRT impl enumerates the platform's
        scheduled notifications — deliberately not driven off the
        ``reminders.toast_id`` column, which has a documented brief NULL
        window (see ``reminder_handler`` module docstring)."""


class NoOpToastBridge(ToastBridge):
    """Default bridge — logs and does nothing. Lets the backend run without a
    frontend / OS integration (dev, tests that don't assert on toasts)."""

    def register_toast(self, reminder_id: str, scheduled_time: str, body: str) -> str:
        toast_id = new_id("toast")
        logger.debug(
            "NoOp register_toast reminder=%s at=%s -> %s", reminder_id, scheduled_time, toast_id
        )
        return toast_id

    def cancel_toast(self, toast_id: str) -> None:
        logger.debug("NoOp cancel_toast %s", toast_id)

    def fire_toast(self, reminder_id: str, body: str) -> None:
        logger.debug("NoOp fire_toast reminder=%s", reminder_id)

    def cancel_all(self) -> None:
        logger.debug("NoOp cancel_all")


class InMemoryToastBridge(ToastBridge):
    """Records every call on ``.calls`` as ``(op, kwargs)``. Satisfies the
    roadmap acceptance criterion "reminder created -> Toast registered
    (verified by mock WinRT call in test)"."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def register_toast(self, reminder_id: str, scheduled_time: str, body: str) -> str:
        self.calls.append(
            (
                "register",
                {"reminder_id": reminder_id, "scheduled_time": scheduled_time, "body": body},
            )
        )
        return f"toast_{len(self.calls)}"

    def cancel_toast(self, toast_id: str) -> None:
        self.calls.append(("cancel", {"toast_id": toast_id}))

    def fire_toast(self, reminder_id: str, body: str) -> None:
        self.calls.append(("fire", {"reminder_id": reminder_id, "body": body}))

    def cancel_all(self) -> None:
        self.calls.append(("cancel_all", {}))


class FileRecordingToastBridge(InMemoryToastBridge):
    """An :class:`InMemoryToastBridge` that also appends every call to a file as
    one JSON object per line (``{"op": ..., "args": {...}}``). Used by the
    Phase 4 e2e harness (``RAGPIPE_FAKE_TOAST=<path>``) — the process that owns
    the bridge and the test process are different, so an in-memory list is not
    observable; a line-delimited file is."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _record(self, op: str, args: dict[str, str]) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"op": op, "args": args}) + "\n")

    def register_toast(self, reminder_id: str, scheduled_time: str, body: str) -> str:
        toast_id = super().register_toast(reminder_id, scheduled_time, body)
        self._record(
            "register",
            {"reminder_id": reminder_id, "scheduled_time": scheduled_time, "body": body},
        )
        return toast_id

    def cancel_toast(self, toast_id: str) -> None:
        super().cancel_toast(toast_id)
        self._record("cancel", {"toast_id": toast_id})

    def fire_toast(self, reminder_id: str, body: str) -> None:
        super().fire_toast(reminder_id, body)
        self._record("fire", {"reminder_id": reminder_id, "body": body})

    def cancel_all(self) -> None:
        super().cancel_all()
        self._record("cancel_all", {})


def resolve_bridge_from_env() -> ToastBridge:
    """The backend composition root's toast bridge. ``NoOpToastBridge`` unless
    the ``RAGPIPE_FAKE_TOAST`` test seam names a recording file. The real WinRT
    bridge (Phase 4 Step 4.6) will slot in here."""
    path = os.getenv(_FAKE_TOAST_ENV)
    if path:
        logger.warning("%s active — recording toast calls to %s", _FAKE_TOAST_ENV, path)
        return FileRecordingToastBridge(path)
    return NoOpToastBridge()
