"""
Post Processor (Phase 1 Step 1.4 — session companion).

Cleans generated answer text, extracts session-temporal citations, validates
grounding against the retrieved session context, and assembles the final
``GenerationResponse``.

Architecture:
    GeneratedAnswer + SessionRetrievedChunks → PostProcessor.process() → GenerationResponse

``AnswerCleaner`` is kept byte-identical — pinned by
tests/generation/test_post_processor_answer_cleaner.py. Configuration comes
from config/generation/post_processing.yaml.
"""

import logging
import re
from typing import Any

from src.common.types import SessionRetrievedChunk
from src.generation.config import (
    AnswerFormat,
    Citation,
    CitationFormatter,
    CitationStyle,
    GeneratedAnswer,
    GenerationMode,
    GenerationResponse,
    PostProcessingConfig,
    RetrievalMetadata,
)

logger = logging.getLogger(__name__)

# Matches "[Session <id> · approx. <ts>]" and "[<table> record · approx. <ts>]"
_CITATION_RE = re.compile(
    r"\[(?:Session\s+(?P<session_id>[^\]|·]+?)|(?P<table>[^\]|·]+?)\s+record)"
    r"\s*·\s*approx\.\s*(?P<timestamp>[^\]]+?)\]",
    re.IGNORECASE,
)


# ============================================================================
# Citation Extractor
# ============================================================================


class CitationExtractor:
    """Extracts session-temporal citation markers from generated text.

    NOTE (Phase 1 Step 1.4a): this is a full session-shaped rewrite of the
    document-era ``CitationExtractor`` (which regex-parsed ``[Course|Chapter|
    Slide N]`` brackets), not that class. It survives under the same name only
    because ``PostProcessor.process`` Step 2 still needs an extract-then-match
    step. A grep for the scrapped document-era behavior will land here — there
    is none left; ``_CITATION_RE`` matches only session/record markers.
    """

    def extract(self, text: str) -> list[Citation]:
        """
        Find every ``[Session … · approx. …]`` / ``[<table> record · approx. …]``
        marker and return an ordered list of ``Citation`` objects (chunk_id
        empty until matched to a source chunk by :class:`PostProcessor`).
        """
        citations: list[Citation] = []
        for i, m in enumerate(_CITATION_RE.finditer(text), start=1):
            session_id = (m.group("session_id") or "").strip()
            citations.append(
                Citation(
                    index=i,
                    chunk_id="",
                    session_id=session_id,
                    approximate_timestamp=m.group("timestamp").strip(),
                    position_start=m.start(),
                    position_end=m.end(),
                )
            )
        return citations


# ============================================================================
# Grounding Validator
# ============================================================================

_LLM_GROUNDING_PROMPT = """You check whether an answer is supported by the given context.

Context:
{context}

Answer:
{answer}

Is every factual claim in the Answer supported by the Context? Reply with ONE
JSON object and nothing else:
{{"grounded": true|false, "confidence": 0.0-1.0}}
"""


class GroundingValidator:
    """
    Validates that a generated answer is grounded in the retrieved session
    context. ``keyword_overlap`` (default) is a cheap lexical check;
    ``llm_check`` adds one Ollama call and falls back to keyword overlap on
    any failure.
    """

    def __init__(
        self,
        method: str = "keyword_overlap",
        min_overlap: float = 0.3,
        *,
        model: str | None = None,
        registry: Any = None,
    ):
        self.method = method
        self.min_overlap = min_overlap
        self._model = model
        self._registry = registry

    def validate(self, answer: str, chunks: list[SessionRetrievedChunk]) -> tuple[bool, float]:
        """Return ``(is_grounded, confidence)``."""
        if not chunks or not answer:
            return False, 0.0

        if self.method in ("llm_check", "both"):
            llm_result = self._llm_check(answer, chunks)
            if llm_result is not None:
                if self.method == "both":
                    kw_grounded, kw_conf = self._keyword_overlap_check(answer, chunks)
                    llm_grounded, llm_conf = llm_result
                    return (kw_grounded and llm_grounded), min(kw_conf, llm_conf)
                return llm_result
            # llm_check failed → fall through to keyword overlap
        return self._keyword_overlap_check(answer, chunks)

    def _llm_check(
        self, answer: str, chunks: list[SessionRetrievedChunk]
    ) -> tuple[bool, float] | None:
        """One Ollama call; ``None`` on any failure (caller falls back)."""
        import json

        from src.common.llm_client import simple_generate

        context = "\n\n".join(c.content for c in chunks)[:6000]
        prompt = _LLM_GROUNDING_PROMPT.format(context=context, answer=answer[:4000])
        try:
            raw = simple_generate(prompt, self._model, registry=self._registry).strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if not match:
                    return None
                data = json.loads(match.group())
            grounded = bool(data.get("grounded", False))
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
            return grounded, confidence
        except Exception as e:  # noqa: BLE001 — any failure → keyword fallback
            logger.warning("llm_check grounding failed (%s); falling back to keyword overlap", e)
            return None

    def _keyword_overlap_check(
        self, answer: str, chunks: list[SessionRetrievedChunk]
    ) -> tuple[bool, float]:
        source_text = " ".join(chunk.content.lower() for chunk in chunks)
        answer_words = self._extract_keywords(answer.lower())

        if not answer_words:
            return False, 0.0

        matched = sum(1 for word in answer_words if word in source_text)
        overlap_ratio = matched / len(answer_words)

        is_grounded = overlap_ratio >= self.min_overlap
        confidence = min(1.0, overlap_ratio / max(self.min_overlap, 0.1))
        return is_grounded, confidence

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        stopwords = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "can",
            "shall",
            "to",
            "of",
            "in",
            "for",
            "on",
            "with",
            "at",
            "by",
            "from",
            "as",
            "into",
            "through",
            "during",
            "before",
            "after",
            "this",
            "that",
            "these",
            "those",
            "it",
            "its",
            "they",
            "them",
            "and",
            "but",
            "or",
            "not",
            "no",
            "if",
            "then",
            "than",
            "also",
            "very",
            "just",
            "about",
            "each",
            "all",
            "both",
            "such",
            "only",
            "other",
            "more",
            "some",
            "most",
        }
        words = re.findall(r"\b[a-z]{3,}\b", text)
        return {w for w in words if w not in stopwords}


# ============================================================================
# Answer Cleaner  (characterization-pinned — keep byte-identical)
# ============================================================================


class AnswerCleaner:
    """
    Cleans generated answer text.

    Removes artifacts, normalizes whitespace, and formats output.
    """

    def __init__(
        self,
        remove_artifacts: bool = True,
        normalize_whitespace: bool = True,
        max_length: int | None = None,
    ):
        self.remove_artifacts = remove_artifacts
        self.normalize_whitespace = normalize_whitespace
        self.max_length = max_length

    def clean(self, text: str) -> str:
        """Clean generated answer text."""
        if not text:
            return ""

        # Remove artifacts
        if self.remove_artifacts:
            text = self._remove_artifacts(text)

        # Normalize whitespace
        if self.normalize_whitespace:
            text = self._normalize_whitespace(text)

        # Truncate if needed
        if self.max_length and len(text) > self.max_length:
            text = text[: self.max_length].rsplit(" ", 1)[0] + "..."

        return text.strip()

    def _remove_artifacts(self, text: str) -> str:
        """Remove generation artifacts."""
        # Remove [HIDE]...[/HIDE] blocks
        text = re.sub(r"\[HIDE\].*?\[/HIDE\]", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Remove <THINK>...</THINK> blocks
        text = re.sub(r"<THINK>.*?</THINK>", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Remove trailing "---" separators
        text = re.sub(r"\n*---\s*$", "", text)

        return text

    def _normalize_whitespace(self, text: str) -> str:
        """Normalize whitespace."""
        # Remove trailing whitespace per line
        lines = [line.rstrip() for line in text.split("\n")]

        # Remove empty lines at start and end
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()

        # Normalize multiple blank lines to max 2
        result = []
        blank_count = 0
        for line in lines:
            if not line.strip():
                blank_count += 1
                if blank_count <= 2:
                    result.append("")
            else:
                blank_count = 0
                result.append(line)

        return "\n".join(result)


# ============================================================================
# Post Processor
# ============================================================================


class PostProcessor:
    """
    Processes generated answers into final ``GenerationResponse`` objects.

    Composes ``CitationExtractor``, ``GroundingValidator``, and
    ``AnswerCleaner``.
    """

    def __init__(
        self,
        config: PostProcessingConfig | None = None,
        *,
        grounding_registry: Any = None,
    ):
        if config is None:
            config = PostProcessingConfig(
                extract_citations=True,
                citation_style=CitationStyle.INLINE,
                validate_grounding=True,
                grounding_check_method="keyword_overlap",
                remove_artifacts=True,
                normalize_whitespace=True,
            )

        self.config = config

        self.citation_extractor = CitationExtractor()
        self.grounding_validator = GroundingValidator(
            method=config.grounding_check_method,
            min_overlap=config.min_keyword_overlap,
            registry=grounding_registry,
        )
        self.answer_cleaner = AnswerCleaner(
            remove_artifacts=config.remove_artifacts,
            normalize_whitespace=config.normalize_whitespace,
            max_length=config.max_answer_length,
        )
        self.citation_formatter = CitationFormatter()

    def process(
        self,
        generated: GeneratedAnswer,
        chunks: list[SessionRetrievedChunk],
        retrieval_metadata: RetrievalMetadata,
        mode: GenerationMode,
        request_id: str,
        generation_id: str,
        warnings: list[str] | None = None,
        sources_used: list[str] | None = None,
    ) -> GenerationResponse:
        """Process a generated answer into the final response."""
        all_warnings = list(warnings) if warnings else []

        # Step 1: Clean the answer
        cleaned_answer = self.answer_cleaner.clean(generated.content)

        # Step 2: Extract + match citations
        citations: list[Citation] = []
        if self.config.extract_citations:
            citations = self.citation_extractor.extract(cleaned_answer)
            for c in citations:
                matched = self._match_citation_to_chunk(c, chunks)
                if matched:
                    c.chunk_id = matched.chunk_id
                    if not c.approximate_timestamp:
                        c.approximate_timestamp = matched.timestamp
            if not citations:
                all_warnings.append("No citations found in generated answer")

        # Step 3: Validate grounding
        is_grounded = False
        grounding_confidence = 0.0
        if self.config.validate_grounding:
            is_grounded, grounding_confidence = self.grounding_validator.validate(
                answer=cleaned_answer, chunks=chunks
            )
            if not is_grounded:
                all_warnings.append(
                    f"Answer may not be fully grounded in context "
                    f"(confidence: {grounding_confidence:.2f})"
                )

        # Step 4: Build final response
        return GenerationResponse(
            answer=cleaned_answer,
            mode=mode,
            answer_format=(
                AnswerFormat.MARKDOWN if self.config.format_as_markdown else AnswerFormat.PLAIN_TEXT
            ),
            citations=citations,
            is_grounded=is_grounded,
            grounding_confidence=round(grounding_confidence, 4),
            retrieval_metadata=retrieval_metadata,
            model_info=generated.model_info,
            usage=generated.usage,
            generation_time_ms=generated.generation_time_ms,
            total_time_ms=0,  # Set by orchestrator
            request_id=request_id,
            generation_id=generation_id,
            warnings=all_warnings,
            sources_used=sources_used or [],
        )

    @staticmethod
    def _match_citation_to_chunk(
        citation: Citation, chunks: list[SessionRetrievedChunk]
    ) -> SessionRetrievedChunk | None:
        """Match a parsed citation to a source chunk by session id."""
        if not citation.session_id or citation.session_id.lower() == "unknown":
            return None
        for chunk in chunks:
            if chunk.session_id and chunk.session_id.lower() == citation.session_id.lower():
                return chunk
        return None
