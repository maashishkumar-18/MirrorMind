"""IPC method handlers (Phase 3 Step 3.1a).

Thin adapters: each function takes a validated ``*Params`` model + the shared
``HandlerContext`` + the originating ``request_id`` and returns a ``*Result``
model. No business logic lives here — every handler delegates straight to a
Phase 1.6 / 2.1 / 2.2 primitive. Streaming (``model.download``) emits
``event`` envelopes via ``ctx.emit_event`` and returns the terminal result;
a failure raises :class:`~src.backend.wire.MethodError`.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from db.health import check_quick
from src.backend.wire import HandlerContext, MethodError
from src.common.ipc.envelope import CURRENT_IPC_VERSION
from src.common.ipc.methods import (
    AppShutdownResult,
    AppStatusResult,
    BackupEntry,
    BackupListResult,
    BackupRestoreResult,
    CatalogEntry,
    HealthCheckResult,
    InstalledEntry,
    ModelActivateParams,
    ModelActivateResult,
    ModelCatalogResult,
    ModelDownloadParams,
    ModelDownloadResult,
    ModelStatusParams,
    ModelStatusResult,
)
from src.models.app_config import AppConfig
from src.models.types import DownloadProgress, ModelDownloadError

Handler = Callable[[BaseModel, HandlerContext, str], BaseModel]


def _app_status(_p: BaseModel, ctx: HandlerContext, _rid: str) -> AppStatusResult:
    cfg = AppConfig.load(ctx.app_config_path)
    return AppStatusResult(
        ipc_version=CURRENT_IPC_VERSION,
        model_setup_required=cfg.active_model is None,
        active_model=cfg.active_model,
        last_exported_at=cfg.last_exported_at,
        degraded=ctx.degraded,
        ready=not ctx.degraded,
    )


def _health_check(_p: BaseModel, ctx: HandlerContext, _rid: str) -> HealthCheckResult:
    res = check_quick(ctx.conn)
    return HealthCheckResult(ok=res.ok, details=res.details)


def _model_catalog(_p: BaseModel, ctx: HandlerContext, _rid: str) -> ModelCatalogResult:
    return ModelCatalogResult(
        models=[
            CatalogEntry(
                name=e.name,
                display_name=e.display_name,
                size_bytes=e.size_bytes,
                description=e.description,
                min_ram_gb=e.min_ram_gb,
                recommended=e.recommended,
            )
            for e in ctx.models.get_model_catalog()
        ]
    )


def _model_status(p: BaseModel, ctx: HandlerContext, _rid: str) -> ModelStatusResult:
    assert isinstance(p, ModelStatusParams)
    active = AppConfig.load(ctx.app_config_path).active_model
    installed = ctx.ollama.get_installed_models()
    running = ctx.ollama.is_running()

    names = p.names
    if names is None:
        names = sorted(
            {e.name for e in ctx.models.get_model_catalog()} | {m.name for m in installed}
        )
    statuses = {n: ctx.ollama.get_model_status(n, active_model=active).value for n in names}
    return ModelStatusResult(
        ollama_running=running,
        installed=[InstalledEntry(name=m.name, size_bytes=m.size_bytes) for m in installed],
        statuses=statuses,
    )


def _model_download(p: BaseModel, ctx: HandlerContext, rid: str) -> ModelDownloadResult:
    assert isinstance(p, ModelDownloadParams)
    assert ctx.emit_event is not None
    emit = ctx.emit_event

    def on_progress(dp: DownloadProgress) -> None:
        emit(
            "model.download.progress",
            {
                "name": p.name,
                "phase": dp.phase,
                "percent": dp.percent,
                "speed_mbps": dp.speed_mbps,
                "eta_seconds": dp.eta_seconds,
                "message": dp.message,
            },
            rid,
        )

    try:
        result = ctx.models.download_model(p.name, on_progress)
    except ModelDownloadError as exc:
        raise MethodError("model_download_failed", str(exc)) from exc

    verified = ctx.models.verify_model_integrity(p.name)
    return ModelDownloadResult(
        model_name=result.model_name,
        status=result.status,
        restarts=result.restarts,
        resumes=result.resumes,
        verified=verified,
    )


def _model_activate(p: BaseModel, ctx: HandlerContext, _rid: str) -> ModelActivateResult:
    assert isinstance(p, ModelActivateParams)
    try:
        ctx.models.switch_active_model(p.name)
    except ModelDownloadError as exc:
        raise MethodError("model_not_installed", str(exc)) from exc
    active = AppConfig.load(ctx.app_config_path).active_model or p.name
    return ModelActivateResult(active_model=active, verified=True)


def _backup_list(_p: BaseModel, ctx: HandlerContext, _rid: str) -> BackupListResult:
    return BackupListResult(
        backups=[
            BackupEntry(path=str(s.path), created_at=s.created_at, size_bytes=s.size_bytes)
            for s in ctx.backups.list_backups()
        ]
    )


def _backup_restore(p: BaseModel, ctx: HandlerContext, _rid: str) -> BackupRestoreResult:
    from src.common.ipc.methods import BackupRestoreParams

    assert isinstance(p, BackupRestoreParams)
    res = ctx.backups.stage_restore(p.path)
    if not res.ok:
        raise MethodError("restore_invalid", res.detail)
    assert res.validated_snapshot_path is not None
    assert ctx.emit_event is not None and ctx.request_restart is not None
    ctx.emit_event(
        "app.restore_staged", {"validated_snapshot_path": res.validated_snapshot_path}, _rid
    )
    ctx.request_restart(res.validated_snapshot_path)
    return BackupRestoreResult(
        ok=True,
        needs_restart=True,
        detail=res.detail,
        validated_snapshot_path=res.validated_snapshot_path,
    )


def _app_shutdown(_p: BaseModel, ctx: HandlerContext, _rid: str) -> AppShutdownResult:
    assert ctx.request_shutdown is not None
    ctx.request_shutdown()
    return AppShutdownResult(stopping=True)


HANDLERS: dict[str, Handler] = {
    "app.status": _app_status,
    "health.check": _health_check,
    "model.catalog": _model_catalog,
    "model.status": _model_status,
    "model.download": _model_download,
    "model.activate": _model_activate,
    "backup.list": _backup_list,
    "backup.restore": _backup_restore,
    "app.shutdown": _app_shutdown,
}
