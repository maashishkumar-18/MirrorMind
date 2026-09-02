"""
Text cleaning for the session ingestion pipeline (Phase 1 Step 1.2).

Fixes encoding damage (mojibake), normalizes unicode and whitespace, and
strips control characters from message text before it is chunked and
embedded. Domain-agnostic — the same repairs that mattered for pasted
document text matter for pasted conversation text.
"""

import re
import unicodedata

import ftfy

from src.common.types import SessionMessage


class TextCleaner:
    """Cleans and standardizes raw text (message content)."""

    def __init__(self) -> None:
        self.default_config = {
            "fix_encoding": True,
            "normalize_whitespace": True,
            "remove_control_chars": True,
            "normalize_unicode": True,
        }

    def clean_text(self, text: str, config: dict | None = None) -> str:
        """Apply all configured cleaning steps to a single string."""
        cfg = {**self.default_config, **(config or {})}
        cleaned = text

        if cfg["fix_encoding"]:
            cleaned = ftfy.fix_text(cleaned)

        if cfg["normalize_unicode"]:
            cleaned = unicodedata.normalize("NFKC", cleaned)

        if cfg["remove_control_chars"]:
            cleaned = self._remove_control_chars(cleaned)

        if cfg["normalize_whitespace"]:
            cleaned = self._normalize_whitespace(cleaned)

        return cleaned.strip()

    def clean_messages(
        self, messages: list[SessionMessage], config: dict | None = None
    ) -> list[SessionMessage]:
        """Return a new list with each message's content cleaned; roles untouched."""
        return [
            SessionMessage(role=m.role, content=self.clean_text(m.content, config))
            for m in messages
        ]

    def _remove_control_chars(self, text: str) -> str:
        """Remove control characters while preserving newlines and tabs."""
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)

    def _normalize_whitespace(self, text: str) -> str:
        """
        Collapse runs of spaces and trailing per-line whitespace, keep single
        paragraph breaks (cap consecutive newlines at 2).
        """
        lines = [line.rstrip() for line in text.split("\n")]
        text = "\n".join(lines)
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text
