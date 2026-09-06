"""
Generation Orchestrator (Phase 1 Step 1.4 — session companion).

Single entry point for the generation layer. Composes:

    PromptBuilder → LLMClient → PostProcessor → GenerationResponse

Owns request lifecycle (ID generation, timing, error handling), configuration
loading, and mode-config resolution. Context assembly is delegated to
``ContextBuilder`` inside ``PromptBuilder`` — the orchestrator no longer picks
an assembly strategy.

Usage:
    orchestrator = GenerationOrchestrator()
    response = orchestrator.generate(request)
"""

import logging
import time
import uuid
from typing import Any

from dotenv import load_dotenv

from observability.tracing import safe_dict, traced_span
from src.common.llm_client import LLMClient
from src.generation.config import (
    AnswerFormat,
    GenerationConfig,
    GenerationRequest,
    GenerationResponse,
    ModelConfig,
)
from src.generation.post_processor import PostProcessor
from src.generation.prompt_builder import PromptBuilder

load_dotenv()

logger = logging.getLogger(__name__)


class GenerationOrchestrator:
    """
    Single entry point for the generation layer.

    Composes PromptBuilder, LLMClient, and PostProcessor into a single
    generate() call. Owns request lifecycle, ID generation, timing, error
    handling, and configuration.
    """

    def __init__(
        self,
        config: GenerationConfig | None = None,
        config_dir: str | None = None,
        prompt_builder: PromptBuilder | None = None,
        llm_client: LLMClient | None = None,
        post_processor: PostProcessor | None = None,
    ):
        self.config = config or GenerationConfig.from_yaml(config_dir)
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.llm_client = llm_client or LLMClient()
        self.post_processor = post_processor or PostProcessor(config=self.config.post_processing)

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """
        Generate an answer from retrieved context.

        This is the single public method for the generation layer.
        """
        # One span wrapping the whole method so prompt_builder/llm_generate/
        # post_processor nest under a single trace instead of each starting
        # its own (sibling spans with no shared parent don't share a trace id).
        with traced_span(
            "generate",
            input=safe_dict(
                {
                    "request_id": request.request_id,
                    "query": request.query,
                    "mode": request.mode,
                    "chunk_count": len(request.chunks),
                }
            ),
        ) as span:
            response = self._generate_impl(request)
            if response.error_type:
                span.update(
                    output=safe_dict(response), level="ERROR", status_message=response.error_type
                )
            elif response.warnings:
                span.update(
                    output=safe_dict(response),
                    level="WARNING",
                    status_message="; ".join(response.warnings),
                )
            else:
                span.update(output=safe_dict(response))
            return response

    def _generate_impl(self, request: GenerationRequest) -> GenerationResponse:
        """generate()'s body, extracted so the tracing span wraps every phase
        without duplicating span setup at each early-return error path."""
        total_start = time.time()
        generation_id = str(uuid.uuid4())[:8]
        warnings: list[str] = []

        # ── Step 1: Resolve mode and model configuration ────────────
        try:
            mode_config = self.config.get_mode_config(request.mode)
            model_config = self.config.get_model_config(mode_config.model_key)
        except Exception as e:
            logger.error(f"Configuration resolution failed: {e}")
            return self._build_error_response(
                request=request,
                generation_id=generation_id,
                model_config=None,
                warnings=[f"Configuration resolution failed: {str(e)}"],
                total_start=total_start,
            )

        # ── Step 2: Build prompt ────────────────────────────────────
        prompt_start = time.time()
        with traced_span(
            "prompt_builder",
            input=safe_dict({"query": request.query, "mode": request.mode}),
        ) as prompt_span:
            try:
                prompt = self.prompt_builder.build(request, mode_config)
            except Exception as e:
                logger.error(f"Prompt building failed: {e}")
                prompt_span.update(level="ERROR", status_message=str(e))
                return self._build_error_response(
                    request=request,
                    generation_id=generation_id,
                    model_config=model_config,
                    warnings=[f"Prompt building failed: {str(e)}"],
                    total_start=total_start,
                )
            prompt_span.update(output=safe_dict(prompt))
        prompt_time = (time.time() - prompt_start) * 1000

        # ── Step 3: Generate answer ─────────────────────────────────
        gen_start = time.time()
        with traced_span(
            "llm_generate",
            as_type="generation",
            input=safe_dict(
                {"system_prompt": prompt.system_prompt, "user_prompt": prompt.user_prompt}
            ),
            model=model_config.model_name,
            metadata={
                "prompt_id": prompt.template_id,
                "prompt_version": prompt.template_version,
                "provider": model_config.provider,
            },
        ) as gen_span:
            try:
                generated = self.llm_client.generate(prompt, model_config)
            except Exception as e:
                logger.error(f"LLM generation failed: {e}")
                gen_span.update(level="ERROR", status_message=str(e))
                return self._build_error_response(
                    request=request,
                    generation_id=generation_id,
                    model_config=model_config,
                    warnings=[f"LLM generation failed: {str(e)}"],
                    total_start=total_start,
                )

            gen_span.update(
                output=safe_dict(generated.content),
                usage_details={
                    "input": generated.usage.input_tokens,
                    "output": generated.usage.output_tokens,
                    "total": generated.usage.total_tokens,
                },
                # Local Ollama inference has no per-token $ cost — the
                # latency-proxy replaces the retired deepseek cost_usd span
                # payload (Phase 1 Step 1.4; audit C4). Also persisted on
                # PipelineCallMetrics.compute_ms via MetricsStore.
                metadata={"compute_ms": round((time.time() - gen_start) * 1000, 2)},
            )
            if generated.finish_reason == "error":
                gen_span.update(level="ERROR", status_message=generated.error_type)
        gen_time = (time.time() - gen_start) * 1000

        # Check for empty response
        if not generated.content or generated.finish_reason == "error":
            warnings.append(f"LLM returned empty or error response: {generated.finish_reason}")
            return self._build_error_response(
                request=request,
                generation_id=generation_id,
                model_config=model_config,
                warnings=warnings,
                total_start=total_start,
                generated=generated,
            )

        # ── Step 4: Post-process ────────────────────────────────────
        pp_start = time.time()
        with traced_span(
            "post_processor",
            input=safe_dict({"chunk_count": len(request.chunks), "mode": request.mode}),
        ) as pp_span:
            try:
                response = self.post_processor.process(
                    generated=generated,
                    chunks=request.chunks,
                    retrieval_metadata=request.retrieval_metadata,
                    mode=request.mode,
                    request_id=request.request_id,
                    generation_id=generation_id,
                    warnings=warnings,
                    sources_used=[],
                )
            except Exception as e:
                logger.error(f"Post-processing failed: {e}")
                pp_span.update(level="ERROR", status_message=str(e))
                return self._build_error_response(
                    request=request,
                    generation_id=generation_id,
                    model_config=model_config,
                    warnings=warnings + [f"Post-processing failed: {str(e)}"],
                    total_start=total_start,
                    generated=generated,
                )
            pp_span.update(
                output=safe_dict(
                    {
                        "is_grounded": response.is_grounded,
                        "grounding_confidence": response.grounding_confidence,
                        "citations_count": len(response.citations),
                        "warnings": response.warnings,
                    }
                )
            )
        pp_time = (time.time() - pp_start) * 1000

        # ── Step 5: Set timing / versions ───────────────────────────
        total_time = (time.time() - total_start) * 1000

        response.generation_time_ms = generated.generation_time_ms
        response.total_time_ms = round(total_time, 2)
        response.prompt_version = prompt.template_version
        response.config_version = self.config.config_version

        logger.info(
            f"Generation complete: mode={request.mode.value}, "
            f"model={model_config.model_name}, "
            f"tokens={generated.usage.total_tokens}, "
            f"grounded={response.is_grounded}, "
            f"citations={len(response.citations)}, "
            f"total_time={total_time:.0f}ms "
            f"(prompt={prompt_time:.0f}ms, gen={gen_time:.0f}ms, pp={pp_time:.0f}ms)"
        )

        return response

    def _build_error_response(
        self,
        request: GenerationRequest,
        generation_id: str,
        model_config: ModelConfig | None,
        warnings: list[str],
        total_start: float,
        generated: Any | None = None,
    ) -> GenerationResponse:
        """
        Build a GenerationResponse for error cases.

        model_config may be None if the error occurred before mode/model
        configuration could be resolved.
        """
        from src.generation.config import ModelInfo, UsageStats

        total_time = (time.time() - total_start) * 1000

        if model_config is not None:
            model_info = model_config.to_model_info()
        else:
            model_info = ModelInfo(provider="unknown", model_name="unknown")

        return GenerationResponse(
            answer="",
            mode=request.mode,
            answer_format=AnswerFormat.PLAIN_TEXT,
            citations=[],
            is_grounded=False,
            grounding_confidence=0.0,
            retrieval_metadata=request.retrieval_metadata,
            model_info=model_info,
            usage=generated.usage if generated else UsageStats(),
            generation_time_ms=generated.generation_time_ms if generated else 0,
            total_time_ms=round(total_time, 2),
            request_id=request.request_id,
            generation_id=generation_id,
            warnings=warnings,
            sources_used=[],
            error_type=generated.error_type if generated else None,
            retry_after_seconds=generated.retry_after_seconds if generated else None,
            is_daily_quota=generated.is_daily_quota if generated else False,
        )
