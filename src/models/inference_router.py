"""``ModelInferenceRouter`` — the app-config-authoritative front door for
generation (Phase 1 Step 1.6).

The existing inference callers (``simple_generate``, ``RetrievalAgent``,
``GenerationConfig``, the feature handlers) still read ``$OLLAMA_DEFAULT_MODEL``
directly; wiring them through this router is the Phase 3 orchestrator's job
(mirroring the Step 1.5 decision to defer the ``AgenticOutput`` dispatcher).
This component exists now so the routing seam, the first-launch gate, and the
"switch takes effect with no backend restart" guarantee are all in place and
tested.

First-launch authority: the app-config ``active_model`` is the *only* source
of truth here. ``$OLLAMA_DEFAULT_MODEL`` being set does **not** satisfy the
gate — ``generate()`` returns ``error_code="no_model_active"`` until a model is
activated (project_logic.md §8).
"""

from __future__ import annotations

import logging

from src.common.llm_client import (
    ModelNotDownloadedError,
    ModelNotLoadedError,
    OllamaNotRunningError,
    ProviderRegistry,
    simple_generate,
)
from src.models.app_config import AppConfig
from src.models.types import InferenceResult

logger = logging.getLogger(__name__)


class ModelInferenceRouter:
    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        app_config_path: str | None = None,
        temperature: float = 0.3,
        max_output_tokens: int = 2048,
        timeout_seconds: int = 120,
    ):
        # 120s default: a CPU 7B/8B model call routinely needs 40-90s (Phase 1
        # Step 1.4b calibration finding).
        self._registry = registry
        self._app_config_path = app_config_path
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds

    def active_model(self) -> str | None:
        """The currently-active model, re-read from the app-config file on
        every call — this is what lets ``ModelManager.switch_active_model``
        take effect for the next ``generate()`` with no restart."""
        return AppConfig.load(self._app_config_path).active_model

    def generate(self, prompt: str, *, model_name: str | None = None) -> InferenceResult:
        """Route a single-turn generation to the active model.

        An explicit ``model_name`` **bypasses the first-launch gate by
        design** — it is the Phase 3 orchestrator's escape hatch to route one
        operation to a specific model, not a user-facing path. It therefore
        makes ``generate(model_name=...)`` and ``model_setup_required()``
        intentionally inconsistent. The normal path passes no ``model_name``
        and is fully gated.

        Always returns a structured ``InferenceResult`` — the provider
        exceptions from ``src/common/llm_client.py`` are mapped to specific,
        user-readable messages, never surfaced as a stack trace.
        """
        model = model_name or self.active_model()
        if not model:
            return InferenceResult(
                ok=False,
                error_code="no_model_active",
                message="No AI model is set up yet. Choose one in Settings → Models to get started.",
            )

        try:
            text = simple_generate(
                prompt,
                model_name=model,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                timeout_seconds=self._timeout_seconds,
                registry=self._registry,
            )
        except OllamaNotRunningError:
            return InferenceResult(
                ok=False,
                model_name=model,
                error_code="ollama_not_running",
                message="The local AI service isn't running. Restart the app and try again.",
            )
        except ModelNotDownloadedError:
            return InferenceResult(
                ok=False,
                model_name=model,
                error_code="model_not_downloaded",
                message=f"The model '{model}' isn't installed. Download it in Settings → Models.",
            )
        except ModelNotLoadedError:
            return InferenceResult(
                ok=False,
                model_name=model,
                error_code="model_not_loaded",
                message=f"'{model}' is still loading. Try again in a moment.",
            )
        except Exception as exc:
            # Last-resort mapping: ProviderTimeoutError / ProviderAPIError /
            # ProviderCredentialError and anything unforeseen all become one
            # user-readable result rather than a raised exception.
            logger.warning("inference via %s failed: %s", model, exc)
            return InferenceResult(
                ok=False,
                model_name=model,
                error_code="inference_failed",
                message="Something went wrong generating a response. Please try again.",
            )

        return InferenceResult(ok=True, text=text, model_name=model)
