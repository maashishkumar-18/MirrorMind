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
from typing import TypeVar

from pydantic import BaseModel

import src.backend.diagnostics as diagnostics
import src.backend.settings as backend_settings
from observability.metrics_store import MetricsStore
from src.backend.feature_wire import (
    meeting_note_wire,
    schedule_conflict_wire,
    schedule_item_wire,
    todo_wire,
)
from src.backend.log_redaction import write_redacted_report
from src.backend.reminders_wire import reminder_wire
from src.backend.wire import HandlerContext, MethodError
from src.common.ipc.envelope import CURRENT_IPC_VERSION
from src.common.ipc.methods import (
    AppShutdownResult,
    AppStatusResult,
    BackupEntry,
    BackupListResult,
    BackupRestoreResult,
    CatalogEntry,
    ChatCitation,
    ChatConfirmActionParams,
    ChatConflict,
    ChatDisambiguation,
    ChatFeature,
    ChatHistoryParams,
    ChatHistoryResult,
    ChatMessage,
    ChatNewResult,
    ChatSendParams,
    ChatSendResult,
    ConfidenceDistribution,
    DataExportParams,
    DataExportResult,
    DataInfoResult,
    DataWipeParams,
    DataWipeResult,
    DiagnosticsLogsParams,
    DiagnosticsLogsResult,
    DiagnosticsMetricsParams,
    DiagnosticsMetricsResult,
    DiagnosticsReportParams,
    DiagnosticsReportResult,
    FeatureDeletedResult,
    FeatureIdParams,
    HealthCheckResult,
    InstalledEntry,
    LatencyPercentiles,
    LogEntry,
    MeetingGetResult,
    MeetingNoteWire,
    MeetingResult,
    MeetingsCaptureParams,
    MeetingsListResult,
    ModelActivateParams,
    ModelActivateResult,
    ModelCatalogResult,
    ModelDownloadParams,
    ModelDownloadResult,
    ModelStatusParams,
    ModelStatusResult,
    ReminderRescheduleParams,
    ReminderResult,
    RemindersListResult,
    RemindersReconciliationResult,
    ReminderUpdateParams,
    ReminderWire,
    ScheduleConflictWire,
    ScheduleCreateItemParams,
    ScheduleDayGroup,
    ScheduleDayParams,
    ScheduleDayResult,
    ScheduleItemResult,
    ScheduleItemWire,
    ScheduleUpdateParams,
    ScheduleWeekParams,
    ScheduleWeekResult,
    SettingsResult,
    SettingsUpdateParams,
    TodoResult,
    TodosListResult,
    TodoUpdateParams,
    TodoWire,
)
from src.common.types import (
    Reminder,
    ScheduleItem,
    Todo,
)
from src.common.types import (
    ScheduleConflict as ScheduleConflictEntity,
)
from src.features.data_admin import (
    EXPORT_BLURB,
    NEVER_EXPORTED_LINE,
    UNINSTALL_WARNING,
    export_badge_state,
)
from src.models.app_config import AppConfig
from src.models.ollama_manager import normalize_model_name
from src.models.types import DownloadProgress, ModelDownloadError


def _require_worker(ctx: HandlerContext):
    if ctx.worker is None:
        raise MethodError("unavailable", "chat features are not available right now")
    return ctx.worker


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
    # Routed to the SessionWorker thread (contract worker=True) — the session
    # connection is thread-affine and lives on that thread.
    res = _require_worker(ctx).health()
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
    installed_norm = {normalize_model_name(m.name) for m in installed}
    statuses = {
        n: s.value
        for n, s in ctx.ollama.get_model_statuses(
            names, active_model=active, installed=installed_norm
        ).items()
    }
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


def _chat_result(r) -> ChatSendResult:
    return ChatSendResult(
        session_id=r.session_id,
        turn_index=r.turn_index,
        answer=r.answer,
        confidence=r.confidence,
        tier=r.tier,
        action_type=r.action_type,
        retrieve_needed=r.retrieve_needed,
        retrieval_route=r.retrieval_route,
        is_grounded=r.is_grounded,
        grounding_confidence=r.grounding_confidence,
        citations=[ChatCitation(**c) for c in r.citations],
        warnings=r.warnings,
        feature=ChatFeature(**r.feature) if r.feature else None,
        disambiguation=ChatDisambiguation(**r.disambiguation) if r.disambiguation else None,
        conflict=ChatConflict.model_validate(r.conflict) if r.conflict else None,
        dismissed_pending=r.dismissed_pending,
    )


def _chat_send(p: BaseModel, ctx: HandlerContext, _rid: str) -> ChatSendResult:
    assert isinstance(p, ChatSendParams)
    return _chat_result(_require_worker(ctx).send(p.text))  # runs on the worker thread


def _chat_confirm_action(p: BaseModel, ctx: HandlerContext, _rid: str) -> ChatSendResult:
    assert isinstance(p, ChatConfirmActionParams)
    return _chat_result(_require_worker(ctx).confirm_action(p.pending_action_id, p.choice))


def _chat_new(_p: BaseModel, ctx: HandlerContext, _rid: str) -> ChatNewResult:
    return ChatNewResult(session_id=_require_worker(ctx).new_conversation())


def _chat_history(p: BaseModel, ctx: HandlerContext, _rid: str) -> ChatHistoryResult:
    assert isinstance(p, ChatHistoryParams)
    session_id, rows = _require_worker(ctx).history(p.session_id)
    return ChatHistoryResult(
        session_id=session_id,
        messages=[ChatMessage(**row) for row in rows],  # type: ignore[arg-type]
    )


def _reminders_reconciliation(
    _p: BaseModel, ctx: HandlerContext, _rid: str
) -> RemindersReconciliationResult:
    recon = _require_worker(ctx).reconciliation()
    return RemindersReconciliationResult(
        overdue=[ReminderWire.model_validate(reminder_wire(r)) for r in recon.overdue],
        pending_acknowledgment=[
            ReminderWire.model_validate(reminder_wire(r)) for r in recon.pending_acknowledgment
        ],
    )


# -- feature views (Step 3.3) — all worker=True -----------------------------


_T = TypeVar("_T")


def _feature_call(fn: Callable[[], _T]) -> _T:
    """Run a feature-handler call, mapping its two documented failure modes to
    stable error codes (a `MethodError` from the worker passes straight through)."""
    try:
        return fn()
    except MethodError:
        raise
    except ValueError as exc:
        raise MethodError("invalid_params", str(exc)) from exc
    except KeyError as exc:
        raise MethodError("not_found", str(exc)) from exc


def _drop_none(**fields: object) -> dict[str, object]:
    return {k: v for k, v in fields.items() if v is not None}


def _reminder_result(r: Reminder) -> ReminderResult:
    return ReminderResult(reminder=ReminderWire.model_validate(reminder_wire(r)))


def _reminders_list(_p: BaseModel, ctx: HandlerContext, _rid: str) -> RemindersListResult:
    rows = _require_worker(ctx).list_reminders()
    return RemindersListResult(
        reminders=[ReminderWire.model_validate(reminder_wire(r)) for r in rows]
    )


def _reminders_complete(p: BaseModel, ctx: HandlerContext, _rid: str) -> ReminderResult:
    assert isinstance(p, FeatureIdParams)
    w = _require_worker(ctx)
    return _reminder_result(_feature_call(lambda: w.complete_reminder(p.id)))


def _reminders_dismiss(p: BaseModel, ctx: HandlerContext, _rid: str) -> ReminderResult:
    assert isinstance(p, FeatureIdParams)
    w = _require_worker(ctx)
    return _reminder_result(_feature_call(lambda: w.dismiss_reminder(p.id)))


def _reminders_reschedule(p: BaseModel, ctx: HandlerContext, _rid: str) -> ReminderResult:
    assert isinstance(p, ReminderRescheduleParams)
    w = _require_worker(ctx)
    return _reminder_result(_feature_call(lambda: w.reschedule_reminder(p.id, p.scheduled_time)))


def _reminders_update(p: BaseModel, ctx: HandlerContext, _rid: str) -> ReminderResult:
    assert isinstance(p, ReminderUpdateParams)
    w = _require_worker(ctx)
    fields = _drop_none(title=p.title, notes=p.notes, scheduled_time=p.scheduled_time)
    return _reminder_result(_feature_call(lambda: w.update_reminder(p.id, **fields)))


def _reminders_delete(p: BaseModel, ctx: HandlerContext, _rid: str) -> FeatureDeletedResult:
    assert isinstance(p, FeatureIdParams)
    w = _require_worker(ctx)
    return FeatureDeletedResult(deleted=bool(_feature_call(lambda: w.delete_reminder(p.id))))


def _todo_result(t: Todo) -> TodoResult:
    return TodoResult(todo=TodoWire.model_validate(todo_wire(t)))


def _todos_list(_p: BaseModel, ctx: HandlerContext, _rid: str) -> TodosListResult:
    rows = _require_worker(ctx).list_todos()
    return TodosListResult(todos=[TodoWire.model_validate(todo_wire(t)) for t in rows])


def _todos_complete(p: BaseModel, ctx: HandlerContext, _rid: str) -> TodoResult:
    assert isinstance(p, FeatureIdParams)
    w = _require_worker(ctx)
    return _todo_result(_feature_call(lambda: w.complete_todo(p.id)))


def _todos_update(p: BaseModel, ctx: HandlerContext, _rid: str) -> TodoResult:
    assert isinstance(p, TodoUpdateParams)
    w = _require_worker(ctx)
    fields = _drop_none(title=p.title, notes=p.notes, priority=p.priority, category=p.category)
    return _todo_result(_feature_call(lambda: w.update_todo(p.id, **fields)))


def _todos_delete(p: BaseModel, ctx: HandlerContext, _rid: str) -> FeatureDeletedResult:
    assert isinstance(p, FeatureIdParams)
    w = _require_worker(ctx)
    return FeatureDeletedResult(deleted=bool(_feature_call(lambda: w.delete_todo(p.id))))


def _meetings_list(_p: BaseModel, ctx: HandlerContext, _rid: str) -> MeetingsListResult:
    rows = _require_worker(ctx).list_meeting_notes()
    return MeetingsListResult(
        meetings=[MeetingNoteWire.model_validate(meeting_note_wire(n)) for n in rows]
    )


def _meetings_get(p: BaseModel, ctx: HandlerContext, _rid: str) -> MeetingGetResult:
    assert isinstance(p, FeatureIdParams)
    note = _require_worker(ctx).get_meeting_note(p.id)
    return MeetingGetResult(
        meeting=MeetingNoteWire.model_validate(meeting_note_wire(note)) if note else None
    )


def _meetings_capture(p: BaseModel, ctx: HandlerContext, _rid: str) -> MeetingResult:
    assert isinstance(p, MeetingsCaptureParams)
    w = _require_worker(ctx)
    note = _feature_call(lambda: w.capture_meeting_note(p.transcript))
    return MeetingResult(meeting=MeetingNoteWire.model_validate(meeting_note_wire(note)))


def _meetings_delete(p: BaseModel, ctx: HandlerContext, _rid: str) -> FeatureDeletedResult:
    assert isinstance(p, FeatureIdParams)
    w = _require_worker(ctx)
    return FeatureDeletedResult(deleted=bool(_feature_call(lambda: w.delete_meeting_note(p.id))))


def _schedule_item_result(res: ScheduleItem | ScheduleConflictEntity) -> ScheduleItemResult:
    if isinstance(res, ScheduleConflictEntity):
        return ScheduleItemResult(
            item=None,
            conflict=ScheduleConflictWire.model_validate(schedule_conflict_wire(res)),
        )
    return ScheduleItemResult(
        item=ScheduleItemWire.model_validate(schedule_item_wire(res)),
        conflict=None,
    )


def _schedule_day(p: BaseModel, ctx: HandlerContext, _rid: str) -> ScheduleDayResult:
    assert isinstance(p, ScheduleDayParams)
    items = _require_worker(ctx).schedule_day(p.date)
    return ScheduleDayResult(
        date=p.date,
        items=[ScheduleItemWire.model_validate(schedule_item_wire(i)) for i in items],
    )


def _schedule_week(p: BaseModel, ctx: HandlerContext, _rid: str) -> ScheduleWeekResult:
    assert isinstance(p, ScheduleWeekParams)
    w = _require_worker(ctx)
    groups = _feature_call(lambda: w.schedule_week(p.start_date))
    return ScheduleWeekResult(
        start_date=p.start_date,
        days=[
            ScheduleDayGroup(
                date=d,
                items=[ScheduleItemWire.model_validate(schedule_item_wire(i)) for i in items],
            )
            for d, items in groups
        ],
    )


def _schedule_create_item(p: BaseModel, ctx: HandlerContext, _rid: str) -> ScheduleItemResult:
    assert isinstance(p, ScheduleCreateItemParams)
    w = _require_worker(ctx)
    return _schedule_item_result(
        _feature_call(
            lambda: w.create_schedule_item(
                p.title,
                p.start_time,
                p.end_time,
                location=p.location,
                notes=p.notes,
                overwrite_ids=p.overwrite_ids,
            )
        )
    )


def _schedule_update(p: BaseModel, ctx: HandlerContext, _rid: str) -> ScheduleItemResult:
    assert isinstance(p, ScheduleUpdateParams)
    w = _require_worker(ctx)
    fields = _drop_none(
        title=p.title,
        start_time=p.start_time,
        end_time=p.end_time,
        location=p.location,
        notes=p.notes,
    )
    return _schedule_item_result(_feature_call(lambda: w.update_schedule_item(p.id, **fields)))


def _schedule_delete(p: BaseModel, ctx: HandlerContext, _rid: str) -> FeatureDeletedResult:
    assert isinstance(p, FeatureIdParams)
    w = _require_worker(ctx)
    return FeatureDeletedResult(deleted=bool(_feature_call(lambda: w.delete_schedule_item(p.id))))


# -- Settings & Diagnostics (Step 3.4) -------------------------------------


def _settings_result(cfg: AppConfig) -> SettingsResult:
    return SettingsResult.model_validate(backend_settings.resolved(cfg))


def _settings_get(_p: BaseModel, ctx: HandlerContext, _rid: str) -> SettingsResult:
    return _settings_result(AppConfig.load(ctx.app_config_path))


def _settings_update(p: BaseModel, ctx: HandlerContext, _rid: str) -> SettingsResult:
    assert isinstance(p, SettingsUpdateParams)
    written = p.model_fields_set
    changes: dict[str, object] = {}
    if "idle_timeout_minutes" in written:
        changes["idle_timeout_minutes"] = p.idle_timeout_minutes
    if "summary_time" in written:
        changes["summary_time"] = p.summary_time
    # the whole load-modify-save is serialised so two in-flight updates cannot
    # lose each other's field
    cfg = AppConfig.update_fields(ctx.app_config_path, **changes)
    return _settings_result(cfg)


def _data_info(_p: BaseModel, ctx: HandlerContext, _rid: str) -> DataInfoResult:
    badge = export_badge_state(AppConfig.load(ctx.app_config_path))
    return DataInfoResult(
        last_exported_at=badge.last_exported_at,
        days_since=badge.days_since,
        needs_export=badge.needs_export,
        settings_line=badge.settings_line,
        never_exported_line=NEVER_EXPORTED_LINE,
        uninstall_warning=UNINSTALL_WARNING,
        export_blurb=EXPORT_BLURB,
    )


def _data_export(p: BaseModel, ctx: HandlerContext, _rid: str) -> DataExportResult:
    assert isinstance(p, DataExportParams)
    w = _require_worker(ctx)
    try:
        exported_at, size = w.export_data(p.path)  # runs on the worker thread
    except OSError as exc:
        raise MethodError("export_failed", f"could not write the export: {exc}") from exc
    return DataExportResult(path=p.path, exported_at=exported_at, bytes_written=size)


def _data_wipe(p: BaseModel, ctx: HandlerContext, _rid: str) -> DataWipeResult:
    assert isinstance(p, DataWipeParams)
    if not p.confirm:
        raise MethodError("invalid_params", "wipe requires confirm=true")
    _require_worker(ctx).wipe_data()
    return DataWipeResult(wiped=True)


def _diagnostics_logs(p: BaseModel, ctx: HandlerContext, _rid: str) -> DiagnosticsLogsResult:
    assert isinstance(p, DiagnosticsLogsParams)
    entries, truncated = diagnostics.read_log_entries(p.level, p.limit)
    return DiagnosticsLogsResult(
        entries=[
            LogEntry(timestamp=e.timestamp, level=e.level, logger=e.logger, message=e.message)
            for e in entries
        ],
        truncated=truncated,
    )


def _diagnostics_metrics(p: BaseModel, ctx: HandlerContext, _rid: str) -> DiagnosticsMetricsResult:
    assert isinstance(p, DiagnosticsMetricsParams)
    store = MetricsStore(db_path=diagnostics.metrics_db_path())
    summary = diagnostics.aggregate_metrics(store.query_recent(limit=p.limit, env="backend"))
    lat = summary.retrieval_latency_ms
    dist = summary.confidence_distribution
    return DiagnosticsMetricsResult(
        sample_size=summary.sample_size,
        retrieval_latency_ms=(
            LatencyPercentiles(p50=lat["p50"], p95=lat["p95"], p99=lat["p99"]) if lat else None
        ),
        confidence_distribution=ConfidenceDistribution(
            high=dist["high"], medium=dist["medium"], low=dist["low"], none=dist["none"]
        ),
        grounded_rate=summary.grounded_rate,
        retrieval_hit_rate=summary.retrieval_hit_rate,
    )


def _diagnostics_report(p: BaseModel, ctx: HandlerContext, _rid: str) -> DiagnosticsReportResult:
    assert isinstance(p, DiagnosticsReportParams)
    try:
        written = write_redacted_report(p.path)
    except OSError as exc:
        raise MethodError("report_failed", f"could not write the report: {exc}") from exc
    return DiagnosticsReportResult(path=p.path, bytes_written=written)


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
    "chat.send": _chat_send,
    "chat.confirm_action": _chat_confirm_action,
    "chat.new": _chat_new,
    "chat.history": _chat_history,
    "reminders.reconciliation": _reminders_reconciliation,
    "reminders.list": _reminders_list,
    "reminders.complete": _reminders_complete,
    "reminders.dismiss": _reminders_dismiss,
    "reminders.reschedule": _reminders_reschedule,
    "reminders.update": _reminders_update,
    "reminders.delete": _reminders_delete,
    "todos.list": _todos_list,
    "todos.complete": _todos_complete,
    "todos.update": _todos_update,
    "todos.delete": _todos_delete,
    "meetings.list": _meetings_list,
    "meetings.get": _meetings_get,
    "meetings.capture": _meetings_capture,
    "meetings.delete": _meetings_delete,
    "schedule.day": _schedule_day,
    "schedule.week": _schedule_week,
    "schedule.create_item": _schedule_create_item,
    "schedule.update": _schedule_update,
    "schedule.delete": _schedule_delete,
    "settings.get": _settings_get,
    "settings.update": _settings_update,
    "data.info": _data_info,
    "data.export": _data_export,
    "data.wipe": _data_wipe,
    "diagnostics.logs": _diagnostics_logs,
    "diagnostics.metrics": _diagnostics_metrics,
    "diagnostics.report": _diagnostics_report,
}
