"""
Prompt Builder (Phase 1 Step 1.4 — session companion).

Loads a YAML prompt template by ``prompt_id``, assembles retrieved session
chunks into context (delegated to ``src.retrieval.context_builder.ContextBuilder``
— one assembly path, one citation format), injects the current session's
recent conversation history as a first-class prompt section, substitutes
template variables, and returns a structured ``Prompt``.

``TokenCounter`` and ``TemplateLoader`` are kept byte-identical — pinned by
tests/generation/test_prompt_builder_{token_counter,template_loader}.py.
``_substitute_with_validation`` likewise (test_prompt_builder_substitution.py).
"""

import hashlib
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.common.types import SessionRetrievedChunk
from src.generation.config import ChatTurn, GenerationRequest, ModeConfig, Prompt
from src.retrieval.context_builder import ContextBuilder

load_dotenv()

logger = logging.getLogger(__name__)


# ============================================================================
# Token Counter  (characterization-pinned — keep byte-identical)
# ============================================================================


class TokenCounter:
    """
    Counts tokens in text for context budget management.

    Uses tiktoken if available, falls back to character-based estimation.
    """

    def __init__(self, model_name: str = "gpt-4o"):
        """
        Initialize token counter.

        Args:
            model_name: Model name for tiktoken encoding lookup
        """
        self.model_name = model_name
        self._encoder = None

        try:
            import tiktoken

            try:
                self._encoder = tiktoken.encoding_for_model(model_name)
            except Exception:
                self._encoder = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            self._encoder = None

    def count(self, text: str) -> int:
        """Count tokens in text."""
        if not text:
            return 0

        if self._encoder is not None:
            try:
                return len(self._encoder.encode(text))
            except Exception:
                pass

        # Fallback: ~4 chars per token
        return len(text) // 4

    def count_batch(self, texts: list[str]) -> list[int]:
        """Count tokens for multiple texts."""
        return [self.count(t) for t in texts]


# ============================================================================
# Template Loader  (characterization-pinned — keep byte-identical)
# ============================================================================


class TemplateLoader:
    """
    Loads and caches prompt templates from YAML files.

    Templates are referenced by prompt_id, not file path.
    Resolution: prompt_id → config/generation/prompts/{prompt_id}.yaml
    """

    def __init__(self, templates_dir: str | None = None):
        """
        Initialize template loader.

        Args:
            templates_dir: Path to prompt templates directory.
                           Defaults from env var or config/generation/prompts/
        """
        self.templates_dir = Path(
            templates_dir
            or os.getenv("RAGPIPE_PROMPT_TEMPLATES_DIR")
            or str(Path(__file__).parent.parent.parent / "config" / "generation" / "prompts")
        )

        if not self.templates_dir.exists():
            raise FileNotFoundError(f"Prompt templates directory not found: {self.templates_dir}")

        # Template cache: prompt_id → (version, system_prompt, user_prompt)
        self._cache: dict[str, tuple[str, str, str]] = {}

    def load(self, prompt_id: str) -> tuple[str, str, str]:
        """
        Load a prompt template by its identifier.

        Args:
            prompt_id: Template identifier (e.g., "context_aware")

        Returns:
            Tuple of (version, system_prompt, user_prompt)

        Raises:
            FileNotFoundError: If template file doesn't exist
            ValueError: If template is missing required fields
        """
        # Check cache
        if prompt_id in self._cache:
            return self._cache[prompt_id]

        # Resolve file path
        template_path = self.templates_dir / f"{prompt_id}.yaml"

        if not template_path.exists():
            raise FileNotFoundError(
                f"Prompt template not found: {template_path}\n"
                f"Available templates: {self._list_available()}"
            )

        # Load and parse
        with open(template_path) as f:
            data = yaml.safe_load(f) or {}

        version = str(data.get("version", "1.0.0"))
        system_prompt = str(data.get("system", ""))
        user_prompt = str(data.get("user", ""))

        if not system_prompt and not user_prompt:
            raise ValueError(f"Template '{prompt_id}' missing both 'system' and 'user' fields")

        # Cache
        self._cache[prompt_id] = (version, system_prompt, user_prompt)

        return version, system_prompt, user_prompt

    def get_version(self, prompt_id: str) -> str:
        """Get the version of a template without loading the full content."""
        version, _, _ = self.load(prompt_id)
        return version

    def _list_available(self) -> list[str]:
        """List available template IDs."""
        if not self.templates_dir.exists():
            return []
        return [p.stem for p in self.templates_dir.glob("*.yaml")]

    def clear_cache(self) -> None:
        """Clear the template cache (useful for hot-reloading)."""
        self._cache.clear()


# ============================================================================
# Prompt Builder
# ============================================================================


class PromptBuilder:
    """
    Builds structured ``Prompt`` objects from YAML templates + retrieved
    session chunks + conversation history.

    Usage:
        builder = PromptBuilder()
        prompt = builder.build(request, mode_config)
    """

    def __init__(
        self,
        templates_dir: str | None = None,
        token_counter: TokenCounter | None = None,
        context_builder: ContextBuilder | None = None,
        log_missing_variables: bool = True,
    ):
        self.template_loader = TemplateLoader(templates_dir)
        self.token_counter = token_counter or TokenCounter()
        # Direct import verified acyclic (retrieval.context_builder imports only
        # common.types); the param is an injection point for tests.
        self.context_builder = context_builder or ContextBuilder()
        self.log_missing_variables = log_missing_variables

    def build(self, request: GenerationRequest, mode_config: ModeConfig) -> Prompt:
        """Build a ``Prompt`` from a ``GenerationRequest``."""
        version, system_template, user_template = self.template_loader.load(mode_config.prompt_id)

        assembled = self.context_builder.build(request.chunks)
        context = assembled.formatted_context
        context_tokens = assembled.tokens_used

        variables = {
            "context": context,
            "conversation_history": self._format_history(request.conversation_history),
            "query": request.query,
            "session_date_range": self._session_date_range(request.chunks),
            "topics": self._topics(request.chunks),
            "answer_hints": self._build_answer_hints_instruction(request.answer_hints),
        }

        system_prompt = self._substitute_with_validation(system_template, variables)
        user_prompt = self._substitute_with_validation(user_template, variables)

        system_tokens = self.token_counter.count(system_prompt)
        user_tokens = self.token_counter.count(user_prompt)
        total_tokens = system_tokens + user_tokens

        prompt_id = self._generate_prompt_id(request.request_id, mode_config.prompt_id)

        return Prompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            template_id=mode_config.prompt_id,
            template_version=version,
            context_token_count=context_tokens,
            system_token_count=system_tokens,
            user_token_count=user_tokens,
            total_token_count=total_tokens,
            prompt_id=prompt_id,
            request_id=request.request_id,
            mode=request.mode,
            assembled_at=datetime.now(UTC).isoformat(),
        )

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_history(history: list[ChatTurn] | None) -> str:
        """Oldest-first, role-labelled recent conversation."""
        if not history:
            return ""
        lines = ["## RECENT CONVERSATION"]
        for turn in history:
            label = "User" if turn.role == "user" else "Assistant"
            lines.append(f"[{label}]: {turn.content}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _session_date_range(chunks: list[SessionRetrievedChunk]) -> str:
        stamps = sorted(c.timestamp for c in chunks if c.timestamp)
        if not stamps:
            return "unknown"
        if stamps[0] == stamps[-1]:
            return stamps[0]
        return f"{stamps[0]} – {stamps[-1]}"

    @staticmethod
    def _topics(chunks: list[SessionRetrievedChunk]) -> str:
        seen: list[str] = []
        for c in chunks:
            for t in c.topics:
                if t and t not in seen:
                    seen.append(t)
        return ", ".join(seen) if seen else "—"

    def _build_answer_hints_instruction(self, answer_hints: dict[str, str] | None) -> str:
        """
        Turn caller-supplied answer_hints into an "## ANSWER REQUIREMENTS"
        block appended to the prompt, so hints are never silently ignored.
        Returns "" when no hints are set.
        """
        if not answer_hints:
            return ""

        lines = ["## ANSWER REQUIREMENTS"] + [v for v in answer_hints.values() if v]

        if len(lines) == 1:
            return ""  # answer_hints given but every value was empty

        return "\n" + "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # Pinned pure helpers  (keep byte-identical)
    # ------------------------------------------------------------------

    def _substitute_with_validation(self, template: str, variables: dict[str, str]) -> str:
        """
        Substitute variables in a template string with validation.

        Uses {variable} syntax. Missing variables are left as-is but
        logged as warnings for debugging.
        """
        result = template

        # Find all placeholders in the template
        placeholders = re.findall(r"\{([^}]+)\}", template)

        for placeholder in placeholders:
            if placeholder in variables:
                # Substitute the variable
                result = result.replace(f"{{{placeholder}}}", variables[placeholder])
            else:
                # Log missing variable if configured
                if self.log_missing_variables:
                    logger.warning(
                        f"Missing template variable: '{{{placeholder}}}' in template. "
                        f"Available variables: {list(variables.keys())}"
                    )
                # Leave placeholder as-is (debug-friendly)

        return result

    def _assemble_full_prompt(self, system_prompt: str, user_prompt: str) -> str:
        """Assemble system and user prompts into a single string."""
        parts = []
        if system_prompt:
            parts.append(f"[SYSTEM]\n{system_prompt}")
        if user_prompt:
            parts.append(f"[USER]\n{user_prompt}")
        return "\n\n".join(parts)

    def _generate_prompt_id(self, request_id: str, template_id: str) -> str:
        """Generate a deterministic prompt ID."""
        raw = f"{request_id}:{template_id}:{time.time()}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]
