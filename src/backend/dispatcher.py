"""Request dispatcher (Phase 3 Step 3.1a).

Owns the read-loop-facing half of the IPC layer: version check, method lookup,
``params`` validation, worker-thread execution, and turning every failure mode
into an ``error`` envelope so the loop in ``main`` never has to.

Threading: each request is run on a small pool so a slow call (a model
download, later a chat generation) does not block reading the next message.
``app.shutdown`` is handled inline — its ack must precede the drain.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, ValidationError

from src.backend.handlers import HANDLERS
from src.backend.transport import StdioTransport
from src.backend.wire import HandlerContext, MethodError, make_error, make_event, make_response
from src.common.ipc.envelope import CURRENT_IPC_VERSION, IPCMessageType
from src.common.ipc.methods import METHOD_CONTRACTS
from src.common.ipc.middleware import IPCVersionMismatchError, check_version

logger = logging.getLogger(__name__)


def _summarize(exc: ValidationError) -> str:
    errs = exc.errors()
    if not errs:
        return "invalid payload"
    first = errs[0]
    loc = ".".join(str(p) for p in first.get("loc", ()))
    return f"{loc}: {first.get('msg', 'invalid')}" if loc else first.get("msg", "invalid payload")


class Dispatcher:
    def __init__(
        self,
        transport: StdioTransport,
        ctx: HandlerContext,
        *,
        expected_version: int = CURRENT_IPC_VERSION,
        max_workers: int = 4,
    ) -> None:
        self._transport = transport
        self._ctx = ctx
        self._expected_version = expected_version
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ipc")
        self.shutdown_requested = threading.Event()
        #: set by a successful ``backup.restore`` — main exits 5, the supervisor swaps + relaunches
        self.restart_snapshot: str | None = None

        self._ctx.emit_event = self._emit_event
        self._ctx.request_shutdown = self.shutdown_requested.set
        self._ctx.request_restart = self._request_restart

    # -- context callbacks -------------------------------------------------

    def _emit_event(self, method: str, params: dict, request_id: str = "-") -> None:
        self._transport.send(make_event(method, params, request_id))

    def _request_restart(self, snapshot_path: str) -> None:
        self.restart_snapshot = snapshot_path
        self.shutdown_requested.set()

    # -- dispatch --------------------------------------------------------

    def handle_raw(self, raw: dict | None) -> None:
        """Process one inbound line. Never raises."""
        if raw is None:
            self._transport.send(
                make_error("-", "validation_error", "input line was not a JSON object")
            )
            return

        rid_val = raw.get("request_id")
        request_id = rid_val if isinstance(rid_val, str) else "-"
        try:
            envelope = check_version(raw, self._expected_version)
        except IPCVersionMismatchError as exc:
            self._transport.send(make_error(request_id, "version_mismatch", str(exc)))
            return
        except ValidationError as exc:
            self._transport.send(make_error(request_id, "validation_error", _summarize(exc)))
            return

        request_id = envelope.request_id
        if envelope.message_type != IPCMessageType.REQUEST:
            self._transport.send(
                make_error(
                    request_id,
                    "validation_error",
                    f"expected a request envelope, got {envelope.message_type.value}",
                )
            )
            return

        payload = envelope.payload or {}
        method = payload.get("method")
        contract = METHOD_CONTRACTS.get(method) if isinstance(method, str) else None
        if contract is None or not isinstance(method, str):
            self._transport.send(
                make_error(request_id, "unknown_method", f"no such method: {method!r}")
            )
            return

        if self._ctx.degraded and not contract.degraded_ok:
            self._transport.send(
                make_error(
                    request_id,
                    "integrity_failed",
                    "the backend is in recovery mode — restore a backup to continue",
                    method,
                )
            )
            return

        try:
            params = contract.params.model_validate(payload.get("params") or {})
        except ValidationError as exc:
            self._transport.send(
                make_error(request_id, "validation_error", _summarize(exc), method)
            )
            return

        if method == "app.shutdown":
            self._run(method, params, request_id)  # inline: ack before the drain
            return
        self._executor.submit(self._run, method, params, request_id)

    def _run(self, method: str, params: BaseModel, request_id: str) -> None:
        handler = HANDLERS[method]
        try:
            result = handler(params, self._ctx, request_id)
        except MethodError as exc:
            self._transport.send(make_error(request_id, exc.code, exc.message, method))
            return
        except Exception:
            logger.exception("handler %r crashed", method)
            self._transport.send(
                make_error(
                    request_id,
                    "internal_error",
                    "the backend hit an unexpected error handling that request",
                    method,
                )
            )
            return
        self._transport.send(make_response(request_id, method, result.model_dump()))

    def close(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
