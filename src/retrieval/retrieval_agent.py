"""
Retrieval Agent (Phase 1 Step 1.3b — session companion).

The single agentic-reasoning LLM call. One ``simple_generate`` round trip to
the local Ollama model turns the user's message (plus recent conversation
history) into an ``AgenticOutput`` (src/common/types.py): what the message
is (``action_type``), how sure we are (``confidence``), whether memory
retrieval should run (``retrieve_needed``) and over which path
(``retrieval_route``), the normalized ``search_query``, and the
natural-language ``response`` — all in the same call (project_logic.md §3).

``retrieve_needed = false`` is the short-circuit: the caller returns
``AgenticOutput.response`` straight to the frontend and never invokes the
Retrieval Router (``RetrievalRouter.route`` also returns ``[]`` for that
case). No second model call.

JSON recovery mirrors ``MetadataExtractor._call_llm``: strip a markdown
fence, ``json.loads``, then a ``{...}`` regex fallback. Any parse or
validation failure degrades to a safe conversational ``AgenticOutput``
rather than raising into the request path.
"""

from pydantic import ValidationError

from src.common.json_recovery import recover_json
from src.common.llm_client import ProviderRegistry, simple_generate
from src.common.types import (
    AgenticActionType,
    AgenticOutput,
    RetrievalRoute,
    SessionMessage,
)

_ACTION_VALUES = [a.value for a in AgenticActionType]
_ROUTE_VALUES = [r.value for r in RetrievalRoute]
_HISTORY_TURNS = 6
# The agentic call is one structured-JSON completion. ``simple_generate``'s
# 30s default is fine for a 3B model but a 7B/8B on CPU inference (no GPU
# acceleration on the reference-class hardware this ships to) routinely needs
# longer — and a timeout here degrades to the safe CONVERSATION fallback,
# silently zeroing routing accuracy. 120s absorbs CPU 7B/8B latency.
_AGENT_TIMEOUT_SECONDS = 120


class RetrievalAgent:
    """One Ollama call: user message -> :class:`AgenticOutput`."""

    def __init__(self, model: str | None = None, *, registry: ProviderRegistry | None = None):
        self.model_name = model
        self.registry = registry

    def reason(
        self,
        query: str,
        conversation_history: list[SessionMessage] | None = None,
    ) -> AgenticOutput:
        """Classify + route + respond to ``query`` in a single model call."""
        prompt = self._build_prompt(query, conversation_history or [])
        try:
            raw = simple_generate(
                prompt,
                self.model_name,
                timeout_seconds=_AGENT_TIMEOUT_SECONDS,
                registry=self.registry,
            )
        except Exception:  # provider/transport failure — never propagate
            return self._fallback("")

        try:
            data = self._parse_json(raw)
            if not isinstance(data, dict):
                raise ValueError("agentic output is not a JSON object")
            if data.get("retrieval_route") not in _ROUTE_VALUES:
                # Small local models (llama3.2) emit "none"/null here whenever
                # retrieve_needed is false — where the route is a don't-care
                # (RetrievalRouter short-circuits). "semantic" is the safe
                # default; a genuine structured/hybrid need is stated explicitly.
                data["retrieval_route"] = RetrievalRoute.SEMANTIC.value
            return AgenticOutput.model_validate(data)
        except (ValueError, ValidationError):
            return self._fallback(raw)

    # ------------------------------------------------------------------

    def _build_prompt(self, query: str, history: list[SessionMessage]) -> str:
        history_text = "\n".join(
            f"[{'User' if m.role == 'user' else 'Assistant'}]: {m.content}"
            for m in history[-_HISTORY_TURNS:]
        )
        return f"""You are the reasoning core of a personal AI companion — a private, \
on-device assistant that remembers past conversations and manages the user's \
reminders, todos, meeting notes and schedule.

Read the recent conversation and the new message, then return ONE JSON object:

{{
  "action_type": one of {_ACTION_VALUES},
  "confidence": 0.0-1.0,            // how sure you are of action_type
  "retrieve_needed": true|false,   // does answering need memory/records lookup?
  "retrieval_route": one of {_ROUTE_VALUES},
  "search_query": "normalized query for retrieval" or null,
  "response": "your natural-language reply to the user"
}}

Guidance:
- confidence >= 0.85: act on it. 0.70-0.85: you'd want a quick confirm.
  0.50-0.70: you'd ask for clarification. < 0.50: treat it as plain conversation.
- retrieve_needed = false for greetings, small talk, or anything you can answer
  without looking anything up. true when the user refers to something from the
  past or to their reminders/todos/meetings/schedule.
- retrieval_route: "semantic" = search past conversations; "structured" =
  search reminders / todos / meeting notes / schedule items; "hybrid" = both.
- Base everything ONLY on the conversation below.
- Return ONLY the JSON object. No markdown fence, no commentary.

Recent conversation:
{history_text or "(none)"}

New message:
[User]: {query}
"""

    @staticmethod
    def _parse_json(raw: str) -> dict:
        # Shared recovery (fence strip / {...} regex / brace-balance repair) —
        # src/common/json_recovery.py.
        return recover_json(raw)

    @staticmethod
    def _fallback(raw: str) -> AgenticOutput:
        """A safe conversational output when the model response is unusable."""
        response = (raw or "").strip()
        # Don't echo raw model output that still looks like the JSON envelope we
        # failed to parse (leading `{`/`[`, a markdown fence, or a body that is
        # mostly braces/brackets — a truncated object). project_logic.md §4:
        # the user must never see the machine contract.
        brace_ratio = sum(response.count(c) for c in '{}[]"') / len(response) if response else 0.0
        if not response or response.startswith(("{", "[", "```")) or brace_ratio > 0.15:
            response = "Sorry, I had trouble understanding that — could you rephrase?"
        return AgenticOutput(
            action_type=AgenticActionType.CONVERSATION,
            confidence=0.0,
            retrieve_needed=False,
            retrieval_route=RetrievalRoute.SEMANTIC,
            search_query=None,
            response=response,
        )
