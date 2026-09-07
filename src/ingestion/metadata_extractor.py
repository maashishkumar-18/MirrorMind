"""
Session metadata extraction (Phase 1 Step 1.2).

One Ollama call per session (via ``simple_generate``) that classifies the
whole conversation: the topics it covers, which structured actions it
contains, the entities mentioned, and the overall sentiment. The result is
denormalized onto every chunk of the session by the chunker, so retrieval
and generation never have to re-derive it.
"""

import json
import re
from dataclasses import dataclass, field

from src.common.llm_client import ProviderRegistry, simple_generate
from src.common.types import AgenticActionType, SessionMessage

_ACTION_TYPES = sorted(a.value for a in AgenticActionType)
_SENTIMENTS = ("positive", "neutral", "negative", "mixed")
_SAMPLE_CHAR_LIMIT = 4000


@dataclass
class SessionMetadata:
    """Whole-session metadata extracted by one LLM classification call."""

    session_id: str
    timestamp: str
    topics: list[str] = field(default_factory=list)
    action_types: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    sentiment: str = "neutral"
    extraction_confidence: float = 0.0


class MetadataExtractor:
    """Extracts :class:`SessionMetadata` from a conversation transcript."""

    def __init__(
        self, model: str | None = None, *, registry: ProviderRegistry | None = None
    ) -> None:
        # ``None`` keeps the historical behaviour (``simple_generate`` resolves
        # ``$OLLAMA_DEFAULT_MODEL``); the Phase 3 backend passes the app-config
        # active model so ingestion follows a Settings model switch. Mirrors
        # ``RetrievalAgent.__init__``.
        self._model = model
        self._registry = registry

    def extract(
        self, session_id: str, messages: list[SessionMessage], timestamp: str
    ) -> SessionMetadata:
        sample = self._prepare_sample(messages)
        raw = self._call_llm(sample)
        return self._parse_response(raw, session_id, timestamp)

    def _prepare_sample(self, messages: list[SessionMessage]) -> str:
        """Role-labelled turns (`[User]:` / `[Assistant]:`), truncated."""
        lines = []
        for m in messages:
            label = "User" if m.role == "user" else "Assistant"
            lines.append(f"[{label}]: {m.content}")
        return "\n".join(lines)[:_SAMPLE_CHAR_LIMIT]

    def _call_llm(self, sample: str) -> dict:
        prompt = f"""You classify a conversation between a person and their personal AI companion.

Read the transcript and return ONE JSON object:

{{
  "topics": ["short topic phrase", ...],          // 1-6 items, what the conversation is about
  "action_types": ["reminder", "todo", ...],      // subset of {_ACTION_TYPES}; [] if none
  "entities": ["Person or place or thing", ...],  // named entities mentioned; [] if none
  "sentiment": "neutral",                         // one of {list(_SENTIMENTS)}
  "confidence": 0.0                               // 0.0-1.0, your confidence in this classification
}}

Rules:
- Base everything ONLY on the transcript below.
- Return ONLY the JSON object. No markdown, no ```json fence, no commentary.

Transcript:
{sample}
"""
        response_text = simple_generate(
            prompt, model_name=self._model, registry=self._registry
        ).strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError(f"Could not parse session metadata JSON: {response_text[:200]}") from e

    def _parse_response(self, raw: dict, session_id: str, timestamp: str) -> SessionMetadata:
        def _str_list(value: object) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(v).strip() for v in value if str(v).strip()]

        sentiment = str(raw.get("sentiment", "neutral")).lower().strip()
        if sentiment not in _SENTIMENTS:
            sentiment = "neutral"

        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        valid_actions = set(_ACTION_TYPES)
        action_types = [a for a in _str_list(raw.get("action_types")) if a in valid_actions]

        return SessionMetadata(
            session_id=session_id,
            timestamp=timestamp,
            topics=_str_list(raw.get("topics")),
            action_types=action_types,
            entities=_str_list(raw.get("entities")),
            sentiment=sentiment,
            extraction_confidence=confidence,
        )

    def extract_with_retry(
        self,
        session_id: str,
        messages: list[SessionMessage],
        timestamp: str,
        max_retries: int = 2,
    ) -> SessionMetadata:
        """Extract with retry; an empty :class:`SessionMetadata` on final failure."""
        for attempt in range(max_retries + 1):
            try:
                return self.extract(session_id, messages, timestamp)
            except Exception as e:
                if attempt == max_retries:
                    return SessionMetadata(session_id=session_id, timestamp=timestamp)
                print(f"⚠️  Session metadata attempt {attempt + 1} failed: {e}. Retrying...")
        return SessionMetadata(session_id=session_id, timestamp=timestamp)
