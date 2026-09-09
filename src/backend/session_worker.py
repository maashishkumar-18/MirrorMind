"""The single-threaded owner of the chat pipeline (Phase 3 Step 3.1b).

``project_logic.md`` §4 end-to-end message flow. The session DB connection and
the ``SQLiteVectorStore`` in-memory index are ``sqlite3`` thread-affine, so
*everything* that touches them — message persistence, retrieval, generation,
re-ingestion — runs on this one thread. The dispatcher's general pool
(``model.*`` / ``app.status`` / ``backup.*``) never contends with it.

Warm-up (opening the DB, loading every embedding, loading the reranker) runs as
the prologue of ``run()`` before the queue loop starts, so a ``chat.send`` that
arrives early simply waits in the queue — it never sees a half-built worker.

Re-ingestion after each message is an async follow-up enqueued behind the
response (a full re-ingest includes a ~40-90s CPU metadata LLM call); shutdown
drains a pending one.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import src.backend.action_dispatch as action_dispatch
import src.backend.settings as backend_settings
from db.connection import open_session_db
from db.health import IntegrityResult, check_quick
from observability.metrics_store import MetricsStore, PipelineCallMetrics
from src.backend.paths import data_dir
from src.backend.session_repository import SessionRepository
from src.backend.slot_extractor import SlotExtractor
from src.backend.wire import MethodError
from src.common.sqlite_vector_store import SQLiteVectorStore
from src.common.types import (
    AgenticActionType,
    MeetingNote,
    ReconciliationResult,
    Reminder,
    ScheduleConflict,
    ScheduleItem,
    SessionRetrievedChunk,
    Todo,
)
from src.features.base import now_iso
from src.features.data_admin import DataManager
from src.features.meeting_note_handler import MeetingNoteHandler
from src.features.reminder_handler import ReminderHandler
from src.features.schedule_handler import ScheduleHandler
from src.features.toast_bridge import NoOpToastBridge
from src.features.todo_handler import TodoHandler
from src.generation.config import (
    ChatTurn,
    ConfidenceLevel,
    GenerationMode,
    GenerationRequest,
    RetrievalMetadata,
)
from src.generation.orchestrator import GenerationOrchestrator
from src.ingestion.embedder import EmbeddingGenerator
from src.ingestion.metadata_extractor import MetadataExtractor
from src.ingestion.pipeline import SessionIngestionPipeline
from src.models.app_config import AppConfig
from src.retrieval.reranker import Reranker
from src.retrieval.retrieval_agent import RetrievalAgent
from src.retrieval.router import RetrievalRouter
from src.retrieval.structured_search import StructuredTableSearch

logger = logging.getLogger(__name__)

HISTORY_TURNS = 6
_NO_MODEL_MESSAGE = "No AI model is set up yet. Choose one in Settings → Models to get started."

# project_logic.md §13: a session closes after a configurable idle timeout
# (~30-60 min). The effective value (AppConfig → env → default 45) is resolved
# per send() by src/backend/settings.py — see Settings → General (Step 3.4).


def _minutes_between(earlier_iso: str, later_iso: str) -> float:
    """Gap in minutes between two ISO-8601 timestamps. A parse failure returns
    0.0 — a session is never spuriously closed because a timestamp was odd."""
    try:
        a = datetime.fromisoformat(earlier_iso)
        b = datetime.fromisoformat(later_iso)
    except ValueError:
        return 0.0
    return (b - a).total_seconds() / 60.0


def _tier(confidence: float) -> int:
    """project_logic.md §3 tiers."""
    if confidence >= 0.85:
        return 1
    if confidence >= 0.70:
        return 2
    if confidence >= 0.50:
        return 3
    return 4


def _confidence_level(confidence: float) -> ConfidenceLevel:
    if confidence >= 0.85:
        return ConfidenceLevel.HIGH
    if confidence >= 0.70:
        return ConfidenceLevel.MEDIUM
    if confidence >= 0.50:
        return ConfidenceLevel.LOW
    return ConfidenceLevel.NONE


@dataclass
class ChatResult:
    session_id: str
    turn_index: int
    answer: str
    confidence: float
    tier: int
    action_type: str
    retrieve_needed: bool
    retrieval_route: str | None
    is_grounded: bool
    grounding_confidence: float
    citations: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: 3.1d — what a Tier-1 actionable message produced, if anything
    feature: dict | None = None  # {"kind", "id", "summary"}
    disambiguation: dict | None = None  # {"pending_action_id", "options"}
    conflict: dict | None = None  # schedule overlap — {"attempted", "conflicts_with"}
    dismissed_pending: bool = False  # a stale disambiguation popup was just discarded


@dataclass
class PendingAction:
    id: str
    action_type: AgenticActionType
    utterance: str
    ao_response: str
    created_at: str


_ACTIONABLE = frozenset(
    {
        AgenticActionType.REMINDER,
        AgenticActionType.TODO,
        AgenticActionType.MEETING_NOTE,
        AgenticActionType.SCHEDULE,
        AgenticActionType.SUMMARY_REQUEST,
    }
)

_NEIGHBOUR = {
    AgenticActionType.REMINDER: AgenticActionType.TODO,
    AgenticActionType.TODO: AgenticActionType.REMINDER,
    AgenticActionType.SCHEDULE: AgenticActionType.REMINDER,
    AgenticActionType.MEETING_NOTE: AgenticActionType.SUMMARY_REQUEST,
    AgenticActionType.SUMMARY_REQUEST: AgenticActionType.RETRIEVAL_QUERY,
}

_CLARIFICATIONS: dict[AgenticActionType, str] = {
    AgenticActionType.REMINDER: (
        'It sounds like you want a reminder — try "Remind me to call John on Tuesday at 2pm".'
    ),
    AgenticActionType.TODO: (
        'Want me to add that as a todo? Try "Add a todo: draft the Q3 report, high priority".'
    ),
    AgenticActionType.SCHEDULE: (
        'Sounds like a schedule entry — try "Schedule a review with Sam 3-4pm on Thursday".'
    ),
    AgenticActionType.MEETING_NOTE: (
        "If that's a meeting to capture, paste the notes or transcript and I'll pull out the key points."
    ),
    AgenticActionType.SUMMARY_REQUEST: (
        'Want a summary? Try "Summarize my day" or "Summarize this week".'
    ),
}


_VERBS = {
    AgenticActionType.REMINDER: "set a reminder",
    AgenticActionType.TODO: "add a todo",
    AgenticActionType.SCHEDULE: "put that on your schedule",
    AgenticActionType.MEETING_NOTE: "capture that as a meeting note",
    AgenticActionType.SUMMARY_REQUEST: "pull together a summary",
}


def _verb(action_type: AgenticActionType) -> str:
    return _VERBS.get(action_type, "do that")


def _disambig_options(primary: AgenticActionType) -> list[str]:
    opts = [primary.value]
    neigh = _NEIGHBOUR.get(primary)
    if neigh is not None:
        opts.append(neigh.value)
    opts.append(AgenticActionType.CONVERSATION.value)
    return opts


class SessionWorker(threading.Thread):
    def __init__(
        self,
        db_path: str,
        key: str | None = None,
        app_config_path: str | None = None,
        *,
        history_turns: int = HISTORY_TURNS,
        idle_minutes: float | None = None,
        on_ready: Callable[[ReconciliationResult], None] | None = None,
        agent: RetrievalAgent | None = None,
        router: RetrievalRouter | None = None,
        orchestrator: GenerationOrchestrator | None = None,
        pipeline: SessionIngestionPipeline | None = None,
        slot_extractor: SlotExtractor | None = None,
    ) -> None:
        super().__init__(daemon=True, name="companion-session-worker")
        self._db_path = db_path
        self._key = key
        self._app_config_path = app_config_path
        self._history_turns = history_turns
        # None → resolve the effective value live on every send() (so a
        # settings.update takes effect with no restart); a test can pin it.
        self._idle_minutes_override = idle_minutes
        self._on_ready = on_ready
        # Injection seams — tests pass fakes so the worker never loads the
        # all-MiniLM / cross-encoder models. When all four are injected the
        # per-model rebuild is skipped. Mirrors bootstrap_pipeline()'s style.
        self._inj_agent = agent
        self._inj_router = router
        self._inj_orchestrator = orchestrator
        self._inj_pipeline = pipeline
        self._inj_slot_extractor = slot_extractor
        self._injected = all(x is not None for x in (agent, router, orchestrator, pipeline))

        self._queue: queue.Queue[tuple[Callable[[], Any], Future] | None] = queue.Queue()
        self._ready = threading.Event()
        self._accepting = True
        self._shutting_down = False
        self._fatal: str | None = None

        # in-memory session state — single thread, no locks
        self._session_id: str | None = None
        self._turn_count = 0
        self._last_activity = now_iso()
        self._pending_action: PendingAction | None = None

        # built in run()'s prologue
        self._conn: Any = None
        self._repo: SessionRepository | None = None
        self._store: SQLiteVectorStore | None = None
        self._embedder: EmbeddingGenerator | None = None
        self._reranker: Reranker | None = None
        self._structured: StructuredTableSearch | None = None
        self._metrics: MetricsStore | None = None
        self._router: RetrievalRouter | None = None
        self._bound_model: str | None = None
        self._agent: RetrievalAgent | None = None
        self._orchestrator: GenerationOrchestrator | None = None
        self._pipeline: SessionIngestionPipeline | None = None
        self._slot_extractor: SlotExtractor | None = None
        self._ingest_pending = False
        #: captured in _build() (worker thread), read only by reconciliation()
        #: (also worker thread) — no lock. Never cleared until the next launch.
        self._reconciliation = ReconciliationResult()

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        try:
            self._build()
        except Exception as exc:  # noqa: BLE001 — a broken worker must still answer, with an error
            logger.exception("session worker failed to warm up")
            self._fatal = f"{type(exc).__name__}: {exc}"
        finally:
            self._ready.set()

        if self._fatal is None and self._on_ready is not None:
            try:
                self._on_ready(self._reconciliation)
            except Exception:  # noqa: BLE001
                logger.exception("session worker on_ready callback failed")

        while True:
            item = self._queue.get()
            if item is None:
                break
            fn, fut = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn())
            except BaseException as exc:  # noqa: BLE001 — propagate to the caller's Future
                fut.set_exception(exc)

        self._teardown()

    def wait_ready(self, timeout: float | None = None) -> bool:
        return self._ready.wait(timeout)

    def submit(self, fn: Callable[[], Any]) -> Future:
        """Run ``fn`` on the worker thread; returns a Future. The dispatcher
        uses this to hop a chat/health handler onto this thread."""
        fut: Future = Future()
        if not self._accepting:
            fut.set_exception(MethodError("unavailable", "the backend is shutting down"))
            return fut
        self._queue.put((fn, fut))
        return fut

    def stop(self, timeout: float = 15.0) -> bool:
        """Stop accepting, drain queued follow-ups (a pending re-ingest), close
        connections. Returns ``True`` if the thread exited within ``timeout``."""
        self._accepting = False
        self._shutting_down = True
        self._queue.put(None)
        if self.is_alive():
            self.join(timeout)
        if self.is_alive():
            logger.warning("session worker did not stop within %.1fs", timeout)
            return False
        return True

    # ------------------------------------------------------------------
    # Build / teardown (worker thread only)
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._conn = open_session_db(self._db_path, self._key)
        self._repo = SessionRepository(connection=self._conn)

        # Close any session a previous run left open (crash / kill with no
        # app.shutdown). project_logic.md §13.
        closed = self._repo.finalize_dangling_sessions("app_shutdown")
        if closed:
            logger.info("finalized %d session(s) left open by a previous run", closed)

        # On-launch reminder reconciliation (project_logic.md §9 steps 7-8) —
        # two independent lists, surfaced via on_ready + reminders.reconciliation.
        self._reconciliation = ReminderHandler(connection=self._conn).reconcile_on_launch(now_iso())

        self._store = SQLiteVectorStore(db_path=self._db_path, key=self._key)
        self._structured = StructuredTableSearch(connection=self._conn)
        self._metrics = MetricsStore(db_path=str(data_dir() / "metrics.db"))

        if self._inj_router is not None:
            self._router = self._inj_router
        else:
            self._embedder = EmbeddingGenerator(enable_logging=False)
            self._reranker = Reranker()
            self._router = RetrievalRouter(
                self._store, self._structured, reranker=self._reranker, embedder=self._embedder
            )

        self._rebuild_model_bundle(AppConfig.load(self._app_config_path).active_model)

    def _rebuild_model_bundle(self, active_model: str | None) -> None:
        """(Re)build only the model-bound objects — the router / reranker /
        embedder / store are model-agnostic and stay put. A no-op when the
        bundle is fully injected (tests)."""
        self._slot_extractor = self._inj_slot_extractor or SlotExtractor(model=active_model)
        if self._injected:
            self._agent = self._inj_agent
            self._orchestrator = self._inj_orchestrator
            self._pipeline = self._inj_pipeline
            self._bound_model = active_model
            return
        self._agent = self._inj_agent or RetrievalAgent(model=active_model)
        self._orchestrator = self._inj_orchestrator or GenerationOrchestrator(
            model_name=active_model
        )
        if self._inj_pipeline is not None:
            self._pipeline = self._inj_pipeline
        else:
            assert self._store is not None
            self._pipeline = SessionIngestionPipeline(
                self._store,
                extractor=MetadataExtractor(model=active_model),
                embedder=self._embedder,
            )
        self._bound_model = active_model

    def _teardown(self) -> None:
        for closer in (
            getattr(self._store, "close", None),
            getattr(self._conn, "close", None),
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001
                logger.exception("session worker teardown step failed")

    def _raise_if_unavailable(self) -> None:
        if self._fatal is not None:
            raise MethodError("unavailable", f"chat is not available: {self._fatal}")

    # ------------------------------------------------------------------
    # Tasks (worker thread only — called directly by a handler already hopped here)
    # ------------------------------------------------------------------

    def health(self) -> IntegrityResult:
        self._raise_if_unavailable()
        assert self._conn is not None
        return check_quick(self._conn)

    def reconciliation(self) -> ReconciliationResult:
        """The overdue / pending-acknowledgment reminder lists captured at
        warm-up (project_logic.md §9). Never cleared — same data every call."""
        self._raise_if_unavailable()
        return self._reconciliation

    def new_conversation(self) -> str:
        self._raise_if_unavailable()
        assert self._repo is not None
        self._pending_action = None
        if self._session_id is not None:
            self._close_session(self._session_id, "explicit")
        self._session_id = self._repo.create_session()
        self._turn_count = 0
        self._last_activity = now_iso()
        return self._session_id

    def _close_session(self, session_id: str, reason: str) -> None:
        """Finalize a session and re-embed its final transcript.

        The re-ingest is ENQUEUED, not synchronous: ``_reingest`` reads
        ``messages`` (all rows persisted, ``ended_at``-independent), so a
        closed session re-embeds correctly from the queue — and a synchronous
        drain here would stall the ``send()`` that triggered the close (the
        user's *returning* message) by ~40-90s for no correctness gain, since
        every prior message already triggered a completed re-ingest. Shutdown
        runs it synchronously so nothing is dropped.
        """
        assert session_id and self._repo is not None
        self._pending_action = None  # a disambiguation never survives a session boundary
        self._repo.finalize_session(session_id, reason)
        if self._shutting_down:
            self._reingest(session_id)
        else:
            self._enqueue_followup(lambda: self._reingest(session_id))

    def history(self, session_id: str | None) -> tuple[str, list[dict[str, object]]]:
        self._raise_if_unavailable()
        assert self._repo is not None
        sid = session_id or self._session_id
        if sid is None:
            sid = self._repo.create_session()
            self._session_id = sid
        return sid, self._repo.history(sid)

    def send(self, text: str) -> ChatResult:
        self._raise_if_unavailable()
        assert self._repo is not None and self._router is not None
        assert self._agent is not None and self._orchestrator is not None

        active = AppConfig.load(self._app_config_path).active_model
        if not active:
            raise MethodError("no_model_active", _NO_MODEL_MESSAGE)
        if active != self._bound_model:
            self._rebuild_model_bundle(active)

        # Idle auto-close (project_logic.md §13) — checked BEFORE the reuse/
        # create decision so this message starts a fresh session. The effective
        # timeout is re-read per call (Settings → General, Phase 3 Step 3.4).
        now = now_iso()
        idle_limit = (
            self._idle_minutes_override
            if self._idle_minutes_override is not None
            else backend_settings.effective_idle_minutes(self._app_config_path)
        )
        if self._session_id is not None and _minutes_between(self._last_activity, now) > idle_limit:
            self._close_session(self._session_id, "idle_timeout")
            self._session_id = None
            self._turn_count = 0

        session_id = self._session_id or self._repo.create_session()
        self._session_id = session_id

        # Any new chat.send discards a pending Tier-2 disambiguation (roadmap:
        # "Dismissible — falls through to Tier 4"). The dedicated
        # chat.confirm_action method is the only way to resolve one.
        dismissed_pending = self._pending_action is not None
        self._pending_action = None

        history = self._repo.recent_turns(session_id, self._history_turns)  # prior turns only
        user_idx = self._repo.append_message(session_id, "user", text)

        ao = self._agent.reason(text, history)
        tier = _tier(ao.confidence)
        feature: dict | None = None
        conflict: dict | None = None
        disambiguation: dict | None = None
        warnings: list[str] = []
        citations: list[dict[str, str]] = []
        is_grounded = False
        grounding_confidence = 0.0
        retrieval_ms = 0.0
        chunk_count = 0

        if ao.action_type in _ACTIONABLE and tier == 1:
            # Tier 1 — auto-execute. No retrieval / generation.
            outcome = self._execute_action(ao.action_type, text)
            answer = outcome.answer
            feature = outcome.feature
            conflict = outcome.conflict
        elif ao.action_type in _ACTIONABLE and tier == 2:
            # Tier 2 — disambiguation popup; the frontend resolves via chat.confirm_action.
            pa_id = f"pa-{uuid.uuid4().hex[:12]}"
            self._pending_action = PendingAction(
                pa_id, ao.action_type, text, ao.response, now_iso()
            )
            answer = f"Did you want me to {_verb(ao.action_type)}, or is this something else?"
            disambiguation = {
                "pending_action_id": pa_id,
                "options": _disambig_options(ao.action_type),
            }
        elif ao.action_type in _ACTIONABLE and tier == 3:
            # Tier 3 — clarification prompt, conversation continues.
            answer = _CLARIFICATIONS.get(ao.action_type, "Could you rephrase that?")
        elif ao.retrieve_needed:
            t0 = time.time()
            chunks: list[SessionRetrievedChunk] = self._router.route(ao, text)
            retrieval_ms = (time.time() - t0) * 1000
            chunk_count = len(chunks)
            request = GenerationRequest(
                request_id=str(uuid.uuid4()),
                query=text,
                mode=GenerationMode.CONTEXT_AWARE,
                chunks=chunks,
                retrieval_metadata=RetrievalMetadata(
                    confidence_score=ao.confidence,
                    confidence_level=_confidence_level(ao.confidence),
                    retrieval_time_ms=retrieval_ms,
                    total_chunks_retrieved=chunk_count,
                    session_id=session_id,
                    retrieval_method=ao.retrieval_route.value,
                ),
                conversation_history=[ChatTurn(role=m.role, content=m.content) for m in history],
            )
            resp = self._orchestrator.generate(request)
            if resp.error_type or not resp.answer:
                warnings.append(f"generation unavailable ({resp.error_type or 'empty'})")
                answer = ao.response
            else:
                answer = resp.answer
                is_grounded = resp.is_grounded
                grounding_confidence = resp.grounding_confidence
                citations = [
                    {
                        "chunk_id": c.chunk_id,
                        "session_id": c.session_id,
                        "approximate_timestamp": c.approximate_timestamp,
                    }
                    for c in resp.citations
                ]
            warnings.extend(resp.warnings or [])
        else:
            # project_logic.md §4: retrieve_needed = false -> the agentic call's
            # own `response` goes straight back. Nothing to ground against, so
            # PostProcessor / GroundingValidator are intentionally skipped.
            answer = ao.response

        self._repo.append_message(session_id, "assistant", answer)
        self._turn_count += 2

        self._record_metrics(
            ao=ao,
            answer=answer,
            retrieval_ms=retrieval_ms,
            chunk_count=chunk_count,
            is_grounded=is_grounded,
            citation_count=len(citations),
        )

        if self._shutting_down:
            self._reingest(session_id)
        else:
            self._enqueue_followup(lambda: self._reingest(session_id))

        # Stamped at the END of a successful send — the true inter-message gap,
        # not shrunk by however long this call's inference took.
        self._last_activity = now_iso()

        return ChatResult(
            session_id=session_id,
            turn_index=user_idx,
            answer=answer,
            confidence=ao.confidence,
            tier=tier,
            action_type=ao.action_type.value,
            retrieve_needed=ao.retrieve_needed,
            retrieval_route=ao.retrieval_route.value if ao.retrieve_needed else None,
            is_grounded=is_grounded,
            grounding_confidence=grounding_confidence,
            citations=citations,
            warnings=warnings,
            feature=feature,
            disambiguation=disambiguation,
            conflict=conflict,
            dismissed_pending=dismissed_pending,
        )

    def confirm_action(self, pending_action_id: str, choice: str) -> ChatResult:
        """Resolve a Tier-2 disambiguation. ``choice`` is an action-type value
        or ``"conversation"`` (dismiss)."""
        self._raise_if_unavailable()
        assert self._repo is not None
        pa = self._pending_action
        if pa is None or pa.id != pending_action_id:
            raise MethodError(
                "no_pending_action", "that confirmation has expired — send your message again"
            )
        self._pending_action = None
        session_id = self._session_id or self._repo.create_session()
        self._session_id = session_id

        feature: dict | None = None
        conflict: dict | None = None
        if choice in (AgenticActionType.CONVERSATION.value, AgenticActionType.NONE.value):
            answer = pa.ao_response or "Okay, never mind."
            action_type = AgenticActionType.CONVERSATION.value
            tier = 4
        else:
            try:
                chosen = AgenticActionType(choice)
            except ValueError:
                chosen = pa.action_type
            outcome = self._execute_action(chosen, pa.utterance)
            answer, feature, conflict = outcome.answer, outcome.feature, outcome.conflict
            action_type = chosen.value
            tier = 1

        turn_index = self._repo.append_message(session_id, "assistant", answer)
        self._turn_count += 1
        self._record_metrics(
            ao=None,
            answer=answer,
            retrieval_ms=0.0,
            chunk_count=0,
            is_grounded=False,
            citation_count=0,
        )
        if self._shutting_down:
            self._reingest(session_id)
        else:
            self._enqueue_followup(lambda: self._reingest(session_id))
        self._last_activity = now_iso()

        return ChatResult(
            session_id=session_id,
            turn_index=turn_index,
            answer=answer,
            confidence=1.0 if tier == 1 else 0.0,
            tier=tier,
            action_type=action_type,
            retrieve_needed=False,
            retrieval_route=None,
            is_grounded=False,
            grounding_confidence=0.0,
            feature=feature,
            conflict=conflict,
        )

    def _execute_action(
        self, action_type: AgenticActionType, utterance: str
    ) -> action_dispatch.DispatchOutcome:
        if action_type == AgenticActionType.MEETING_NOTE:
            slots: dict[str, object] = {}  # capture_meeting_note self-extracts
        else:
            assert self._slot_extractor is not None
            slots = self._slot_extractor.extract(action_type, utterance, now_iso())
        return action_dispatch.dispatch(
            action_type,
            slots,
            utterance,
            conn=self._conn,
            session_id=self._session_id,
            bridge=NoOpToastBridge(),
            model=self._bound_model,
        )

    # ------------------------------------------------------------------
    # Feature-view CRUD pass-throughs (Step 3.3) — worker thread only.
    # Each builds a handler on self._conn (the only long-lived session
    # connection) and returns the dataclass(es); handlers.py maps to wire.
    # ------------------------------------------------------------------

    def _reminders(self) -> ReminderHandler:
        # NoOpToastBridge: the real WinRT bridge is a later step, so these
        # create/reschedule paths do not (yet) register OS toasts — same as
        # action_dispatch today.
        return ReminderHandler(connection=self._conn, bridge=NoOpToastBridge())

    def _prune_reconciliation(self, reminder_id: str) -> None:
        """Drop a reminder id from the cached on-launch reconciliation lists
        (Q3) — keeps the frozen snapshot honest for a later re-fetch. Worker
        thread only, no lock (same access pattern as the cache itself)."""
        self._reconciliation = ReconciliationResult(
            overdue=[r for r in self._reconciliation.overdue if r.id != reminder_id],
            pending_acknowledgment=[
                r for r in self._reconciliation.pending_acknowledgment if r.id != reminder_id
            ],
        )

    def list_reminders(self) -> list[Reminder]:
        self._raise_if_unavailable()
        return self._reminders().get_reminders(active_only=True)

    def complete_reminder(self, reminder_id: str) -> Reminder:
        self._raise_if_unavailable()
        r = self._reminders().complete_reminder(reminder_id)
        self._prune_reconciliation(reminder_id)
        return r

    def dismiss_reminder(self, reminder_id: str) -> Reminder:
        self._raise_if_unavailable()
        r = self._reminders().dismiss_reminder(reminder_id)
        self._prune_reconciliation(reminder_id)
        return r

    def reschedule_reminder(self, reminder_id: str, scheduled_time: str) -> Reminder:
        self._raise_if_unavailable()
        r = self._reminders().reschedule_reminder(reminder_id, scheduled_time)
        self._prune_reconciliation(reminder_id)
        return r

    def update_reminder(self, reminder_id: str, **fields: object) -> Reminder:
        self._raise_if_unavailable()
        return self._reminders().update_reminder(reminder_id, **fields)

    def delete_reminder(self, reminder_id: str) -> bool:
        self._raise_if_unavailable()
        deleted = self._reminders().delete_reminder(reminder_id)
        self._prune_reconciliation(reminder_id)
        return deleted

    def list_todos(self, *, limit: int = 500) -> list[Todo]:
        self._raise_if_unavailable()
        return TodoHandler(connection=self._conn).get_todos()[-limit:]

    def complete_todo(self, todo_id: str) -> Todo:
        self._raise_if_unavailable()
        return TodoHandler(connection=self._conn).complete_todo(todo_id)

    def update_todo(self, todo_id: str, **fields: object) -> Todo:
        self._raise_if_unavailable()
        return TodoHandler(connection=self._conn).update_todo(todo_id, **fields)

    def delete_todo(self, todo_id: str) -> bool:
        self._raise_if_unavailable()
        return TodoHandler(connection=self._conn).delete_todo(todo_id)

    def list_meeting_notes(self) -> list[MeetingNote]:
        self._raise_if_unavailable()
        return MeetingNoteHandler(connection=self._conn).get_meeting_notes()

    def get_meeting_note(self, note_id: str) -> MeetingNote | None:
        self._raise_if_unavailable()
        return MeetingNoteHandler(connection=self._conn).get_meeting_note(note_id)

    def capture_meeting_note(self, transcript: str) -> MeetingNote:
        self._raise_if_unavailable()
        return MeetingNoteHandler(
            connection=self._conn, model=self._bound_model
        ).capture_meeting_note(transcript, session_id=self._session_id)

    def delete_meeting_note(self, note_id: str) -> bool:
        self._raise_if_unavailable()
        return MeetingNoteHandler(connection=self._conn).delete_meeting_note(note_id)

    def schedule_day(self, date: str) -> list[ScheduleItem]:
        self._raise_if_unavailable()
        return ScheduleHandler(connection=self._conn).get_day_schedule(date)

    def schedule_week(self, start_date: str) -> list[tuple[str, list[ScheduleItem]]]:
        self._raise_if_unavailable()
        try:
            start = datetime.fromisoformat(start_date).date()
        except ValueError as exc:
            raise MethodError(
                "invalid_params", f"start_date is not a date: {start_date!r}"
            ) from exc
        dates = [(start + timedelta(days=i)).isoformat() for i in range(7)]
        items = ScheduleHandler(connection=self._conn).get_range_schedule(dates[0], dates[-1])
        by_date: dict[str, list[ScheduleItem]] = {d: [] for d in dates}
        for it in items:
            by_date.setdefault(it.start_time[:10], []).append(it)
        return [(d, by_date.get(d, [])) for d in dates]

    def create_schedule_item(
        self,
        title: str,
        start_time: str,
        end_time: str,
        *,
        location: str = "",
        notes: str = "",
        overwrite_ids: list[str] | None = None,
    ) -> ScheduleItem | ScheduleConflict:
        self._raise_if_unavailable()
        return ScheduleHandler(connection=self._conn).create_schedule_item(
            title,
            start_time,
            end_time,
            location=location,
            notes=notes,
            overwrite_ids=overwrite_ids,
        )

    def update_schedule_item(
        self, item_id: str, **fields: object
    ) -> ScheduleItem | ScheduleConflict:
        self._raise_if_unavailable()
        return ScheduleHandler(connection=self._conn).update_schedule_item(item_id, **fields)

    def delete_schedule_item(self, item_id: str) -> bool:
        self._raise_if_unavailable()
        return ScheduleHandler(connection=self._conn).delete_schedule_item(item_id)

    # ------------------------------------------------------------------
    # Data & Privacy pass-throughs (Step 3.4) — worker thread only, because
    # export reads and full-wipe writes the same session tables the chat spine
    # touches; serializing them here removes the race with a concurrent send().
    # ------------------------------------------------------------------

    def _data_manager(self) -> DataManager:
        return DataManager(
            connection=self._conn,
            bridge=NoOpToastBridge(),
            app_config=AppConfig.load(self._app_config_path),
        )

    def export_data(self, path: str) -> tuple[str, int]:
        """Write the JSON export to ``path``; returns ``(exported_at, bytes_written)``.
        ``DataManager.write_export`` stamps ``last_exported_at`` on success."""
        self._raise_if_unavailable()
        now = now_iso()
        written = self._data_manager().write_export(path, now=now)
        return now, written.stat().st_size

    def wipe_data(self) -> None:
        """Soft-delete every substantive row, then discard the now-meaningless
        in-memory session + vector state so nothing stale survives the wipe."""
        self._raise_if_unavailable()
        self._data_manager().full_wipe()

        self._session_id = None
        self._turn_count = 0
        self._pending_action = None
        self._reconciliation = ReconciliationResult(overdue=[], pending_acknowledgment=[])

        # The vector index still holds the (now soft-deleted) chunks in memory.
        # In a real run rebuild it + everything that referenced the old store;
        # a fully-injected test keeps its fakes.
        if self._inj_router is None:
            if self._store is not None:
                self._store.close()
            self._store = SQLiteVectorStore(db_path=self._db_path, key=self._key)
            assert self._reranker is not None and self._embedder is not None
            self._router = RetrievalRouter(
                self._store, self._structured, reranker=self._reranker, embedder=self._embedder
            )
            self._rebuild_model_bundle(self._bound_model)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _enqueue_followup(self, fn: Callable[[], Any]) -> None:
        fut: Future = Future()
        self._queue.put((fn, fut))

    def _reingest(self, session_id: str) -> None:
        """Re-embed the whole session. Guarded like SchedulerThread._tick — a
        failure logs and is dropped, never propagates."""
        self._ingest_pending = True
        try:
            assert self._repo is not None and self._pipeline is not None
            messages = self._repo.all_messages(session_id)
            if not messages:
                return
            started_at = self._repo.session_started_at(session_id) or now_iso()
            self._pipeline.ingest_session(session_id, messages, timestamp=started_at)
        except Exception:  # noqa: BLE001
            logger.exception("session worker: re-ingestion of %s failed", session_id)
        finally:
            self._ingest_pending = False

    def _record_metrics(
        self,
        *,
        ao: Any,
        answer: str,
        retrieval_ms: float,
        chunk_count: int,
        is_grounded: bool,
        citation_count: int,
    ) -> None:
        if self._metrics is None:
            return
        confidence = ao.confidence if ao is not None else 0.0
        route = ao.retrieval_route.value if (ao is not None and ao.retrieve_needed) else "none"
        try:
            self._metrics.record(
                PipelineCallMetrics(
                    request_id=str(uuid.uuid4()),
                    env="backend",
                    query="",  # project_logic §6.1 — no message content in metrics
                    retrieval_time_ms=retrieval_ms,
                    confidence_score=confidence,
                    confidence_level=_confidence_level(confidence).value,
                    retrieval_hit=chunk_count > 0,
                    candidates_retrieved=chunk_count,
                    is_grounded=is_grounded,
                    citations_count=citation_count,
                    model_name=self._bound_model,
                    retrieval_pipeline_name=route,
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("metrics record failed", exc_info=True)
