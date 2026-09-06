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

import logging
from abc import ABC, abstractmethod

from src.features.base import new_id

logger = logging.getLogger(__name__)


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
