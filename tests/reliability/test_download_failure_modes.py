"""Reliability — model-download failure modes (Phase 4 Step 4.4a).

Raises the Step 1.6 unit assertions to the roadmap's Step 4.4 acceptance
wording (production_roadmap.md):

  * "Disk-full during download produces the correct specific error message"
    + "clean state (no partial download artifacts)"
  * "Network-loss during download produces a clean, resume-or-restart state
    with no partial artifacts"

The scripted ``PullStreamer`` + ``FakeOllama`` from ``tests/models`` do the
work; this file just pins the end-to-end contract in one place.
"""

from __future__ import annotations

import re

import pytest

from src.models.model_manager import ModelDownloadError
from tests.models.test_model_manager import (
    MODEL,
    FakeOllama,
    FakePullStreamer,
    app_cfg,  # noqa: F401 — pytest fixture
    dl,
    make_mm,
)

pytestmark = pytest.mark.reliability


def _assert_no_partial_artifacts(mm, ollama) -> None:
    """A failed download must leave nothing behind: the model isn't installed,
    ``/api/delete`` was issued for it, and the in-flight tracker is clear."""
    assert ollama.delete_calls and ollama.delete_calls[-1] == MODEL
    assert MODEL not in mm._active_downloads
    assert mm.verify_model_integrity(MODEL) is False


# ------------------------------------------------------------------
# Disk-full
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "err_text",
    [
        "write /root/.ollama/blobs/sha256-x: no space left on device",
        "not enough disk space available on the target drive",
        "the target disk is full",
    ],
)
def test_disk_full_exact_message_and_clean_state(app_cfg, err_text):  # noqa: F811
    streamer = FakePullStreamer([[{"status": "error", "error": err_text}]])
    # not-installed ollama so verify_model_integrity is honest afterwards
    mm, ollama = make_mm(streamer, app_cfg, ollama=FakeOllama(installed=set()))

    with pytest.raises(ModelDownloadError) as ei:
        mm.download_model(MODEL, lambda _p: None)

    assert re.fullmatch(r"Not enough disk space — free .+ and try again\.", str(ei.value))
    _assert_no_partial_artifacts(mm, ollama)


# ------------------------------------------------------------------
# Network-loss — resume first, then a clean restart, never a partial state
# ------------------------------------------------------------------


def test_network_loss_resumes_from_the_last_layer_without_a_restart(app_cfg):  # noqa: F811
    # stream drops mid-layer, then the retry (Ollama's native resume) completes
    streamer = FakePullStreamer(
        [
            [dl(0, 100), dl(40, 100), FakePullStreamer.INTERRUPT],
            [dl(60, 100), dl(100, 100), {"status": "success"}],
        ]
    )
    mm, _ = make_mm(streamer, app_cfg, ollama=FakeOllama(installed={MODEL}))

    events: list = []
    result = mm.download_model(MODEL, events.append)

    assert result.resumes == 1 and result.restarts == 0
    assert [e.phase for e in events].count("restarting") == 0  # resume != restart
    assert mm.verify_model_integrity(MODEL) is True


def test_network_loss_that_cannot_resume_ends_clean_no_partial_artifacts(app_cfg):  # noqa: F811
    # every attempt drops before success and never reports a resumable layer —
    # the loop exhausts _MAX_ATTEMPTS and must clean up.
    streamer = FakePullStreamer([[FakePullStreamer.INTERRUPT]] * 8)
    mm, ollama = make_mm(streamer, app_cfg, ollama=FakeOllama(installed=set()))

    with pytest.raises(ModelDownloadError) as ei:
        mm.download_model(MODEL, lambda _p: None)

    assert "connection" in str(ei.value).lower() or "resume" in str(ei.value).lower()
    _assert_no_partial_artifacts(mm, ollama)


def test_layer_inconsistency_triggers_one_clean_restart_then_gives_up_clean(app_cfg):  # noqa: F811
    # a reported success that fails the integrity check → delete + restart from
    # zero once, then (still bad) a clean terminal failure.
    streamer = FakePullStreamer(
        [
            [dl(100, 100), {"status": "success"}],
            [dl(100, 100), {"status": "success"}],
        ]
    )
    mm, ollama = make_mm(streamer, app_cfg, ollama=FakeOllama(installed=set(), show_ok=False))

    with pytest.raises(ModelDownloadError):
        mm.download_model(MODEL, lambda _p: None)

    _assert_no_partial_artifacts(mm, ollama)
