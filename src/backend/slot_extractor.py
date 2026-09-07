"""Utterance → structured feature-handler slots (Phase 3 Step 3.1d).

The second LLM call in the Tier-1 actionable path: once the agentic call has
classified a message as ``reminder`` / ``todo`` / ``schedule`` /
``summary_request`` at confidence >= 0.85, this pulls the fields the matching
Step 1.5 handler needs. ``meeting_note`` is not handled here — its handler
(``capture_meeting_note``) self-extracts from the raw transcript.

One ``simple_generate`` per call, ``recover_json`` for parsing, and a **safe
empty dict on any failure** — never raises into ``SessionWorker.send()``.
Mirrors ``RetrievalAgent`` / ``MetadataExtractor``.
"""

from __future__ import annotations

import logging

from src.common.json_recovery import recover_json
from src.common.llm_client import ProviderRegistry, simple_generate
from src.common.types import AgenticActionType

logger = logging.getLogger(__name__)

_SLOT_TIMEOUT_SECONDS = 120  # CPU 7B/8B, same budget as RetrievalAgent

# One prompt per actionable type. {now} lets the model resolve "tomorrow at 3".
_PROMPTS: dict[AgenticActionType, str] = {
    AgenticActionType.REMINDER: """The user wants to set a reminder. The current time is {now}.
Extract this JSON object (use null for anything not stated):
{{"title": "what to be reminded of", "scheduled_time": "ISO-8601 datetime", "notes": "extra detail or null"}}
Resolve relative times ("tomorrow", "Friday 3pm") against the current time. Output ISO-8601
with a timezone offset. Return ONLY the JSON object.

User message: {utterance}
""",
    AgenticActionType.TODO: """The user wants to add a todo. The current time is {now}.
Extract this JSON object (use null for anything not stated):
{{"title": "the task", "priority": "low|medium|high or null", "category": "a short category or null", "notes": "extra detail or null"}}
Return ONLY the JSON object.

User message: {utterance}
""",
    AgenticActionType.SCHEDULE: """The user wants to schedule something. The current time is {now}.
Extract this JSON object (use null for anything not stated):
{{"title": "the event", "start_time": "ISO-8601 datetime", "end_time": "ISO-8601 datetime", "location": "where or null", "notes": "extra detail or null"}}
Resolve relative times against the current time. If no end time is stated, assume one hour after
the start. Output ISO-8601 with a timezone offset. Return ONLY the JSON object.

User message: {utterance}
""",
    AgenticActionType.SUMMARY_REQUEST: """The user is asking for a summary. The current time is {now}.
Extract this JSON object:
{{"period": "daily or weekly", "date": "ISO-8601 date the summary covers, or null for today/this week"}}
"my day"/"today" -> daily; "my week"/"this week" -> weekly; a bare "summarize" -> daily.
Return ONLY the JSON object.

User message: {utterance}
""",
}


class SlotExtractor:
    def __init__(
        self, model: str | None = None, *, registry: ProviderRegistry | None = None
    ) -> None:
        self._model = model
        self._registry = registry

    def extract(
        self, action_type: AgenticActionType, utterance: str, now: str
    ) -> dict[str, object]:
        """Slots for ``action_type``. ``{}`` on any parse / transport failure —
        the dispatcher then treats every field as missing and nudges the user."""
        prompt = _PROMPTS.get(action_type)
        if prompt is None:
            return {}
        try:
            raw = simple_generate(
                prompt.format(now=now, utterance=utterance),
                model_name=self._model,
                timeout_seconds=_SLOT_TIMEOUT_SECONDS,
                registry=self._registry,
            )
        except Exception:  # noqa: BLE001 — never propagate into send()
            logger.warning("slot extraction transport failure for %s", action_type.value)
            return {}
        try:
            data = recover_json(raw)
        except ValueError:
            logger.warning("slot extraction unparseable for %s: %r", action_type.value, raw[:120])
            return {}
        if not isinstance(data, dict):
            return {}
        # normalise: drop null / empty-string values so the dispatcher's
        # "missing slot" checks are simple `key not in slots`
        return {k: v for k, v in data.items() if v not in (None, "", [])}
