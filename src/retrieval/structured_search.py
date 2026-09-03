"""
Structured-table search (Phase 1 Step 1.3b).

The ``structured`` retrieval route: SQLite FTS5 keyword/phrase match over the
four structured-record tables — ``reminders``, ``todos``, ``meeting_notes``,
``schedule_items`` — returning results as ``SessionRetrievedChunk`` instances
with ``chunk_type = "structured_record"``.

FTS5 has no concept of ``deleted_at``; every query JOINs the FTS virtual
table back to its base table and filters ``base.deleted_at IS NULL``
(docs/schema_review.md §2). This is keyword match, not semantic search —
paraphrases that share no keywords with the stored text will miss
(project_logic.md §5, "Known limitation").
"""

import re
import sqlite3
from dataclasses import dataclass

from db.connection import open_session_db
from src.common.types import SessionRetrievedChunk


@dataclass(frozen=True)
class _TableSpec:
    base: str
    fts: str
    # Columns joined into the returned chunk's `content`, best first.
    text_columns: tuple[str, ...]
    has_session_id: bool


_TABLES: dict[str, _TableSpec] = {
    "reminders": _TableSpec("reminders", "reminders_fts", ("title", "notes"), True),
    "todos": _TableSpec("todos", "todos_fts", ("title", "notes"), True),
    "meeting_notes": _TableSpec(
        "meeting_notes", "meeting_notes_fts", ("searchable_text", "raw_transcript"), True
    ),
    "schedule_items": _TableSpec(
        "schedule_items", "schedule_items_fts", ("title", "notes", "location"), False
    ),
}

ALL_TABLES: tuple[str, ...] = tuple(_TABLES)

_TOKEN_RE = re.compile(r"\w+")


def _fts_match_expr(query: str) -> str | None:
    """
    Turn a free-text query into a recall-biased FTS5 MATCH expression:
    each word-token quoted (so FTS5 never interprets punctuation as an
    operator) and OR-joined. Returns ``None`` for an all-punctuation /
    empty query.
    """
    tokens = _TOKEN_RE.findall(query.lower())
    if not tokens:
        return None
    return " OR ".join(f'"{tok}"' for tok in tokens)


class StructuredTableSearch:
    """FTS5 search over the structured-record tables."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        connection: sqlite3.Connection | None = None,
        key: str | None = None,
    ):
        """
        Pass ``db_path`` for a file-backed database (opened via
        ``open_session_db``) or inject a ``connection`` directly (tests use a
        tmp-DB connection). Exactly one of the two must be given. The ``0001``
        migration must already have been applied.
        """
        if (db_path is None) == (connection is None):
            raise ValueError("Pass exactly one of db_path or connection")

        self._conn = connection or open_session_db(db_path, key)  # type: ignore[arg-type]
        self._conn.row_factory = sqlite3.Row

    def search(
        self,
        query: str,
        *,
        tables: list[str] | None = None,
        top_k: int = 10,
    ) -> list[SessionRetrievedChunk]:
        """
        Return up to ``top_k`` structured records matching ``query``, best
        first (BM25 rank), across the requested ``tables`` (default: all four).
        """
        match_expr = _fts_match_expr(query)
        if match_expr is None or top_k <= 0:
            return []

        requested = tables or list(ALL_TABLES)
        results: list[SessionRetrievedChunk] = []

        for name in requested:
            spec = _TABLES.get(name)
            if spec is None:
                raise ValueError(f"unknown structured table: {name!r}")
            results.extend(self._search_table(spec, match_expr, top_k))

        results.sort(key=lambda c: c.score, reverse=True)
        return results[:top_k]

    def _search_table(
        self, spec: _TableSpec, match_expr: str, top_k: int
    ) -> list[SessionRetrievedChunk]:
        session_col = "b.session_id" if spec.has_session_id else "NULL AS session_id"
        text_cols = ", ".join(f"b.{c}" for c in spec.text_columns)
        sql = (
            f"SELECT b.id, {session_col}, {text_cols}, "
            f"b.created_at, b.updated_at, bm25({spec.fts}) AS _rank "
            f"FROM {spec.base} b JOIN {spec.fts} ON {spec.fts}.rowid = b.rowid "
            f"WHERE {spec.fts} MATCH ? AND b.deleted_at IS NULL "
            f"ORDER BY _rank LIMIT ?"
        )
        rows = self._conn.execute(sql, (match_expr, top_k)).fetchall()
        return [self._row_to_chunk(spec, row) for row in rows]

    @staticmethod
    def _row_to_chunk(spec: _TableSpec, row: sqlite3.Row) -> SessionRetrievedChunk:
        parts = [str(row[c]).strip() for c in spec.text_columns if row[c]]
        content = " — ".join(p for p in parts if p) or "(no text)"
        timestamp = row["updated_at"] or row["created_at"] or ""
        # bm25() returns a score where lower (more negative) is a better
        # match; fold it into a monotonic (0, 1] similarity.
        rank = float(row["_rank"])
        score = 1.0 / (1.0 + abs(rank))
        return SessionRetrievedChunk(
            chunk_id=f"{spec.base}:{row['id']}",
            session_id=row["session_id"] or "",
            content=content,
            raw_content=content,
            timestamp=timestamp,
            chunk_type="structured_record",
            parent_chunk_id=None,
            token_count=0,
            score=score,
            semantic_score=None,
            keyword_score=score,
            metadata_score=None,
            source_prefix=f"[{spec.base} · approx. {timestamp}]",
            metadata={"table": spec.base, "record_id": row["id"]},
        )
