/**
 * IPC **method contract** — the zod mirror of `src/common/ipc/methods.py`
 * (`METHOD_CONTRACTS` + the lifecycle/streaming event models).
 *
 * The envelope (`envelope.ts`) frames every message and carries `version`;
 * this module types what rides inside `payload` for a given `method`. Kept in
 * sync with the Pydantic side by `tests/common/test_ipc_methods_roundtrip.py`
 * — a real Pydantic→zod round-trip per method, not convention alone.
 *
 * Every object is `.strict()` to match Pydantic's `extra="forbid"`. Everything
 * here is additive under IPC version 1 (a new optional field / method does not
 * bump `CURRENT_IPC_VERSION`).
 *
 * Payload shapes (all under the frozen `IPCEnvelope.payload`):
 *
 *   request   {"method": <name>, "params": {...}}
 *   response  {"method": <name>, "result": {...}}
 *   error     {"method": <name>?, "code": <slug>, "message": <text>}
 *   event     {"method": <name>, "params": {...}}
 */
import { z } from "zod";

// --------------------------------------------------------------------------
// app.status
// --------------------------------------------------------------------------
export const AppStatusParams = z.object({}).strict();
export const AppStatusResult = z
  .object({
    ipc_version: z.number().int(),
    model_setup_required: z.boolean(),
    active_model: z.string().nullable(),
    last_exported_at: z.string().nullable(),
    degraded: z.boolean(),
    ready: z.boolean(),
  })
  .strict();

// --------------------------------------------------------------------------
// health.check
// --------------------------------------------------------------------------
export const HealthCheckParams = z.object({}).strict();
export const HealthCheckResult = z
  .object({
    ok: z.boolean(),
    details: z.array(z.string()),
  })
  .strict();

// --------------------------------------------------------------------------
// model.catalog
// --------------------------------------------------------------------------
export const CatalogEntry = z
  .object({
    name: z.string(),
    display_name: z.string(),
    size_bytes: z.number().int(),
    description: z.string(),
    min_ram_gb: z.number(),
    recommended: z.boolean(),
  })
  .strict();
export const ModelCatalogParams = z.object({}).strict();
export const ModelCatalogResult = z.object({ models: z.array(CatalogEntry) }).strict();

// --------------------------------------------------------------------------
// model.status
// --------------------------------------------------------------------------
export const ModelStatusParams = z
  .object({
    // None → status for the catalog + everything installed.
    names: z.array(z.string()).nullable().default(null),
  })
  .strict();
export const InstalledEntry = z
  .object({ name: z.string(), size_bytes: z.number().int() })
  .strict();
export const ModelStatusResult = z
  .object({
    ollama_running: z.boolean(),
    installed: z.array(InstalledEntry),
    // model name → "not_installed" | "downloading" | "available" | "active"
    statuses: z.record(z.string(), z.string()),
  })
  .strict();

// --------------------------------------------------------------------------
// model.download  (streaming — model.download.progress events, then a result)
// --------------------------------------------------------------------------
export const ModelDownloadParams = z.object({ name: z.string().min(1) }).strict();
export const ModelDownloadProgressEvent = z
  .object({
    name: z.string(),
    phase: z.string(),
    percent: z.number(),
    speed_mbps: z.number(),
    eta_seconds: z.number().nullable(),
    message: z.string().nullable(),
  })
  .strict();
export const ModelDownloadResult = z
  .object({
    model_name: z.string(),
    status: z.string(),
    restarts: z.number().int(),
    resumes: z.number().int(),
    verified: z.boolean(),
  })
  .strict();

// --------------------------------------------------------------------------
// model.activate
// --------------------------------------------------------------------------
export const ModelActivateParams = z.object({ name: z.string().min(1) }).strict();
export const ModelActivateResult = z
  .object({ active_model: z.string(), verified: z.boolean() })
  .strict();

// --------------------------------------------------------------------------
// backup.list
// --------------------------------------------------------------------------
export const BackupListParams = z.object({}).strict();
export const BackupEntry = z
  .object({
    path: z.string(),
    created_at: z.string(),
    size_bytes: z.number().int(),
  })
  .strict();
export const BackupListResult = z.object({ backups: z.array(BackupEntry) }).strict();

// --------------------------------------------------------------------------
// backup.restore  (validate-only; the Rust supervisor performs the swap)
// --------------------------------------------------------------------------
export const BackupRestoreParams = z.object({ path: z.string().min(1) }).strict();
export const BackupRestoreResult = z
  .object({
    ok: z.boolean(),
    needs_restart: z.boolean(),
    detail: z.string(),
    validated_snapshot_path: z.string().nullable(),
  })
  .strict();

// --------------------------------------------------------------------------
// app.shutdown
// --------------------------------------------------------------------------
export const AppShutdownParams = z.object({}).strict();
export const AppShutdownResult = z.object({ stopping: z.boolean() }).strict();

// --------------------------------------------------------------------------
// chat.send / chat.new / chat.history / chat.confirm_action
// --------------------------------------------------------------------------
export const ChatSendParams = z.object({ text: z.string().min(1) }).strict();

export const ChatCitation = z
  .object({
    chunk_id: z.string(),
    session_id: z.string(),
    approximate_timestamp: z.string(),
  })
  .strict();

export const ChatFeature = z
  .object({
    // "reminder" | "todo" | "schedule_item" | "meeting_note" | "summary"
    kind: z.string(),
    id: z.string(),
    summary: z.string(),
  })
  .strict();

export const ChatDisambiguation = z
  .object({
    pending_action_id: z.string(),
    // action-type values the user picks between (plus "conversation" to dismiss)
    options: z.array(z.string()),
  })
  .strict();

export const ChatConflictItem = z
  .object({
    id: z.string(),
    title: z.string(),
    start_time: z.string(),
    end_time: z.string(),
    location: z.string(),
  })
  .strict();

export const ChatConflict = z
  .object({
    attempted: ChatConflictItem,
    conflicts_with: z.array(ChatConflictItem),
  })
  .strict();

export const ChatSendResult = z
  .object({
    session_id: z.string(),
    turn_index: z.number().int(),
    answer: z.string(),
    confidence: z.number(),
    // project_logic §3: 1 auto-execute, 2 disambiguation, 3 clarification, 4 conversation
    tier: z.number().int(),
    action_type: z.string(),
    retrieve_needed: z.boolean(),
    retrieval_route: z.string().nullable(),
    is_grounded: z.boolean(),
    grounding_confidence: z.number(),
    citations: z.array(ChatCitation).default([]),
    warnings: z.array(z.string()).default([]),
    // 3.1d — a Tier-1 actionable message's outcome
    feature: ChatFeature.nullable().default(null),
    disambiguation: ChatDisambiguation.nullable().default(null),
    conflict: ChatConflict.nullable().default(null),
    // a stale disambiguation popup was just discarded by this message
    dismissed_pending: z.boolean().default(false),
  })
  .strict();

export const ChatNewParams = z.object({}).strict();
export const ChatNewResult = z.object({ session_id: z.string() }).strict();

export const ChatHistoryParams = z
  .object({ session_id: z.string().nullable().default(null) })
  .strict();
export const ChatMessage = z
  .object({
    turn_index: z.number().int(),
    role: z.string(),
    content: z.string(),
    created_at: z.string(),
  })
  .strict();
export const ChatHistoryResult = z
  .object({ session_id: z.string(), messages: z.array(ChatMessage) })
  .strict();

export const ChatConfirmActionParams = z
  .object({
    pending_action_id: z.string().min(1),
    // an action-type value, or "conversation" to dismiss
    choice: z.string().min(1),
  })
  .strict();
// same shape as chat.send's result
export const ChatConfirmActionResult = ChatSendResult;

// --------------------------------------------------------------------------
// reminders.reconciliation  (on-launch overdue / pending-ack lists, §9)
// --------------------------------------------------------------------------
export const ReminderWire = z
  .object({
    id: z.string(),
    title: z.string(),
    scheduled_time: z.string(),
    notes: z.string(),
    fired_at: z.string().nullable(),
    completed_at: z.string().nullable(),
    dismissed_at: z.string().nullable(),
    created_at: z.string(),
  })
  .strict();
export const RemindersReconciliationParams = z.object({}).strict();
export const RemindersReconciliationResult = z
  .object({
    overdue: z.array(ReminderWire),
    pending_acknowledgment: z.array(ReminderWire),
  })
  .strict();

// --------------------------------------------------------------------------
// Feature views — reminders / todos / meetings / schedule CRUD  (Step 3.3)
// Every method is worker=true, degradedOk=false.
// --------------------------------------------------------------------------
export const FeatureIdParams = z.object({ id: z.string().min(1) }).strict();
export const FeatureDeletedResult = z.object({ deleted: z.boolean() }).strict();

// reminders (reuses ReminderWire above)
export const RemindersListParams = z.object({}).strict();
export const RemindersListResult = z.object({ reminders: z.array(ReminderWire) }).strict();
export const ReminderRescheduleParams = z
  .object({ id: z.string().min(1), scheduled_time: z.string().min(1) })
  .strict();
export const ReminderUpdateParams = z
  .object({
    id: z.string().min(1),
    title: z.string().nullable().default(null),
    notes: z.string().nullable().default(null),
    scheduled_time: z.string().nullable().default(null),
  })
  .strict();
export const ReminderResult = z.object({ reminder: ReminderWire }).strict();

// todos
export const TodoWire = z
  .object({
    id: z.string(),
    session_id: z.string().nullable(),
    title: z.string(),
    notes: z.string(),
    priority: z.string().nullable(),
    category: z.string().nullable(),
    completed_at: z.string().nullable(),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .strict();
export const TodosListParams = z.object({}).strict();
export const TodosListResult = z.object({ todos: z.array(TodoWire) }).strict();
export const TodoUpdateParams = z
  .object({
    id: z.string().min(1),
    title: z.string().nullable().default(null),
    notes: z.string().nullable().default(null),
    priority: z.string().nullable().default(null),
    category: z.string().nullable().default(null),
  })
  .strict();
export const TodoResult = z.object({ todo: TodoWire }).strict();

// meetings
export const ActionItemWire = z
  .object({
    task: z.string(),
    owner: z.string().nullable(),
    deadline: z.string().nullable(),
  })
  .strict();
export const MeetingNoteWire = z
  .object({
    id: z.string(),
    session_id: z.string().nullable(),
    raw_transcript: z.string(),
    attendees: z.array(z.string()),
    topics: z.array(z.string()),
    decisions: z.array(z.string()),
    action_items: z.array(ActionItemWire),
    follow_ups: z.array(z.string()),
    needs_review: z.boolean(),
    searchable_text: z.string(),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .strict();
export const MeetingsListParams = z.object({}).strict();
export const MeetingsListResult = z.object({ meetings: z.array(MeetingNoteWire) }).strict();
export const MeetingGetResult = z.object({ meeting: MeetingNoteWire.nullable() }).strict();
export const MeetingsCaptureParams = z.object({ transcript: z.string().min(1) }).strict();
export const MeetingResult = z.object({ meeting: MeetingNoteWire }).strict();

// schedule
export const ScheduleItemWire = z
  .object({
    id: z.string(),
    schedule_id: z.string(),
    title: z.string(),
    start_time: z.string(),
    end_time: z.string(),
    location: z.string(),
    notes: z.string(),
    created_at: z.string(),
    updated_at: z.string(),
  })
  .strict();
export const ScheduleConflictWire = z
  .object({
    attempted: ScheduleItemWire,
    conflicts_with: z.array(ScheduleItemWire),
  })
  .strict();
export const ScheduleDayParams = z.object({ date: z.string().min(1) }).strict();
export const ScheduleDayGroup = z
  .object({ date: z.string(), items: z.array(ScheduleItemWire) })
  .strict();
export const ScheduleDayResult = z
  .object({ date: z.string(), items: z.array(ScheduleItemWire) })
  .strict();
export const ScheduleWeekParams = z.object({ start_date: z.string().min(1) }).strict();
export const ScheduleWeekResult = z
  .object({ start_date: z.string(), days: z.array(ScheduleDayGroup) })
  .strict();
export const ScheduleCreateItemParams = z
  .object({
    title: z.string().min(1),
    start_time: z.string().min(1),
    end_time: z.string().min(1),
    location: z.string().default(""),
    notes: z.string().default(""),
    overwrite_ids: z.array(z.string()).default([]),
  })
  .strict();
export const ScheduleUpdateParams = z
  .object({
    id: z.string().min(1),
    title: z.string().nullable().default(null),
    start_time: z.string().nullable().default(null),
    end_time: z.string().nullable().default(null),
    location: z.string().nullable().default(null),
    notes: z.string().nullable().default(null),
  })
  .strict();
export const ScheduleItemResult = z
  .object({
    item: ScheduleItemWire.nullable(),
    conflict: ScheduleConflictWire.nullable(),
  })
  .strict();

// --------------------------------------------------------------------------
// Lifecycle / streaming events (server-initiated; no request from the frontend)
// --------------------------------------------------------------------------
export const AppRemindersPendingEvent = z
  .object({
    overdue: z.array(ReminderWire),
    pending_acknowledgment: z.array(ReminderWire),
  })
  .strict();
export const AppReadyEvent = z
  .object({
    ipc_version: z.number().int(),
    model_setup_required: z.boolean(),
    active_model: z.string().nullable(),
  })
  .strict();
export const AppIntegrityFailedEvent = z.object({ details: z.array(z.string()) }).strict();
export const AppPreviousDataUnrecoverableEvent = z.object({ message: z.string() }).strict();
export const AppRestoreStagedEvent = z
  .object({ validated_snapshot_path: z.string() })
  .strict();

// --------------------------------------------------------------------------
// Registries
// --------------------------------------------------------------------------
export interface MethodContract {
  params: z.ZodTypeAny;
  result: z.ZodTypeAny;
  /** served while the backend is in degraded (integrity-failed) mode */
  degradedOk: boolean;
  /** runs on the single-threaded SessionWorker, not the dispatcher pool */
  worker: boolean;
}

export const METHOD_CONTRACTS = {
  "app.status": { params: AppStatusParams, result: AppStatusResult, degradedOk: true, worker: false },
  "health.check": { params: HealthCheckParams, result: HealthCheckResult, degradedOk: false, worker: true },
  "model.catalog": { params: ModelCatalogParams, result: ModelCatalogResult, degradedOk: false, worker: false },
  "model.status": { params: ModelStatusParams, result: ModelStatusResult, degradedOk: false, worker: false },
  "model.download": { params: ModelDownloadParams, result: ModelDownloadResult, degradedOk: false, worker: false },
  "model.activate": { params: ModelActivateParams, result: ModelActivateResult, degradedOk: false, worker: false },
  "backup.list": { params: BackupListParams, result: BackupListResult, degradedOk: true, worker: false },
  "backup.restore": { params: BackupRestoreParams, result: BackupRestoreResult, degradedOk: true, worker: false },
  "app.shutdown": { params: AppShutdownParams, result: AppShutdownResult, degradedOk: true, worker: false },
  "chat.send": { params: ChatSendParams, result: ChatSendResult, degradedOk: false, worker: true },
  "chat.new": { params: ChatNewParams, result: ChatNewResult, degradedOk: false, worker: true },
  "chat.history": { params: ChatHistoryParams, result: ChatHistoryResult, degradedOk: false, worker: true },
  "chat.confirm_action": {
    params: ChatConfirmActionParams,
    result: ChatConfirmActionResult,
    degradedOk: false,
    worker: true,
  },
  "reminders.reconciliation": {
    params: RemindersReconciliationParams,
    result: RemindersReconciliationResult,
    degradedOk: false,
    worker: true,
  },
  // -- feature views (Step 3.3) — all worker=true, degradedOk=false ----------
  "reminders.list": { params: RemindersListParams, result: RemindersListResult, degradedOk: false, worker: true },
  "reminders.complete": { params: FeatureIdParams, result: ReminderResult, degradedOk: false, worker: true },
  "reminders.dismiss": { params: FeatureIdParams, result: ReminderResult, degradedOk: false, worker: true },
  "reminders.reschedule": { params: ReminderRescheduleParams, result: ReminderResult, degradedOk: false, worker: true },
  "reminders.update": { params: ReminderUpdateParams, result: ReminderResult, degradedOk: false, worker: true },
  "reminders.delete": { params: FeatureIdParams, result: FeatureDeletedResult, degradedOk: false, worker: true },
  "todos.list": { params: TodosListParams, result: TodosListResult, degradedOk: false, worker: true },
  "todos.complete": { params: FeatureIdParams, result: TodoResult, degradedOk: false, worker: true },
  "todos.update": { params: TodoUpdateParams, result: TodoResult, degradedOk: false, worker: true },
  "todos.delete": { params: FeatureIdParams, result: FeatureDeletedResult, degradedOk: false, worker: true },
  "meetings.list": { params: MeetingsListParams, result: MeetingsListResult, degradedOk: false, worker: true },
  "meetings.get": { params: FeatureIdParams, result: MeetingGetResult, degradedOk: false, worker: true },
  "meetings.capture": { params: MeetingsCaptureParams, result: MeetingResult, degradedOk: false, worker: true },
  "meetings.delete": { params: FeatureIdParams, result: FeatureDeletedResult, degradedOk: false, worker: true },
  "schedule.day": { params: ScheduleDayParams, result: ScheduleDayResult, degradedOk: false, worker: true },
  "schedule.week": { params: ScheduleWeekParams, result: ScheduleWeekResult, degradedOk: false, worker: true },
  "schedule.create_item": { params: ScheduleCreateItemParams, result: ScheduleItemResult, degradedOk: false, worker: true },
  "schedule.update": { params: ScheduleUpdateParams, result: ScheduleItemResult, degradedOk: false, worker: true },
  "schedule.delete": { params: FeatureIdParams, result: FeatureDeletedResult, degradedOk: false, worker: true },
} as const satisfies Record<string, MethodContract>;

export type MethodName = keyof typeof METHOD_CONTRACTS;

/** Server-initiated events, keyed by the `payload.method` they arrive under. */
export const EVENT_SCHEMAS = {
  "app.ready": AppReadyEvent,
  "app.integrity_failed": AppIntegrityFailedEvent,
  "app.previous_data_unrecoverable": AppPreviousDataUnrecoverableEvent,
  "app.restore_staged": AppRestoreStagedEvent,
  "app.reminders_pending": AppRemindersPendingEvent,
  "model.download.progress": ModelDownloadProgressEvent,
} as const satisfies Record<string, z.ZodTypeAny>;

export type EventName = keyof typeof EVENT_SCHEMAS;
