"""Backend logging configuration (Phase 3 Step 3.4a).

Until now ``src/backend/main.py`` only did ``logging.basicConfig(stream=sys.stderr)``
and the Tauri shell ran the child with ``stderr(Stdio::inherit())`` — so in a
packaged app the logs went nowhere. ``configure_logging()`` keeps that stderr
stream (stdout stays the IPC channel) and adds a rotating file at
``paths.log_file()`` so ``diagnostics.logs`` can tail it and ``diagnostics.report``
can package a redacted copy.

Idempotent: safe to call twice (tests, a re-import) without stacking handlers.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from src.backend.paths import log_file

#: shared by the stderr + file handlers and by ``diagnostics.logs`` parsing.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3
#: marker attribute set on handlers we installed, so a second call is a no-op
_MARKER = "_mirrormind_backend_handler"


def _drop_prior_handlers(root: logging.Logger) -> None:
    for handler in [h for h in root.handlers if getattr(h, _MARKER, False)]:
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


def configure_logging(level: int = logging.INFO) -> None:
    """Install the stderr + rotating-file handlers on the root logger. Safe to
    call repeatedly — prior handlers we installed are removed first, so a test
    that runs ``main()`` under a fresh ``RAGPIPE_DATA_DIR`` re-points the file
    handler instead of stacking or writing to a stale path."""
    root = logging.getLogger()
    root.setLevel(level)
    _drop_prior_handlers(root)

    formatter = logging.Formatter(LOG_FORMAT)

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(formatter)
    setattr(stderr_handler, _MARKER, True)
    root.addHandler(stderr_handler)

    path = log_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(formatter)
        setattr(file_handler, _MARKER, True)
        root.addHandler(file_handler)
    except OSError:
        # A read-only data dir must not stop the backend from starting — stderr
        # logging still works, only the file viewer / report will be empty.
        root.warning(
            "could not open the log file at %s; file logging disabled", path, exc_info=True
        )
