"""The IPC **method contract** — request ``params`` and response ``result``
models for every method the backend serves (Phase 3 Step 3.1a).

This is the Python half of project_logic.md §7's "zod (TypeScript) + Pydantic
(Python)" contract. The envelope (``envelope.py``) frames every message and
carries the ``version``; this module types what rides inside ``payload`` for a
given ``method``. A ``ipc/schema/methods.ts`` zod mirror + a cross-language
round-trip test land with the frontend sub-step.

Everything here is **additive under IPC version 1** — a new optional field or a
new method does not bump ``CURRENT_IPC_VERSION``; removing/retyping a field
does.

Payload shapes (all under the frozen ``IPCEnvelope.payload``):

    request   {"method": <name>, "params": {...}}
    response  {"method": <name>, "result": {...}}
    error     {"method": <name>?, "code": <slug>, "message": <text>}
    event     {"method": <name>, "params": {...}}
"""

from __future__ import annotations

from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field

# ``model_*`` field names (model.catalog / model.download results) collide with
# Pydantic v2's protected namespace; these are wire DTOs, not models with
# behaviour, so opting out is safe and silences the warning.
_WIRE = ConfigDict(extra="forbid", protected_namespaces=())


class _Params(BaseModel):
    """Base for every request-params model: reject unknown fields so a stale
    or malformed frontend call surfaces as ``validation_error``, not a
    silently-ignored argument."""

    model_config = _WIRE


class _Result(BaseModel):
    model_config = _WIRE


# --------------------------------------------------------------------------
# app.status
# --------------------------------------------------------------------------
class AppStatusParams(_Params):
    pass


class AppStatusResult(_Result):
    ipc_version: int
    model_setup_required: bool
    active_model: str | None
    last_exported_at: str | None
    degraded: bool
    ready: bool


# --------------------------------------------------------------------------
# health.check
# --------------------------------------------------------------------------
class HealthCheckParams(_Params):
    pass


class HealthCheckResult(_Result):
    ok: bool
    details: list[str]


# --------------------------------------------------------------------------
# model.catalog
# --------------------------------------------------------------------------
class CatalogEntry(_Result):
    name: str
    display_name: str
    size_bytes: int
    description: str
    min_ram_gb: float
    recommended: bool


class ModelCatalogParams(_Params):
    pass


class ModelCatalogResult(_Result):
    models: list[CatalogEntry]


# --------------------------------------------------------------------------
# model.status
# --------------------------------------------------------------------------
class ModelStatusParams(_Params):
    #: None → status for the catalog + everything installed.
    names: list[str] | None = None


class InstalledEntry(_Result):
    name: str
    size_bytes: int


class ModelStatusResult(_Result):
    ollama_running: bool
    installed: list[InstalledEntry]
    #: model name → "not_installed" | "downloading" | "available" | "active"
    statuses: dict[str, str]


# --------------------------------------------------------------------------
# model.download  (streaming — model.download.progress events, then a result)
# --------------------------------------------------------------------------
class ModelDownloadParams(_Params):
    name: str = Field(min_length=1)


class ModelDownloadProgressEvent(_Result):
    name: str
    phase: str
    percent: float
    speed_mbps: float
    eta_seconds: float | None
    message: str | None


class ModelDownloadResult(_Result):
    model_name: str
    status: str
    restarts: int
    resumes: int
    verified: bool


# --------------------------------------------------------------------------
# model.activate
# --------------------------------------------------------------------------
class ModelActivateParams(_Params):
    name: str = Field(min_length=1)


class ModelActivateResult(_Result):
    active_model: str
    verified: bool


# --------------------------------------------------------------------------
# backup.list
# --------------------------------------------------------------------------
class BackupListParams(_Params):
    pass


class BackupEntry(_Result):
    path: str
    created_at: str
    size_bytes: int


class BackupListResult(_Result):
    backups: list[BackupEntry]


# --------------------------------------------------------------------------
# backup.restore  (validate-only; the Rust supervisor performs the swap)
# --------------------------------------------------------------------------
class BackupRestoreParams(_Params):
    path: str = Field(min_length=1)


class BackupRestoreResult(_Result):
    ok: bool
    needs_restart: bool
    detail: str
    validated_snapshot_path: str | None


# --------------------------------------------------------------------------
# app.shutdown
# --------------------------------------------------------------------------
class AppShutdownParams(_Params):
    pass


class AppShutdownResult(_Result):
    stopping: bool


# --------------------------------------------------------------------------
# chat.send / chat.new / chat.history  (routed to the SessionWorker thread)
# --------------------------------------------------------------------------
class ChatSendParams(_Params):
    text: str = Field(min_length=1)


class ChatCitation(_Result):
    chunk_id: str
    session_id: str
    approximate_timestamp: str


class ChatFeature(_Result):
    #: "reminder" | "todo" | "schedule_item" | "meeting_note" | "summary"
    kind: str
    id: str
    summary: str


class ChatDisambiguation(_Result):
    pending_action_id: str
    #: action-type values the user picks between (plus "conversation" to dismiss)
    options: list[str]


class ChatConflictItem(_Result):
    id: str
    title: str
    start_time: str
    end_time: str
    location: str


class ChatConflict(_Result):
    attempted: ChatConflictItem
    conflicts_with: list[ChatConflictItem]


class ChatSendResult(_Result):
    session_id: str
    turn_index: int
    answer: str
    confidence: float
    #: project_logic §3: 1 auto-execute, 2 disambiguation, 3 clarification, 4 conversation
    tier: int
    action_type: str
    retrieve_needed: bool
    retrieval_route: str | None
    is_grounded: bool
    grounding_confidence: float
    citations: list[ChatCitation] = []
    warnings: list[str] = []
    #: 3.1d — a Tier-1 actionable message's outcome
    feature: ChatFeature | None = None
    disambiguation: ChatDisambiguation | None = None
    conflict: ChatConflict | None = None
    #: a stale disambiguation popup was just discarded by this message
    dismissed_pending: bool = False


class ChatNewParams(_Params):
    pass


class ChatNewResult(_Result):
    session_id: str


class ChatHistoryParams(_Params):
    session_id: str | None = None


class ChatMessage(_Result):
    turn_index: int
    role: str
    content: str
    created_at: str


class ChatHistoryResult(_Result):
    session_id: str
    messages: list[ChatMessage]


class ChatConfirmActionParams(_Params):
    pending_action_id: str = Field(min_length=1)
    #: an action-type value, or "conversation" to dismiss
    choice: str = Field(min_length=1)


#: same shape as chat.send's result
ChatConfirmActionResult = ChatSendResult


# --------------------------------------------------------------------------
# reminders.reconciliation  (on-launch overdue / pending-ack lists, §9)
# --------------------------------------------------------------------------
class ReminderWire(_Result):
    id: str
    title: str
    scheduled_time: str
    notes: str
    fired_at: str | None
    completed_at: str | None
    dismissed_at: str | None
    created_at: str


class RemindersReconciliationParams(_Params):
    pass


class RemindersReconciliationResult(_Result):
    overdue: list[ReminderWire]
    pending_acknowledgment: list[ReminderWire]


class AppRemindersPendingEvent(_Result):
    overdue: list[ReminderWire]
    pending_acknowledgment: list[ReminderWire]


# --------------------------------------------------------------------------
# Feature views — reminders / todos / meetings / schedule CRUD  (Step 3.3)
#
# Every method is worker=True (the session DB connection is thread-affine on the
# SessionWorker, same reason chat.* / health.check are) and degraded_ok=False
# (degraded mode has no worker). Inline NLP create in each view goes through
# chat.send, so there is no *.create here except schedule.create_item (which
# carries the Q2 overwrite/keep resolution) and meetings.capture (transcript
# extraction — deliberately not agentic; "no conversational response").
# --------------------------------------------------------------------------


class FeatureIdParams(_Params):
    id: str = Field(min_length=1)


class FeatureDeletedResult(_Result):
    deleted: bool


# -- reminders -------------------------------------------------------------
class RemindersListParams(_Params):
    pass


class RemindersListResult(_Result):
    #: active reminders only (not completed / dismissed / deleted)
    reminders: list[ReminderWire]


class ReminderRescheduleParams(_Params):
    id: str = Field(min_length=1)
    scheduled_time: str = Field(min_length=1)


class ReminderUpdateParams(_Params):
    id: str = Field(min_length=1)
    title: str | None = None
    notes: str | None = None
    scheduled_time: str | None = None


class ReminderResult(_Result):
    reminder: ReminderWire


# -- todos ---------------------------------------------------------------
class TodoWire(_Result):
    id: str
    session_id: str | None
    title: str
    notes: str
    priority: str | None
    category: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


class TodosListParams(_Params):
    pass


class TodosListResult(_Result):
    #: every non-deleted todo (active + completed) — the view splits them into
    #: the list and the "Completed" tab; capped server-side at the newest 500.
    todos: list[TodoWire]


class TodoUpdateParams(_Params):
    id: str = Field(min_length=1)
    title: str | None = None
    notes: str | None = None
    priority: str | None = None
    category: str | None = None


class TodoResult(_Result):
    todo: TodoWire


# -- meetings ----------------------------------------------------------
class ActionItemWire(_Result):
    task: str
    owner: str | None
    deadline: str | None


class MeetingNoteWire(_Result):
    id: str
    session_id: str | None
    raw_transcript: str
    attendees: list[str]
    topics: list[str]
    decisions: list[str]
    action_items: list[ActionItemWire]
    follow_ups: list[str]
    needs_review: bool
    searchable_text: str
    created_at: str
    updated_at: str


class MeetingsListParams(_Params):
    pass


class MeetingsListResult(_Result):
    meetings: list[MeetingNoteWire]


class MeetingGetResult(_Result):
    meeting: MeetingNoteWire | None


class MeetingsCaptureParams(_Params):
    transcript: str = Field(min_length=1)


class MeetingResult(_Result):
    meeting: MeetingNoteWire


# -- schedule --------------------------------------------------------
class ScheduleItemWire(_Result):
    id: str
    schedule_id: str
    title: str
    start_time: str
    end_time: str
    location: str
    notes: str
    created_at: str
    updated_at: str


class ScheduleConflictWire(_Result):
    attempted: ScheduleItemWire
    conflicts_with: list[ScheduleItemWire]


class ScheduleDayParams(_Params):
    date: str = Field(min_length=1)


class ScheduleDayGroup(_Result):
    date: str
    items: list[ScheduleItemWire]


class ScheduleDayResult(_Result):
    date: str
    items: list[ScheduleItemWire]


class ScheduleWeekParams(_Params):
    start_date: str = Field(min_length=1)


class ScheduleWeekResult(_Result):
    start_date: str
    days: list[ScheduleDayGroup]


class ScheduleCreateItemParams(_Params):
    title: str = Field(min_length=1)
    start_time: str = Field(min_length=1)
    end_time: str = Field(min_length=1)
    location: str = ""
    notes: str = ""
    #: Q2 — non-empty resolves a conflict: each id (must be in the current
    #: conflict set) is soft-deleted, then the item is created.
    overwrite_ids: list[str] = []


class ScheduleUpdateParams(_Params):
    id: str = Field(min_length=1)
    title: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    notes: str | None = None


class ScheduleItemResult(_Result):
    #: exactly one of these is set — a created/updated item, or an unresolved conflict
    item: ScheduleItemWire | None
    conflict: ScheduleConflictWire | None


# --------------------------------------------------------------------------
# Settings → General  (Step 3.4) — persisted as AppConfig fields; the results
# carry the *effective* value (config → env → YAML → default) plus a flag per
# field for the "Reset to default" affordance.
# --------------------------------------------------------------------------
class SettingsGetParams(_Params):
    pass


class SettingsResult(_Result):
    idle_timeout_minutes: int
    summary_time: str
    idle_timeout_is_default: bool
    summary_time_is_default: bool


class SettingsUpdateParams(_Params):
    #: A field present with a value → write it; present as ``null`` → clear the
    #: override (back to the env/YAML default); absent → leave unchanged. The
    #: handler uses ``model_fields_set`` to tell "null" from "absent".
    idle_timeout_minutes: int | None = Field(default=None, ge=1, le=1440)
    summary_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


# --------------------------------------------------------------------------
# Data & Privacy  (Step 3.4)
# --------------------------------------------------------------------------
class DataInfoParams(_Params):
    pass


class DataInfoResult(_Result):
    last_exported_at: str | None
    days_since: int | None
    needs_export: bool
    settings_line: str
    never_exported_line: str
    uninstall_warning: str
    export_blurb: str


class DataExportParams(_Params):
    path: str = Field(min_length=1)


class DataExportResult(_Result):
    path: str
    exported_at: str
    bytes_written: int


class DataWipeParams(_Params):
    #: must be ``true`` — a second guard behind the frontend confirm dialog
    confirm: bool


class DataWipeResult(_Result):
    wiped: bool


# --------------------------------------------------------------------------
# Diagnostics  (Step 3.4)
# --------------------------------------------------------------------------
class DiagnosticsLogsParams(_Params):
    #: "DEBUG" | "INFO" | "WARNING" | "ERROR" — None = all levels
    level: str | None = None
    limit: int = Field(default=200, ge=1, le=2000)


class LogEntry(_Result):
    timestamp: str
    level: str
    logger: str
    message: str


class DiagnosticsLogsResult(_Result):
    entries: list[LogEntry]
    #: True when older entries existed beyond ``limit``
    truncated: bool


class DiagnosticsMetricsParams(_Params):
    limit: int = Field(default=1000, ge=1, le=10000)


class LatencyPercentiles(_Result):
    p50: float
    p95: float
    p99: float


class ConfidenceDistribution(_Result):
    high: int
    medium: int
    low: int
    none: int


class DiagnosticsMetricsResult(_Result):
    sample_size: int
    retrieval_latency_ms: LatencyPercentiles | None
    confidence_distribution: ConfidenceDistribution
    grounded_rate: float | None
    retrieval_hit_rate: float | None
    #: not instrumented yet (v1.1) — the UI renders "not tracked yet"
    error_rate: None = None
    compute_ms: None = None


class DiagnosticsReportParams(_Params):
    path: str = Field(min_length=1)


class DiagnosticsReportResult(_Result):
    path: str
    bytes_written: int


# --------------------------------------------------------------------------
# Lifecycle events (server-initiated; no request/params from the frontend)
# --------------------------------------------------------------------------
class AppReadyEvent(_Result):
    ipc_version: int
    model_setup_required: bool
    active_model: str | None


class AppIntegrityFailedEvent(_Result):
    details: list[str]


class AppPreviousDataUnrecoverableEvent(_Result):
    message: str


class AppRestoreStagedEvent(_Result):
    validated_snapshot_path: str


# Toast events (Phase 4 Step 4.6) — backend → Tauri shell only. The Rust stdout
# reader routes any `event` whose method starts with `toast.` to the WinRT
# module (`src-tauri/src/toast.rs`) and does NOT forward it to the webview.
# `toast_id` is client-generated and used verbatim as the
# `ScheduledToastNotification.Tag`, so cancel needs no round-trip.
class ToastRegisterEvent(_Result):
    toast_id: str
    reminder_id: str
    scheduled_time: str
    body: str


class ToastCancelEvent(_Result):
    toast_id: str


class ToastFireEvent(_Result):
    reminder_id: str
    body: str


class ToastCancelAllEvent(_Result):
    pass


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
class MethodContract(NamedTuple):
    params: type[BaseModel]
    result: type[BaseModel]
    #: served while the backend is in degraded (integrity-failed) mode
    degraded_ok: bool = False
    #: runs on the single-threaded SessionWorker (owns the session DB
    #: connection + the vector index), not the dispatcher's general pool
    worker: bool = False


METHOD_CONTRACTS: dict[str, MethodContract] = {
    "app.status": MethodContract(AppStatusParams, AppStatusResult, degraded_ok=True),
    "health.check": MethodContract(HealthCheckParams, HealthCheckResult, worker=True),
    "model.catalog": MethodContract(ModelCatalogParams, ModelCatalogResult),
    "model.status": MethodContract(ModelStatusParams, ModelStatusResult),
    "model.download": MethodContract(ModelDownloadParams, ModelDownloadResult),
    "model.activate": MethodContract(ModelActivateParams, ModelActivateResult),
    "backup.list": MethodContract(BackupListParams, BackupListResult, degraded_ok=True),
    "backup.restore": MethodContract(BackupRestoreParams, BackupRestoreResult, degraded_ok=True),
    "app.shutdown": MethodContract(AppShutdownParams, AppShutdownResult, degraded_ok=True),
    "chat.send": MethodContract(ChatSendParams, ChatSendResult, worker=True),
    "chat.new": MethodContract(ChatNewParams, ChatNewResult, worker=True),
    "chat.history": MethodContract(ChatHistoryParams, ChatHistoryResult, worker=True),
    "chat.confirm_action": MethodContract(
        ChatConfirmActionParams, ChatConfirmActionResult, worker=True
    ),
    "reminders.reconciliation": MethodContract(
        RemindersReconciliationParams, RemindersReconciliationResult, worker=True
    ),
    # -- feature views (Step 3.3) — all worker=True, degraded_ok=False --------
    "reminders.list": MethodContract(RemindersListParams, RemindersListResult, worker=True),
    "reminders.complete": MethodContract(FeatureIdParams, ReminderResult, worker=True),
    "reminders.dismiss": MethodContract(FeatureIdParams, ReminderResult, worker=True),
    "reminders.reschedule": MethodContract(ReminderRescheduleParams, ReminderResult, worker=True),
    "reminders.update": MethodContract(ReminderUpdateParams, ReminderResult, worker=True),
    "reminders.delete": MethodContract(FeatureIdParams, FeatureDeletedResult, worker=True),
    "todos.list": MethodContract(TodosListParams, TodosListResult, worker=True),
    "todos.complete": MethodContract(FeatureIdParams, TodoResult, worker=True),
    "todos.update": MethodContract(TodoUpdateParams, TodoResult, worker=True),
    "todos.delete": MethodContract(FeatureIdParams, FeatureDeletedResult, worker=True),
    "meetings.list": MethodContract(MeetingsListParams, MeetingsListResult, worker=True),
    "meetings.get": MethodContract(FeatureIdParams, MeetingGetResult, worker=True),
    "meetings.capture": MethodContract(MeetingsCaptureParams, MeetingResult, worker=True),
    "meetings.delete": MethodContract(FeatureIdParams, FeatureDeletedResult, worker=True),
    "schedule.day": MethodContract(ScheduleDayParams, ScheduleDayResult, worker=True),
    "schedule.week": MethodContract(ScheduleWeekParams, ScheduleWeekResult, worker=True),
    "schedule.create_item": MethodContract(
        ScheduleCreateItemParams, ScheduleItemResult, worker=True
    ),
    "schedule.update": MethodContract(ScheduleUpdateParams, ScheduleItemResult, worker=True),
    "schedule.delete": MethodContract(FeatureIdParams, FeatureDeletedResult, worker=True),
    # -- Settings & Diagnostics (Step 3.4) ----------------------------------
    "settings.get": MethodContract(SettingsGetParams, SettingsResult, degraded_ok=True),
    "settings.update": MethodContract(SettingsUpdateParams, SettingsResult, degraded_ok=True),
    "data.info": MethodContract(DataInfoParams, DataInfoResult, degraded_ok=True),
    "data.export": MethodContract(DataExportParams, DataExportResult, worker=True),
    "data.wipe": MethodContract(DataWipeParams, DataWipeResult, worker=True),
    "diagnostics.logs": MethodContract(
        DiagnosticsLogsParams, DiagnosticsLogsResult, degraded_ok=True
    ),
    "diagnostics.metrics": MethodContract(
        DiagnosticsMetricsParams, DiagnosticsMetricsResult, degraded_ok=True
    ),
    "diagnostics.report": MethodContract(
        DiagnosticsReportParams, DiagnosticsReportResult, degraded_ok=True
    ),
}
