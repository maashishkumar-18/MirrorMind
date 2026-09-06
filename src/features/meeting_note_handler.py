"""
MeetingNoteHandler (Phase 1 Step 1.5a).

``capture_meeting_note(raw_transcript)`` runs one local-LLM extraction pass
(``simple_generate``) to pull ``attendees`` / ``topics`` / ``decisions`` /
``action_items`` / ``follow_ups`` out of a raw transcript, then persists a
``meeting_notes`` row. No conversational response is generated during capture
(spec requirement).

If extraction fails after retries, or comes back with neither ``decisions``
nor ``action_items``, the row is stored with ``needs_review = 1`` so the
frontend can flag it. The JSON-recovery idiom mirrors
``src/retrieval/retrieval_agent.py::_parse_json`` (markdown-fence strip,
``{...}`` regex, brace-balance repair for small local models).

``searchable_text`` is flattened and written *here* — the ``meeting_notes``
FTS5 triggers only index it, they never derive it from the JSON columns
(docs/schema_review.md §6).
"""

import json
import logging
import re
import sqlite3

from src.common.llm_client import simple_generate
from src.common.types import ActionItem, MeetingNote
from src.features.base import TableHandler, new_id, now_iso

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_TRANSCRIPT_CHAR_LIMIT = 6000

_EXTRACTION_PROMPT = """You extract structured facts from a meeting transcript.

Read the transcript and return ONE JSON object:

{{
  "attendees": ["name", ...],
  "topics": ["short topic phrase", ...],
  "decisions": ["a decision that was made", ...],
  "action_items": [{{"task": "...", "owner": "name or null", "deadline": "when or null"}}, ...],
  "follow_ups": ["something to revisit later", ...]
}}

Rules:
- Base everything ONLY on the transcript below. Use [] for a section with nothing.
- Return ONLY the JSON object. No markdown fence, no commentary.

Transcript:
{transcript}
"""


class MeetingNoteHandler(TableHandler):
    _REQUIRED_TABLES = ("meeting_notes",)

    def capture_meeting_note(
        self, raw_transcript: str, *, session_id: str | None = None
    ) -> MeetingNote:
        extracted = self._extract_with_retry(raw_transcript)

        attendees = _str_list(extracted.get("attendees"))
        topics = _str_list(extracted.get("topics"))
        decisions = _str_list(extracted.get("decisions"))
        action_items = _action_items(extracted.get("action_items"))
        follow_ups = _str_list(extracted.get("follow_ups"))

        needs_review = extracted.get("_failed", False) or (not decisions and not action_items)
        searchable_text = " · ".join(
            [*attendees, *topics, *decisions, *(ai.task for ai in action_items), *follow_ups]
        )

        note_id = new_id("mn")
        now = now_iso()
        self._conn.execute(
            "INSERT INTO meeting_notes (id, session_id, raw_transcript, attendees, topics, "
            "decisions, action_items, follow_ups, needs_review, searchable_text, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                note_id,
                session_id,
                raw_transcript,
                json.dumps(attendees),
                json.dumps(topics),
                json.dumps(decisions),
                json.dumps([_action_item_to_dict(ai) for ai in action_items]),
                json.dumps(follow_ups),
                int(needs_review),
                searchable_text,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self._row_to_note(self._require_row("meeting_notes", note_id))

    def get_meeting_note(self, note_id: str) -> MeetingNote | None:
        row = self._get_row("meeting_notes", note_id)
        return self._row_to_note(row) if row else None

    def get_meeting_notes(self, *, needs_review: bool | None = None) -> list[MeetingNote]:
        sql = "SELECT * FROM meeting_notes WHERE deleted_at IS NULL"
        params: list[object] = []
        if needs_review is not None:
            sql += " AND needs_review = ?"
            params.append(int(needs_review))
        sql += " ORDER BY created_at"
        return [self._row_to_note(r) for r in self._conn.execute(sql, params).fetchall()]

    def delete_meeting_note(self, note_id: str) -> bool:
        return self._soft_delete("meeting_notes", note_id)

    # ------------------------------------------------------------------

    def _extract_with_retry(self, raw_transcript: str) -> dict:
        prompt = _EXTRACTION_PROMPT.format(transcript=raw_transcript[:_TRANSCRIPT_CHAR_LIMIT])
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._parse_json(simple_generate(prompt))
            except Exception as e:  # noqa: BLE001 — never propagate; fall back to needs_review
                if attempt == _MAX_RETRIES:
                    logger.warning("meeting-note extraction failed (%s); flagging for review", e)
                    return {"_failed": True}
        return {"_failed": True}

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            start = text.find("{")
            opened = text.count("{") - text.count("}")
            if start != -1 and opened > 0:
                repaired = text[start:].rstrip().rstrip(",") + "}" * opened
                try:
                    return json.loads(repaired)
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"could not parse meeting-note JSON: {raw[:200]}") from e

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> MeetingNote:
        return MeetingNote(
            id=row["id"],
            session_id=row["session_id"],
            raw_transcript=row["raw_transcript"],
            attendees=json.loads(row["attendees"] or "[]"),
            topics=json.loads(row["topics"] or "[]"),
            decisions=json.loads(row["decisions"] or "[]"),
            action_items=[
                ActionItem(
                    task=str(d.get("task", "")),
                    owner=d.get("owner"),
                    deadline=d.get("deadline"),
                )
                for d in json.loads(row["action_items"] or "[]")
            ],
            follow_ups=json.loads(row["follow_ups"] or "[]"),
            needs_review=bool(row["needs_review"]),
            searchable_text=row["searchable_text"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _action_items(value: object) -> list[ActionItem]:
    if not isinstance(value, list):
        return []
    out: list[ActionItem] = []
    for entry in value:
        if isinstance(entry, dict) and str(entry.get("task", "")).strip():
            out.append(
                ActionItem(
                    task=str(entry["task"]).strip(),
                    owner=_clean_opt(entry.get("owner")),
                    deadline=_clean_opt(entry.get("deadline")),
                )
            )
        elif isinstance(entry, str) and entry.strip():
            out.append(ActionItem(task=entry.strip()))
    return out


def _clean_opt(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None if text.lower() != "null" else None


def _action_item_to_dict(ai: ActionItem) -> dict[str, str | None]:
    return {"task": ai.task, "owner": ai.owner, "deadline": ai.deadline}
