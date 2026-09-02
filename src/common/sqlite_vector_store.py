"""
SQLiteVectorStore (Production Roadmap Phase 1 Step 1.1).

The v1 concrete ``VectorStoreInterface``: brute-force cosine similarity over
an in-memory numpy matrix of every non-deleted ``session_chunks.embedding``,
loaded once at construction. At personal-use scale (thousands of sessions
over years) this stays well under the 200ms vector-search target on the
reference hardware — no native SQLite vector extension is needed in v1
(``audits/project_logic.md`` §5).

The SQLite connection is opened through ``db.connection.open_session_db`` so
it inherits WAL mode, the busy timeout, foreign-key enforcement, and the
(currently inert) SQLCipher ``key`` seam that Phase 2 Step 2.1 fills in.
"""

import json
import sqlite3
from datetime import UTC, datetime

import numpy as np

from db.connection import open_session_db
from src.common.types import (
    SessionChunkRecord,
    SessionRetrievedChunk,
    StoreStats,
    UpsertResult,
)
from src.common.vector_store import VectorStoreInterface

EMBEDDING_DIM = 384

_SELECT_COLUMNS = (
    "id, session_id, chunk_type, content, embedding, token_count, "
    "topics, action_types, entities, sentiment, message_roles, "
    "window_start_message_idx, window_end_message_idx, created_at, updated_at"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteVectorStore(VectorStoreInterface):
    def __init__(
        self,
        db_path: str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        key: str | None = None,
    ):
        """
        Open the store. Pass ``db_path`` for a real file-backed database
        (opened via ``open_session_db``), or inject a ``connection`` directly
        (tests use a shared in-memory / tmp connection). Exactly one of the
        two must be given.

        The ``0001`` migration must already have been applied — this class
        never runs migrations. A missing ``session_chunks`` table is a
        composition-root wiring error and raises immediately.
        """
        if (db_path is None) == (connection is None):
            raise ValueError("Pass exactly one of db_path or connection")

        self._conn = connection or open_session_db(db_path, key)  # type: ignore[arg-type]
        self._conn.row_factory = sqlite3.Row

        if not self._table_exists("session_chunks"):
            raise RuntimeError(
                "session_chunks table is missing — apply db/migrations/0001_initial_schema.sql "
                "(via db.migration_runner.MigrationRunner) before constructing SQLiteVectorStore."
            )

        # Parallel in-memory index. _matrix[i] is the embedding for _ids[i];
        # _rows[chunk_id] holds that row's decoded metadata.
        self._ids: list[str] = []
        self._id_pos: dict[str, int] = {}
        self._rows: dict[str, dict] = {}
        # session_id -> the session's primary chunk id. Maintained on load /
        # upsert / delete so parent_chunk_id derivation at query time is O(1),
        # never a scan or a per-result SELECT (the <200ms budget is the hot path).
        self._primary_by_session: dict[str, str] = {}
        self._matrix: np.ndarray = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        self._norms: np.ndarray = np.zeros((0,), dtype=np.float32)
        self._load()

    # ------------------------------------------------------------------
    # Construction-time load
    # ------------------------------------------------------------------

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def _load(self) -> None:
        rows = self._conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM session_chunks WHERE deleted_at IS NULL"
        ).fetchall()

        vectors: list[np.ndarray] = []
        for row in rows:
            chunk_id = row["id"]
            self._ids.append(chunk_id)
            self._id_pos[chunk_id] = len(self._ids) - 1
            self._rows[chunk_id] = self._decode_row(row)
            if row["chunk_type"] == "primary":
                self._primary_by_session[row["session_id"]] = chunk_id
            vectors.append(_blob_to_vector(row["embedding"]))

        if vectors:
            self._matrix = np.vstack(vectors).astype(np.float32)
        self._recompute_norms()

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict:
        return {
            "session_id": row["session_id"],
            "chunk_type": row["chunk_type"],
            "content": row["content"],
            "token_count": row["token_count"],
            "topics": json.loads(row["topics"] or "[]"),
            "action_types": json.loads(row["action_types"] or "[]"),
            "entities": json.loads(row["entities"] or "[]"),
            "sentiment": row["sentiment"] or "",
            "message_roles": json.loads(row["message_roles"] or "[]"),
            "window_start_message_idx": row["window_start_message_idx"],
            "window_end_message_idx": row["window_end_message_idx"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _recompute_norms(self) -> None:
        if len(self._ids):
            self._norms = np.linalg.norm(self._matrix, axis=1).astype(np.float32)
        else:
            self._norms = np.zeros((0,), dtype=np.float32)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def upsert(self, chunk: SessionChunkRecord) -> UpsertResult:
        vector = np.asarray(chunk.embedding, dtype=np.float32).reshape(-1)
        if vector.shape[0] != EMBEDDING_DIM:
            raise ValueError(
                f"embedding for {chunk.chunk_id} has {vector.shape[0]} dims, expected {EMBEDDING_DIM}"
            )

        existing = self._conn.execute(
            "SELECT created_at FROM session_chunks WHERE id = ?", (chunk.chunk_id,)
        ).fetchone()
        now = _now()
        created_at = existing["created_at"] if existing else now
        operation = "updated" if existing else "inserted"

        self._conn.execute(
            "INSERT INTO session_chunks ("
            "  id, session_id, chunk_type, window_start_message_idx, window_end_message_idx,"
            "  content, embedding, token_count, topics, action_types, entities, sentiment,"
            "  message_roles, created_at, updated_at, deleted_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, NULL) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  session_id=excluded.session_id, chunk_type=excluded.chunk_type,"
            "  window_start_message_idx=excluded.window_start_message_idx,"
            "  window_end_message_idx=excluded.window_end_message_idx,"
            "  content=excluded.content, embedding=excluded.embedding,"
            "  token_count=excluded.token_count, topics=excluded.topics,"
            "  action_types=excluded.action_types, entities=excluded.entities,"
            "  sentiment=excluded.sentiment, message_roles=excluded.message_roles,"
            "  updated_at=excluded.updated_at, deleted_at=NULL",
            (
                chunk.chunk_id,
                chunk.session_id,
                chunk.chunk_type,
                chunk.window_start_message_idx,
                chunk.window_end_message_idx,
                chunk.content,
                vector.tobytes(),
                int(chunk.token_count),
                json.dumps(list(chunk.topics)),
                json.dumps(list(chunk.action_types)),
                json.dumps(list(chunk.entities)),
                chunk.sentiment,
                json.dumps(list(chunk.message_roles)),
                created_at,
                now,
            ),
        )
        self._conn.commit()

        decoded = {
            "session_id": chunk.session_id,
            "chunk_type": chunk.chunk_type,
            "content": chunk.content,
            "token_count": int(chunk.token_count),
            "topics": list(chunk.topics),
            "action_types": list(chunk.action_types),
            "entities": list(chunk.entities),
            "sentiment": chunk.sentiment,
            "message_roles": list(chunk.message_roles),
            "window_start_message_idx": chunk.window_start_message_idx,
            "window_end_message_idx": chunk.window_end_message_idx,
            "created_at": created_at,
            "updated_at": now,
        }
        self._index_put(chunk.chunk_id, vector, decoded)

        return UpsertResult(
            chunk_id=chunk.chunk_id,
            operation=operation,
            total_chunks_in_store=len(self._ids),
        )

    def _index_put(self, chunk_id: str, vector: np.ndarray, decoded: dict) -> None:
        self._rows[chunk_id] = decoded
        if decoded["chunk_type"] == "primary":
            self._primary_by_session[decoded["session_id"]] = chunk_id
        if chunk_id in self._id_pos:
            self._matrix[self._id_pos[chunk_id]] = vector
        else:
            self._ids.append(chunk_id)
            self._id_pos[chunk_id] = len(self._ids) - 1
            self._matrix = (
                vector.reshape(1, -1)
                if self._matrix.shape[0] == 0
                else np.vstack([self._matrix, vector])
            )
        self._recompute_norms()

    def delete(self, chunk_id: str) -> bool:
        cur = self._conn.execute(
            "UPDATE session_chunks SET deleted_at = ?, updated_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (_now(), _now(), chunk_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            return False

        pos = self._id_pos.pop(chunk_id, None)
        removed = self._rows.pop(chunk_id, None)
        if removed is not None and self._primary_by_session.get(removed["session_id"]) == chunk_id:
            self._primary_by_session.pop(removed["session_id"], None)
        if pos is not None:
            self._ids.pop(pos)
            self._matrix = np.delete(self._matrix, pos, axis=0)
            self._id_pos = {cid: i for i, cid in enumerate(self._ids)}
            self._recompute_norms()
        return True

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def query(
        self,
        embedding: np.ndarray,
        top_k: int,
        filters: dict | None = None,
    ) -> list[SessionRetrievedChunk]:
        if len(self._ids) == 0 or top_k <= 0:
            return []

        q = np.asarray(embedding, dtype=np.float32).reshape(-1)
        q_norm = float(np.linalg.norm(q))
        if q_norm == 0.0:
            return []

        sims = (self._matrix @ q) / (self._norms * q_norm + 1e-12)

        candidate_positions = self._apply_filters(filters)
        if candidate_positions is not None:
            if len(candidate_positions) == 0:
                return []
            order = candidate_positions[np.argsort(-sims[candidate_positions])]
        else:
            order = np.argsort(-sims)

        order = order[:top_k]
        return [self._to_retrieved_chunk(self._ids[pos], float(sims[pos])) for pos in order]

    def _apply_filters(self, filters: dict | None) -> np.ndarray | None:
        if not filters:
            return None

        mask = np.ones(len(self._ids), dtype=bool)

        session_id = filters.get("session_id")
        if session_id is not None:
            mask &= np.array([self._rows[cid]["session_id"] == session_id for cid in self._ids])

        chunk_type = filters.get("chunk_type")
        if chunk_type is not None:
            allowed = {chunk_type} if isinstance(chunk_type, str) else set(chunk_type)
            mask &= np.array([self._rows[cid]["chunk_type"] in allowed for cid in self._ids])

        exclude = filters.get("exclude_chunk_ids")
        if exclude:
            exclude_set = set(exclude)
            mask &= np.array([cid not in exclude_set for cid in self._ids])

        return np.nonzero(mask)[0]

    def _to_retrieved_chunk(self, chunk_id: str, score: float) -> SessionRetrievedChunk:
        row = self._rows[chunk_id]
        timestamp = row["updated_at"] or row["created_at"] or ""
        topics = row["topics"]
        primary_topic = topics[0] if topics else "—"
        parent_chunk_id = (
            None if row["chunk_type"] == "primary" else self._primary_id_for(row["session_id"])
        )
        return SessionRetrievedChunk(
            chunk_id=chunk_id,
            session_id=row["session_id"],
            content=row["content"],
            raw_content=row["content"],
            topics=list(topics),
            action_types=list(row["action_types"]),
            entities=list(row["entities"]),
            sentiment=row["sentiment"],
            timestamp=timestamp,
            message_roles=list(row["message_roles"]),
            chunk_type=row["chunk_type"],
            parent_chunk_id=parent_chunk_id,
            token_count=row["token_count"],
            score=score,
            semantic_score=score,
            keyword_score=None,
            metadata_score=None,
            source_prefix=f"[Session: {row['session_id']} | {timestamp} | Topic: {primary_topic}]",
            metadata={
                "window_start_message_idx": row["window_start_message_idx"],
                "window_end_message_idx": row["window_end_message_idx"],
            },
        )

    def _primary_id_for(self, session_id: str) -> str | None:
        return self._primary_by_session.get(session_id)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_stats(self) -> StoreStats:
        primary = sum(1 for cid in self._ids if self._rows[cid]["chunk_type"] == "primary")
        return StoreStats(
            total_chunks=len(self._ids),
            total_sessions=len({self._rows[cid]["session_id"] for cid in self._ids}),
            embedding_dim=EMBEDDING_DIM,
            primary_chunks=primary,
            sub_chunks=len(self._ids) - primary,
        )


def _blob_to_vector(blob: bytes) -> np.ndarray:
    vector = np.frombuffer(blob, dtype=np.float32)
    if vector.shape[0] != EMBEDDING_DIM:
        raise ValueError(
            f"stored embedding has {vector.shape[0]} float32 values, expected {EMBEDDING_DIM}"
        )
    return vector.copy()
