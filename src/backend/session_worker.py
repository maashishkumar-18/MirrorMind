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
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from db.connection import open_session_db
from db.health import IntegrityResult, check_quick
from observability.metrics_store import MetricsStore, PipelineCallMetrics
from src.backend.paths import data_dir
from src.backend.session_repository import SessionRepository
from src.backend.wire import MethodError
from src.common.sqlite_vector_store import SQLiteVectorStore
from src.common.types import ReconciliationResult, SessionRetrievedChunk
from src.features.base import now_iso
from src.features.reminder_handler import ReminderHandler
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
# (~30-60 min). Env-overridable like HISTORY_TURNS; no YAML — the worker is not
# a "feature" and §13 only needs the value externalized.
_DEFAULT_IDLE_MINUTES = 45.0


def _idle_minutes() -> float:
    raw = os.getenv("RAGPIPE_SESSION_IDLE_MINUTES")
    if raw is None:
        return _DEFAULT_IDLE_MINUTES
    try:
        return float(raw)
    except ValueError:
        logger.warning("RAGPIPE_SESSION_IDLE_MINUTES=%r is not a number; using default", raw)
        return _DEFAULT_IDLE_MINUTES


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
    ) -> None:
        super().__init__(daemon=True, name="companion-session-worker")
        self._db_path = db_path
        self._key = key
        self._app_config_path = app_config_path
        self._history_turns = history_turns
        self._idle_minutes = idle_minutes if idle_minutes is not None else _idle_minutes()
        self._on_ready = on_ready
        # Injection seams — tests pass fakes so the worker never loads the
        # all-MiniLM / cross-encoder models. When all four are injected the
        # per-model rebuild is skipped. Mirrors bootstrap_pipeline()'s style.
        self._inj_agent = agent
        self._inj_router = router
        self._inj_orchestrator = orchestrator
        self._inj_pipeline = pipeline
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
        # create decision so this message starts a fresh session.
        now = now_iso()
        if (
            self._session_id is not None
            and _minutes_between(self._last_activity, now) > self._idle_minutes
        ):
            self._close_session(self._session_id, "idle_timeout")
            self._session_id = None
            self._turn_count = 0

        session_id = self._session_id or self._repo.create_session()
        self._session_id = session_id

        history = self._repo.recent_turns(session_id, self._history_turns)  # prior turns only
        user_idx = self._repo.append_message(session_id, "user", text)

        ao = self._agent.reason(text, history)
        warnings: list[str] = []
        citations: list[dict[str, str]] = []
        is_grounded = False
        grounding_confidence = 0.0
        retrieval_ms = 0.0
        chunk_count = 0

        if ao.retrieve_needed:
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
            tier=_tier(ao.confidence),
            action_type=ao.action_type.value,
            retrieve_needed=ao.retrieve_needed,
            retrieval_route=ao.retrieval_route.value if ao.retrieve_needed else None,
            is_grounded=is_grounded,
            grounding_confidence=grounding_confidence,
            citations=citations,
            warnings=warnings,
        )

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
        try:
            self._metrics.record(
                PipelineCallMetrics(
                    request_id=str(uuid.uuid4()),
                    env="backend",
                    query="",  # project_logic §6.1 — no message content in metrics
                    retrieval_time_ms=retrieval_ms,
                    confidence_score=ao.confidence,
                    confidence_level=_confidence_level(ao.confidence).value,
                    retrieval_hit=chunk_count > 0,
                    candidates_retrieved=chunk_count,
                    is_grounded=is_grounded,
                    citations_count=citation_count,
                    model_name=self._bound_model,
                    retrieval_pipeline_name=(
                        ao.retrieval_route.value if ao.retrieve_needed else "none"
                    ),
                )
            )
        except Exception:  # noqa: BLE001
            logger.debug("metrics record failed", exc_info=True)
