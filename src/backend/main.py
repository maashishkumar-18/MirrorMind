"""Backend entrypoint (Phase 3 Step 3.1a).

``python -m src.backend.main`` — the process the Tauri shell spawns as a
stdio sidecar. Composes the Phase 2.1 startup sequence, wires the
``ShutdownCoordinator``, and serves versioned IPC envelopes over stdin/stdout.

Startup sequence (project_logic.md §12, ``PHASE_2_AUDIT.md`` hand-off 1):

    single-instance guard
      → resolve key  (PreviousDataUnrecoverableError → "starting fresh" screen, exit 3)
      → run migrations (pre-migration snapshot is automatic)
      → open the session DB
      → integrity check  (fail → degraded mode: only status / backup methods)
      → build managers + ShutdownCoordinator + scheduler
      → emit app.ready
      → read loop

Exit codes: ``0`` clean, ``3`` previous data unrecoverable, ``5`` restore
staged (the supervisor swaps the file with the backend down and relaunches).
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType
from typing import BinaryIO

from db.connection import open_session_db
from db.health import check_integrity
from db.migration_runner import MigrationRunner
from observability.tracing import shutdown_tracing
from src.backend import fake_llm, fake_models, fake_retrieval
from src.backend.dispatcher import Dispatcher
from src.backend.keys import resolve_db_key
from src.backend.lifecycle import ShutdownCoordinator
from src.backend.logging_setup import configure_logging
from src.backend.paths import session_db_path, snapshot_dir
from src.backend.reminders_wire import reconciliation_payload
from src.backend.session_worker import SessionWorker
from src.backend.single_instance import SingleInstanceGuard
from src.backend.transport import StdioTransport
from src.backend.wire import HandlerContext, make_error, make_event
from src.common.ipc.envelope import CURRENT_IPC_VERSION
from src.common.types import ReconciliationResult
from src.features.backup_manager import BackupManager
from src.features.scheduler import SchedulerThread
from src.features.toast_bridge import resolve_bridge_from_env
from src.models.app_config import AppConfig
from src.models.model_manager import ModelManager
from src.models.ollama_manager import OllamaManager
from src.security.errors import PreviousDataUnrecoverableError

logger = logging.getLogger("backend")


def _install_signal_handlers(dispatcher: Dispatcher) -> None:
    def _handler(_signum: int, _frame: FrameType | None) -> None:
        logger.info("stop signal received")
        dispatcher.shutdown_requested.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):  # not the main thread (tests) / unsupported
            pass


def _serve(transport: StdioTransport) -> int:
    db_path = session_db_path()

    try:
        key = resolve_db_key(db_path)
    except PreviousDataUnrecoverableError as exc:
        transport.send(make_event("app.previous_data_unrecoverable", {"message": str(exc)}))
        logger.warning("previous data unrecoverable — starting fresh screen")
        return 3

    MigrationRunner(db_path, key=key, snapshot_dir=snapshot_dir()).run()
    conn = open_session_db(db_path, key)
    integrity = check_integrity(conn)
    degraded = not integrity.ok

    active_downloads: set[str] = set()
    _fake = fake_models.build(active_downloads)  # None unless RAGPIPE_FAKE_MODELS is set
    if _fake is not None:
        ollama, models = _fake
    else:
        ollama = OllamaManager(active_downloads=active_downloads)
        models = ModelManager(ollama, active_downloads=active_downloads)
    backups = BackupManager(db_path, key=key)

    coordinator = ShutdownCoordinator()
    coordinator.register("tracing", shutdown_tracing)

    worker: SessionWorker | None = None
    scheduler: SchedulerThread | None = None

    if degraded:
        # keep `conn` for potential degraded-mode diagnostics; nothing writes it
        ctx = HandlerContext(
            ollama=ollama,
            models=models,
            backups=backups,
            app_config_path=None,
            degraded=True,
            conn=conn,
        )
        coordinator.register("session_db", conn.close)
    else:
        # The SessionWorker owns the only long-lived session connection + the
        # keyed SQLiteVectorStore (both sqlite3 thread-affine). Close ours now.
        conn.close()

        def _emit_reminders_pending(recon: ReconciliationResult) -> None:
            # project_logic.md §9: overdue / pending-ack reminders must never be
            # silently dropped. Fired from the worker thread once warm-up
            # completes; transport.send is lock-guarded.
            if recon.overdue or recon.pending_acknowledgment:
                transport.send(make_event("app.reminders_pending", reconciliation_payload(recon)))

        toast_bridge = resolve_bridge_from_env()
        worker = SessionWorker(
            db_path,
            key=key,
            app_config_path=None,
            on_ready=_emit_reminders_pending,
            toast_bridge=toast_bridge,
            **fake_retrieval.session_worker_kwargs(),  # {} unless RAGPIPE_FAKE_RETRIEVAL
        )
        worker.start()
        the_worker = worker
        ctx = HandlerContext(
            ollama=ollama,
            models=models,
            backups=backups,
            app_config_path=None,
            degraded=False,
            worker=worker,
        )

        def _stop_worker() -> None:
            the_worker.stop(timeout=15.0)

        coordinator.register("session_worker", _stop_worker)

        scheduler = SchedulerThread(db_path, toast_bridge, key=key)
        scheduler.start()
        sched = scheduler

        def _stop_scheduler() -> None:
            sched.stop(timeout=5.0)

        coordinator.register("scheduler", _stop_scheduler)

    dispatcher = Dispatcher(transport, ctx, worker=worker)

    _install_signal_handlers(dispatcher)

    if degraded:
        transport.send(make_event("app.integrity_failed", {"details": integrity.details}))
        logger.warning("integrity check failed: %s", integrity.details)

    cfg = AppConfig.load()
    transport.send(
        make_event(
            "app.ready",
            {
                "ipc_version": CURRENT_IPC_VERSION,
                "model_setup_required": cfg.active_model is None,
                "active_model": cfg.active_model,
            },
        )
    )
    logger.info("backend ready (degraded=%s, active_model=%s)", degraded, cfg.active_model)

    try:
        for raw in transport.read_messages():
            dispatcher.handle_raw(raw)
            if dispatcher.shutdown_requested.is_set():
                break
    finally:
        dispatcher.close(wait=True)
        for step in coordinator.shutdown():
            if not step.ok:
                logger.warning("shutdown step %r: %s", step.name, step.error)

    return 5 if dispatcher.restart_snapshot is not None else 0


def main(
    argv: list[str] | None = None,
    *,
    in_stream: BinaryIO | None = None,
    out_stream: BinaryIO | None = None,
) -> int:
    configure_logging()
    # Test seam — must run before any pipeline component is constructed (R1).
    fake_llm.install_if_configured()
    transport = StdioTransport(in_stream, out_stream)
    guard = SingleInstanceGuard()
    if not guard.acquire():
        transport.send(
            make_error("-", "already_running", "another instance of the backend is already running")
        )
        logger.warning("another instance already holds the single-instance mutex; exiting")
        return 0
    try:
        return _serve(transport)
    finally:
        guard.release()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
