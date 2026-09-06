"""``ModelManager`` — model download lifecycle above the Ollama HTTP API
(Phase 1 Step 1.6).

Responsibilities:

- ``download_model`` — stream Ollama's ``/api/pull``, reporting
  ``{percent, speed_mbps, eta_seconds}`` via a callback, with the roadmap's
  **three-state interruption guarantee** (see ``_run_download``).
- ``verify_model_integrity`` — model present in ``/api/tags`` *and*
  ``/api/show`` returns details.
- ``switch_active_model`` — write the active model to the app-config file
  (atomic); takes effect for the next ``ModelInferenceRouter.generate()`` with
  no backend restart.
- ``get_model_catalog`` — the bundled JSON catalog (never network).

**Testability.** The pull loop is driven by an injected ``PullStreamer``; unit
tests pass a scripted fake and never touch a real daemon. The real
``HttpPullStreamer`` is the default.

**Composition-root wiring** — share one in-flight-download set so
``OllamaManager.get_model_status`` can report ``DOWNLOADING``::

    tracker: set[str] = set()
    om = OllamaManager(host, active_downloads=tracker)
    mm = ModelManager(om, active_downloads=tracker)

**Cleanup honesty.** Over HTTP the only cleanup lever is ``DELETE /api/delete``;
we cannot reach into ``~/.ollama/models/blobs``. Ollama's own pull reconciles
and garbage-collects orphaned partial blobs on the next pull, so
``/api/delete`` followed by a fresh ``/api/pull`` is what satisfies "no partial
artifacts left on disk without a clear status". Every terminal
``ModelDownloadError`` is raised *after* ``_safe_delete`` has run.
"""

from __future__ import annotations

import json
import logging
import math
import re
import shutil
import time
from collections import deque
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

import requests

from src.models.app_config import AppConfig
from src.models.catalog import load_model_catalog
from src.models.ollama_manager import OllamaManager, normalize_model_name
from src.models.types import (
    DownloadProgress,
    DownloadResult,
    ModelCatalogEntry,
    ModelDownloadError,
)

logger = logging.getLogger(__name__)


class PullInterrupted(Exception):
    """The pull stream ended before ``success`` — a mid-download network loss.
    Recoverable: the next ``pull()`` call resumes from the last completed
    layer (Ollama does this natively)."""


class _DiskFull(Exception):
    """Internal — a pull error event indicating no space left on device."""


class _CallbackError(Exception):
    """Internal — the caller's ``progress_callback`` raised. Treated as a
    terminal failure so ``_run_download`` still cleans up (Phase 1 audit F1:
    a raising callback must not leave a partial model on disk)."""


class _LayerInconsistent(Exception):
    """Internal — layer state can't be trusted (bad digest, corrupt blob, or a
    reported success that fails integrity verification). Triggers a clean
    delete-and-restart-from-zero."""


# Disk-full is detected by matching the pull error text — Ollama surfaces the OS
# ENOSPC message rather than a structured code (Phase 1 audit F3: broadened past
# the 3 exact spec strings).
_DISK_FULL_RE = re.compile(
    r"no space left|enospc|not enough\b.*\bspace|\bdisk\b.{0,6}\bfull\b|out of disk",
    re.IGNORECASE,
)


class PullStreamer(Protocol):
    """Yields Ollama's ``/api/pull`` NDJSON status dicts. Raises
    ``PullInterrupted`` if the stream drops mid-download."""

    def pull(self, model_name: str) -> Iterator[dict]: ...


class HttpPullStreamer:
    """Real ``PullStreamer`` — streams ``POST /api/pull`` over ``requests``."""

    def __init__(
        self,
        host: str,
        *,
        connect_timeout: float = 10.0,
        read_timeout: float = 300.0,
    ):
        self._host = host.rstrip("/")
        self._timeout = (connect_timeout, read_timeout)

    def pull(self, model_name: str) -> Iterator[dict]:
        try:
            resp = requests.post(
                f"{self._host}/api/pull",
                json={"name": model_name, "stream": True},
                stream=True,
                timeout=self._timeout,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            raise PullInterrupted(str(exc)) from exc

        if resp.status_code != 200:
            try:
                body = resp.json()
            except ValueError:
                body = {"error": f"pull failed with HTTP {resp.status_code}"}
            yield body if isinstance(body, dict) else {"error": str(body)}
            return

        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            raise PullInterrupted(str(exc)) from exc


class ModelManager:
    _MAX_ATTEMPTS = 4  # hard ceiling on total outer-loop iterations, any cause
    _MAX_RESTARTS = 1  # sub-limit on clean-restart-from-zero specifically
    _SPEED_WINDOW_S = 5.0
    _RESTART_MESSAGE = "Download interrupted — restarting from the beginning."

    def __init__(
        self,
        ollama: OllamaManager,
        *,
        app_config_path: str | None = None,
        pull_streamer: PullStreamer | None = None,
        catalog_path: str | None = None,
        disk_check_dir: str | None = None,
        active_downloads: set[str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._ollama = ollama
        self._app_config_path = app_config_path
        self._catalog_path = catalog_path
        self._disk_check_dir = str(disk_check_dir) if disk_check_dir else str(Path.home())
        self._active_downloads = active_downloads if active_downloads is not None else set()
        self._pull_streamer = pull_streamer or HttpPullStreamer(ollama.host)
        self._clock = clock

    # ------------------------------------------------------------------
    # Catalog / integrity / switching
    # ------------------------------------------------------------------

    def get_model_catalog(self) -> list[ModelCatalogEntry]:
        return load_model_catalog(self._catalog_path)

    def verify_model_integrity(self, model_name: str) -> bool:
        target = normalize_model_name(model_name)
        installed = {normalize_model_name(m.name) for m in self._ollama.get_installed_models()}
        if target not in installed:
            return False
        return self._ollama.show_model(model_name) is not None

    def switch_active_model(self, model_name: str) -> None:
        """Point the app-config file at ``model_name``. Raises if it isn't
        installed and verified."""
        if not self.verify_model_integrity(model_name):
            raise ModelDownloadError(
                f"Can't switch to '{model_name}' — it isn't installed. Download it first."
            )
        AppConfig.load(self._app_config_path).set_active_model(model_name)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_model(
        self,
        model_name: str,
        progress_callback: Callable[[DownloadProgress], None],
    ) -> DownloadResult:
        # Track under the normalized (tagged) name so get_model_status reports
        # DOWNLOADING regardless of whether the query uses the bare or tagged
        # form (Phase 1 audit F2).
        tracked = normalize_model_name(model_name)
        self._active_downloads.add(tracked)
        try:
            return self._run_download(model_name, progress_callback)
        finally:
            self._active_downloads.discard(tracked)

    @staticmethod
    def _emit(
        progress_callback: Callable[[DownloadProgress], None],
        progress: DownloadProgress,
        *,
        critical: bool = True,
    ) -> None:
        """Call the caller's progress_callback. A raise from it during the
        download (``critical``) becomes ``_CallbackError`` so ``_run_download``
        cleans up; a raise after the model is already complete is swallowed
        (the download succeeded — a UI callback bug must not undo it)."""
        try:
            progress_callback(progress)
        except Exception as exc:
            if critical:
                raise _CallbackError(str(exc)) from exc
            logger.warning("progress_callback raised after completion: %s", exc)

    def _run_download(
        self,
        model_name: str,
        progress_callback: Callable[[DownloadProgress], None],
    ) -> DownloadResult:
        """The three-state guarantee. On every exit the user has seen exactly
        one of:

        (a) a complete, integrity-verified model (``DownloadResult`` returned);
        (b) a clean restart from zero, announced with ``_RESTART_MESSAGE``;
        (c) a specific, actionable ``ModelDownloadError`` — after cleanup.

        Never an ambiguous partial state, including when the caller's
        ``progress_callback`` itself raises (Phase 1 audit F1).
        """
        try:
            return self._run_download_inner(model_name, progress_callback)
        except _CallbackError as exc:
            self._safe_delete(model_name)
            raise ModelDownloadError(
                "The download was stopped by the app before it finished. Any partial "
                "data has been cleaned up — try again."
            ) from exc

    def _run_download_inner(
        self,
        model_name: str,
        progress_callback: Callable[[DownloadProgress], None],
    ) -> DownloadResult:
        """The retry loop. ``attempts`` is a single counter incremented on every
        iteration (restart or resume); ``_MAX_ATTEMPTS`` bounds the whole loop
        regardless of the failure mix."""
        restarts = 0
        resumes = 0
        attempts = 0

        while attempts < self._MAX_ATTEMPTS:
            attempts += 1
            try:
                self._stream_once(model_name, progress_callback)
                if not self.verify_model_integrity(model_name):
                    raise _LayerInconsistent("integrity check failed after reported success")
            except _DiskFull as exc:
                self._safe_delete(model_name)
                raise ModelDownloadError(self._disk_full_message(model_name)) from exc
            except ModelDownloadError:
                # Non-retryable (e.g. unknown model name) — clean up, re-raise.
                self._safe_delete(model_name)
                raise
            except _LayerInconsistent as exc:
                self._safe_delete(model_name)
                # Raise (don't announce a restart we won't get to attempt) when
                # we're out of restarts OR out of attempts — otherwise the user
                # sees "restarting…" immediately followed by a failure message
                # (Phase 1 audit C2).
                if restarts >= self._MAX_RESTARTS or attempts >= self._MAX_ATTEMPTS:
                    raise ModelDownloadError(
                        f"Couldn't download '{model_name}' — the download kept failing "
                        "verification. Please try again later."
                    ) from exc
                restarts += 1
                self._emit(
                    progress_callback,
                    DownloadProgress(
                        phase="restarting", percent=0.0, message=self._RESTART_MESSAGE
                    ),
                )
                continue
            except PullInterrupted:
                if attempts >= self._MAX_ATTEMPTS:
                    break
                resumes += 1
                self._emit(
                    progress_callback,
                    DownloadProgress(
                        phase="downloading", message="Connection lost — resuming download."
                    ),
                )
                continue

            # Success reported and integrity verified. A callback failure here
            # must NOT delete the good model, so this emit is non-critical.
            self._emit(
                progress_callback,
                DownloadProgress(phase="complete", percent=100.0),
                critical=False,
            )
            return DownloadResult(
                model_name=model_name,
                status="restarted_then_complete" if restarts else "complete",
                restarts=restarts,
                resumes=resumes,
                verified=True,
            )

        self._safe_delete(model_name)
        raise ModelDownloadError(
            "Download interrupted and couldn't be resumed. Check your connection and try again."
        )

    def _stream_once(
        self,
        model_name: str,
        progress_callback: Callable[[DownloadProgress], None],
    ) -> None:
        """Consume one ``pull()`` stream. Returns on a ``success`` event; raises
        ``_DiskFull`` / ``_LayerInconsistent`` / ``ModelDownloadError`` on an
        error event; raises ``PullInterrupted`` if the stream ends without
        ``success``."""
        layers: dict[str, tuple[int, int]] = {}
        samples: deque[tuple[float, int]] = deque(maxlen=128)

        for event in self._pull_streamer.pull(model_name):
            if event.get("error"):
                self._raise_for_error(str(event["error"]), model_name)

            status = str(event.get("status", ""))
            if status == "success":
                return
            if status.startswith("error"):
                self._raise_for_error(status, model_name)

            if status == "pulling manifest":
                self._emit(progress_callback, DownloadProgress(phase="manifest"))
            elif status.startswith("downloading") or ("total" in event and "completed" in event):
                digest = str(event.get("digest") or status)
                total = int(event.get("total", 0) or 0)
                completed = int(event.get("completed", 0) or 0)
                if total > 0:
                    layers[digest] = (completed, total)
                sum_c = sum(c for c, _ in layers.values())
                sum_t = sum(t for _, t in layers.values())
                percent = 100.0 * sum_c / sum_t if sum_t else 0.0
                samples.append((self._clock(), sum_c))
                speed, eta = self._speed_and_eta(samples, sum_c, sum_t)
                self._emit(
                    progress_callback,
                    DownloadProgress(
                        phase="downloading",
                        percent=round(percent, 2),
                        speed_mbps=speed,
                        eta_seconds=eta,
                    ),
                )
            elif status.startswith("verifying"):
                self._emit(progress_callback, DownloadProgress(phase="verifying"))
            # "writing manifest" / "removing any unused layers" / unknown -> ignore

        raise PullInterrupted("pull stream ended before a success event")

    def _raise_for_error(self, message: str, model_name: str) -> None:
        low = message.lower()
        if _DISK_FULL_RE.search(low):
            raise _DiskFull(message)
        if (
            "not found" in low
            or "does not exist" in low
            or "unknown model" in low
            or "file does not exist" in low
        ):
            raise ModelDownloadError(
                f"'{model_name}' isn't a known model — check the name and try again."
            )
        raise _LayerInconsistent(message)

    def _speed_and_eta(
        self,
        samples: deque[tuple[float, int]],
        sum_c: int,
        sum_t: int,
    ) -> tuple[float, float | None]:
        if len(samples) < 2:
            return 0.0, None
        now = samples[-1][0]
        window = [s for s in samples if now - s[0] <= self._SPEED_WINDOW_S]
        if len(window) < 2:
            window = list(samples)[-2:]
        (t0, c0), (t1, c1) = window[0], window[-1]
        dt = t1 - t0
        if dt <= 0 or c1 <= c0:
            return 0.0, None
        bytes_per_s = (c1 - c0) / dt
        speed_mbps = bytes_per_s / 1_000_000
        remaining = max(sum_t - sum_c, 0)
        eta = remaining / bytes_per_s if bytes_per_s > 0 else None
        return round(speed_mbps, 2), (round(eta, 1) if eta is not None else None)

    def _disk_full_message(self, model_name: str) -> str:
        entry = next(
            (
                e
                for e in self.get_model_catalog()
                if normalize_model_name(e.name) == normalize_model_name(model_name)
            ),
            None,
        )
        free: int | None = None
        try:
            free = shutil.disk_usage(self._disk_check_dir).free
        except OSError:
            pass
        if entry and entry.size_bytes and free is not None:
            shortfall = entry.size_bytes - free
            if shortfall > 0:
                gb = math.ceil(shortfall / 1_000_000_000)
                return f"Not enough disk space — free {gb} GB and try again."
        return "Not enough disk space — free up space and try again."

    def _safe_delete(self, model_name: str) -> None:
        try:
            self._ollama.delete_model(model_name)
        except Exception as exc:  # best-effort cleanup — never mask the real error
            logger.warning("cleanup delete of %s failed: %s", model_name, exc)
