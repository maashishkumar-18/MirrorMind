"""
Characterization tests for GenerationOrchestrator's request lifecycle
(src/generation/orchestrator.py).

All three collaborators (LLMClient, PromptBuilder, PostProcessor) are
injected as mocks via the constructor's own injection points -- the real
seam this class already provides, not a monkeypatch. `config` is also
injected explicitly (a hand-built GenerationConfig, no real YAML I/O).
traced_span() is confirmed a genuine no-op without LANGFUSE_PUBLIC_KEY/
LANGFUSE_SECRET_KEY -- the _NullSpan returned there accepts any .update()
call, so no stubbing is needed beyond tests/conftest.py's guard.

Each of _generate_impl's failure branches returns a fully-formed
GenerationResponse via _build_error_response rather than raising -- this
suite exercises every branch, not just the happy path.

Phase 1 Step 1.4 (user-approved retarget): ModeConfig lost
`include_source_prefix`/`prompt_version_constraint`; the default provider is
Ollama; `AssemblyStrategySelector` and `PromptBuilder.set_assembly_strategy`
are gone (context assembly moved into ContextBuilder), so the
assembly-strategy test was deleted -- `conversation_history` wiring is now
pinned by tests/generation/test_prompt_builder_session.py instead.
"""

from unittest.mock import MagicMock

import pytest

from src.generation.config import (
    AnswerFormat,
    CitationStyle,
    ConfidenceLevel,
    GeneratedAnswer,
    GenerationConfig,
    GenerationMode,
    GenerationRequest,
    ModeConfig,
    ModelConfig,
    ModelInfo,
    PostProcessingConfig,
    RetrievalMetadata,
    UsageStats,
)
from src.generation.orchestrator import GenerationOrchestrator

pytestmark = pytest.mark.characterization


def _make_config():
    post_processing = PostProcessingConfig(
        extract_citations=True,
        citation_style=CitationStyle.INLINE,
        validate_grounding=True,
        grounding_check_method="keyword_overlap",
        remove_artifacts=True,
        normalize_whitespace=True,
    )
    mode_config = ModeConfig(
        mode=GenerationMode.CONTEXT_AWARE,
        prompt_id="context_aware",
        model_key="test_model",
        answer_format=AnswerFormat.PLAIN_TEXT,
        citation_style=CitationStyle.INLINE,
        include_context_header=True,
        max_context_tokens=4000,
        system_instructions="test",
    )
    model_config = ModelConfig(provider="ollama", model_name="llama3.1:8b")
    return GenerationConfig(
        default_model_key="test_model",
        models={"test_model": model_config},
        modes={GenerationMode.CONTEXT_AWARE.value: mode_config},
        post_processing=post_processing,
    )


def _make_request(mode=GenerationMode.CONTEXT_AWARE):
    return GenerationRequest(
        request_id="req-1",
        query="what did we discuss",
        mode=mode,
        chunks=[],
        retrieval_metadata=RetrievalMetadata(
            confidence_score=0.9,
            confidence_level=ConfidenceLevel.HIGH,
            retrieval_time_ms=10.0,
            total_chunks_retrieved=0,
            session_id="default",
        ),
    )


def _make_generated_answer(content="the answer", finish_reason="stop"):
    return GeneratedAnswer(
        content=content,
        model_info=ModelInfo(provider="ollama", model_name="llama3.1:8b"),
        usage=UsageStats(input_tokens=10, output_tokens=5, total_tokens=15),
        generation_time_ms=42.0,
        finish_reason=finish_reason,
    )


def _make_orchestrator(llm_client=None, prompt_builder=None, post_processor=None, config=None):
    return GenerationOrchestrator(
        config=config or _make_config(),
        prompt_builder=prompt_builder or MagicMock(),
        llm_client=llm_client or MagicMock(),
        post_processor=post_processor or MagicMock(),
    )


class TestHappyPath:
    def test_full_pipeline_produces_a_populated_response(self):
        llm_client = MagicMock()
        generated = _make_generated_answer()
        llm_client.generate.return_value = generated

        prompt_builder = MagicMock()
        fake_prompt = MagicMock(
            system_prompt="sys",
            user_prompt="usr",
            template_id="context_aware",
            template_version="1.0.0",
        )
        prompt_builder.build.return_value = fake_prompt

        post_processor = MagicMock()
        from src.generation.config import GenerationResponse

        expected_response = GenerationResponse(
            answer="the answer",
            mode=GenerationMode.CONTEXT_AWARE,
            answer_format=AnswerFormat.PLAIN_TEXT,
            citations=[],
            is_grounded=True,
            grounding_confidence=0.9,
            retrieval_metadata=_make_request().retrieval_metadata,
            model_info=ModelInfo(provider="ollama", model_name="llama3.1:8b"),
            usage=generated.usage,
            generation_time_ms=0,
            total_time_ms=0,
            request_id="req-1",
            generation_id="whatever",
            warnings=[],
            sources_used=[],
        )
        post_processor.process.return_value = expected_response

        orchestrator = _make_orchestrator(llm_client, prompt_builder, post_processor)
        response = orchestrator.generate(_make_request())

        assert response.answer == "the answer"
        assert response.error_type is None
        # generation_time_ms/total_time_ms/prompt_version/config_version are
        # set by _generate_impl AFTER post_processor.process() returns.
        assert response.generation_time_ms == generated.generation_time_ms
        assert response.total_time_ms >= 0
        assert response.prompt_version == "1.0.0"
        assert response.config_version == orchestrator.config.config_version

    def test_post_processor_receives_the_generated_answer_and_request_fields(self):
        prompt_builder = MagicMock()
        prompt_builder.build.return_value = MagicMock(
            system_prompt="s", user_prompt="u", template_id="x", template_version="1.0.0"
        )
        llm_client = MagicMock()
        generated = _make_generated_answer()
        llm_client.generate.return_value = generated
        post_processor = MagicMock()

        orchestrator = _make_orchestrator(llm_client, prompt_builder, post_processor)
        request = _make_request()
        orchestrator.generate(request)

        _args, kwargs = post_processor.process.call_args
        assert kwargs["generated"] is generated
        assert kwargs["request_id"] == request.request_id
        assert kwargs["mode"] == request.mode


class TestConfigResolutionFailure:
    def test_unknown_mode_returns_error_response_not_raising(self):
        orchestrator = _make_orchestrator()
        # ModeConfig lookup for a mode not in config.modes raises ValueError
        # inside get_mode_config() -- caught by Step 1's try/except.
        request = _make_request(mode=GenerationMode.SIMPLE_EXPLANATION)

        response = orchestrator.generate(request)

        assert response.answer == ""
        assert response.error_type is None  # error_type only set when `generated` exists
        assert any("Configuration resolution failed" in w for w in response.warnings)
        assert response.model_info.provider == "unknown"

    def test_llm_client_and_prompt_builder_are_never_called_on_config_failure(self):
        llm_client = MagicMock()
        prompt_builder = MagicMock()
        orchestrator = _make_orchestrator(llm_client=llm_client, prompt_builder=prompt_builder)

        orchestrator.generate(_make_request(mode=GenerationMode.SIMPLE_EXPLANATION))

        llm_client.generate.assert_not_called()
        prompt_builder.build.assert_not_called()


class TestPromptBuildingFailure:
    def test_prompt_builder_exception_returns_error_response(self):
        prompt_builder = MagicMock()
        prompt_builder.build.side_effect = RuntimeError("template not found")
        llm_client = MagicMock()

        orchestrator = _make_orchestrator(llm_client=llm_client, prompt_builder=prompt_builder)
        response = orchestrator.generate(_make_request())

        assert response.answer == ""
        assert any("Prompt building failed" in w for w in response.warnings)
        # model_info IS populated here -- mode/model config resolved successfully
        # before the prompt-building step failed.
        assert response.model_info.provider == "ollama"
        llm_client.generate.assert_not_called()


class TestLlmGenerationFailure:
    def test_llm_client_exception_returns_error_response(self):
        prompt_builder = MagicMock()
        prompt_builder.build.return_value = MagicMock(
            system_prompt="s", user_prompt="u", template_id="x", template_version="1.0.0"
        )
        llm_client = MagicMock()
        llm_client.generate.side_effect = RuntimeError("provider unreachable")
        post_processor = MagicMock()

        orchestrator = _make_orchestrator(llm_client, prompt_builder, post_processor)
        response = orchestrator.generate(_make_request())

        assert response.answer == ""
        assert any("LLM generation failed" in w for w in response.warnings)
        post_processor.process.assert_not_called()


class TestEmptyOrErrorLlmResponse:
    def test_empty_content_returns_error_response_preserving_usage(self):
        prompt_builder = MagicMock()
        prompt_builder.build.return_value = MagicMock(
            system_prompt="s", user_prompt="u", template_id="x", template_version="1.0.0"
        )
        llm_client = MagicMock()
        generated = _make_generated_answer(content="")
        llm_client.generate.return_value = generated
        post_processor = MagicMock()

        orchestrator = _make_orchestrator(llm_client, prompt_builder, post_processor)
        response = orchestrator.generate(_make_request())

        assert response.answer == ""
        assert any("LLM returned empty or error response" in w for w in response.warnings)
        # `generated` is passed through to _build_error_response here, so
        # usage/generation_time_ms are preserved from the (empty) LLM call.
        assert response.usage == generated.usage
        assert response.generation_time_ms == generated.generation_time_ms
        post_processor.process.assert_not_called()

    def test_finish_reason_error_returns_error_response(self):
        prompt_builder = MagicMock()
        prompt_builder.build.return_value = MagicMock(
            system_prompt="s", user_prompt="u", template_id="x", template_version="1.0.0"
        )
        llm_client = MagicMock()
        generated = _make_generated_answer(content="partial", finish_reason="error")
        generated.error_type = "api_error"
        llm_client.generate.return_value = generated
        post_processor = MagicMock()

        orchestrator = _make_orchestrator(llm_client, prompt_builder, post_processor)
        response = orchestrator.generate(_make_request())

        assert response.error_type == "api_error"


class TestPostProcessingFailure:
    def test_post_processor_exception_returns_error_response_preserving_generated(self):
        prompt_builder = MagicMock()
        prompt_builder.build.return_value = MagicMock(
            system_prompt="s", user_prompt="u", template_id="x", template_version="1.0.0"
        )
        llm_client = MagicMock()
        generated = _make_generated_answer()
        llm_client.generate.return_value = generated
        post_processor = MagicMock()
        post_processor.process.side_effect = RuntimeError("citation extraction blew up")

        orchestrator = _make_orchestrator(llm_client, prompt_builder, post_processor)
        response = orchestrator.generate(_make_request())

        assert response.answer == ""
        assert any("Post-processing failed" in w for w in response.warnings)
        assert response.usage == generated.usage


class TestRequestAndGenerationIds:
    def test_generation_id_is_an_8_char_uuid_prefix(self):
        prompt_builder = MagicMock()
        prompt_builder.build.return_value = MagicMock(
            system_prompt="s", user_prompt="u", template_id="x", template_version="1.0.0"
        )
        llm_client = MagicMock()
        llm_client.generate.return_value = _make_generated_answer()
        post_processor = MagicMock()

        orchestrator = _make_orchestrator(llm_client, prompt_builder, post_processor)
        orchestrator.generate(_make_request())

        _args, kwargs = post_processor.process.call_args
        assert len(kwargs["generation_id"]) == 8

    def test_request_id_passed_through_unchanged(self):
        orchestrator = _make_orchestrator()
        response = orchestrator.generate(_make_request(mode=GenerationMode.SIMPLE_EXPLANATION))
        assert response.request_id == "req-1"
