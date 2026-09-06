"""Unit tests for src/models/model_manager.py (Phase 1 Step 1.6).

The pull loop is driven by a scripted fake ``PullStreamer`` and a fake
``OllamaManager`` — no real daemon, no network. The resume path and the
clean-restart path are exercised in separate tests (roadmap requirement).
"""

import re

import pytest

from src.models.catalog import load_model_catalog
from src.models.model_manager import ModelManager, PullInterrupted
from src.models.types import DownloadProgress, ModelDownloadError

pytestmark = pytest.mark.unit

MODEL = "llama3.1:8b"


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class FakeClock:
    def __init__(self, step: float = 1.0):
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


class FakePullStreamer:
    INTERRUPT = "__interrupt__"

    def __init__(self, scripts):
        self._scripts = [list(s) for s in scripts]
        self.calls = 0

    def pull(self, model_name):
        assert self.calls < len(
            self._scripts
        ), f"pull() called {self.calls + 1}x, only {len(self._scripts)} scripts provided"
        script = self._scripts[self.calls]
        self.calls += 1
        for item in script:
            if item == self.INTERRUPT:
                raise PullInterrupted("scripted mid-stream drop")
            yield item


class FakeOllama:
    host = "http://fake:11434"

    def __init__(self, *, installed=None, show_ok=True):
        self.installed = set(installed or ())
        self.show_ok = show_ok
        self.delete_calls: list[str] = []

    def get_installed_models(self):
        from src.models.types import InstalledModel

        return [InstalledModel(name=n, size_bytes=1) for n in self.installed]

    def show_model(self, name):
        return {"details": {}} if self.show_ok else None

    def delete_model(self, name):
        self.delete_calls.append(name)
        return True


def dl(completed: int, total: int, digest: str = "sha256:layer1") -> dict:
    return {
        "status": f"downloading {digest}",
        "digest": digest,
        "total": total,
        "completed": completed,
    }


@pytest.fixture
def app_cfg(tmp_path, monkeypatch):
    path = tmp_path / "app_config.json"
    monkeypatch.setenv("RAGPIPE_APP_CONFIG_PATH", str(path))
    monkeypatch.delenv("RAGPIPE_MODEL_CATALOG", raising=False)
    return path


def make_mm(streamer, app_cfg, *, ollama=None):
    ollama = ollama or FakeOllama(installed={MODEL}, show_ok=True)
    return (
        ModelManager(
            ollama,
            pull_streamer=streamer,
            clock=FakeClock(),
            app_config_path=str(app_cfg),
        ),
        ollama,
    )


# --------------------------------------------------------------------------
# 1. Clean download
# --------------------------------------------------------------------------


def test_clean_download_streams_progress(app_cfg):
    streamer = FakePullStreamer(
        [
            [
                {"status": "pulling manifest"},
                dl(0, 100_000_000),
                dl(50_000_000, 100_000_000),
                dl(100_000_000, 100_000_000),
                {"status": "verifying sha256 digest"},
                {"status": "writing manifest"},
                {"status": "success"},
            ]
        ]
    )
    mm, ollama = make_mm(streamer, app_cfg)
    events: list[DownloadProgress] = []

    result = mm.download_model(MODEL, events.append)

    phases = [e.phase for e in events]
    assert phases[0] == "manifest"
    assert "verifying" in phases
    assert phases[-1] == "complete"

    downloads = [e for e in events if e.phase == "downloading"]
    assert [e.percent for e in downloads] == [0.0, 50.0, 100.0]
    assert downloads[1].speed_mbps == 50.0
    assert downloads[1].eta_seconds == 1.0

    assert result.status == "complete"
    assert result.verified is True
    assert result.restarts == 0 and result.resumes == 0
    assert ollama.delete_calls == []
    assert MODEL not in mm._active_downloads


# --------------------------------------------------------------------------
# 2. Resume path (separate from restart)
# --------------------------------------------------------------------------


def test_network_loss_resumes_without_restart(app_cfg):
    streamer = FakePullStreamer(
        [
            [
                {"status": "pulling manifest"},
                dl(0, 100_000_000),
                dl(30_000_000, 100_000_000),
                FakePullStreamer.INTERRUPT,
            ],
            [
                dl(30_000_000, 100_000_000),
                dl(100_000_000, 100_000_000),
                {"status": "success"},
            ],
        ]
    )
    mm, ollama = make_mm(streamer, app_cfg)
    events: list[DownloadProgress] = []

    result = mm.download_model(MODEL, events.append)

    assert result.resumes == 1
    assert result.restarts == 0
    assert result.status == "complete"
    assert ollama.delete_calls == []
    assert all(e.phase != "restarting" for e in events)
    assert streamer.calls == 2


# --------------------------------------------------------------------------
# 3. Clean-restart path (separate from resume)
# --------------------------------------------------------------------------


def test_inconsistent_layer_triggers_clean_restart(app_cfg):
    streamer = FakePullStreamer(
        [
            [
                {"status": "pulling manifest"},
                dl(0, 100_000_000),
                dl(30_000_000, 100_000_000),
                FakePullStreamer.INTERRUPT,
            ],
            [
                {
                    "status": "error",
                    "error": "invalid digest sha256:deadbeef, expected sha256:cafe",
                },
            ],
            [
                {"status": "pulling manifest"},
                dl(0, 100_000_000),
                dl(100_000_000, 100_000_000),
                {"status": "success"},
            ],
        ]
    )
    mm, ollama = make_mm(streamer, app_cfg)
    events: list[DownloadProgress] = []

    result = mm.download_model(MODEL, events.append)

    assert result.restarts == 1
    assert result.status == "restarted_then_complete"
    assert result.verified is True
    assert ollama.delete_calls == [MODEL]

    restart_events = [e for e in events if e.phase == "restarting"]
    assert len(restart_events) == 1
    assert restart_events[0].message == "Download interrupted — restarting from the beginning."


# --------------------------------------------------------------------------
# 4. Disk full
# --------------------------------------------------------------------------


def test_disk_full_specific_message_and_cleanup(app_cfg):
    streamer = FakePullStreamer(
        [
            [
                {
                    "status": "error",
                    "error": "write /root/.ollama/blobs/sha256-x: no space left on device",
                }
            ]
        ]
    )
    mm, ollama = make_mm(streamer, app_cfg)

    with pytest.raises(ModelDownloadError) as ei:
        mm.download_model(MODEL, lambda _p: None)

    assert re.match(r"^Not enough disk space — free .* and try again\.$", str(ei.value))
    assert ollama.delete_calls == [MODEL]
    assert MODEL not in mm._active_downloads


# --------------------------------------------------------------------------
# 5. Unknown model — actionable error, no retry loop
# --------------------------------------------------------------------------


def test_unknown_model_fails_fast(app_cfg):
    streamer = FakePullStreamer(
        [[{"status": "error", "error": "pull model manifest: file does not exist"}]]
    )
    mm, ollama = make_mm(streamer, app_cfg)

    with pytest.raises(ModelDownloadError) as ei:
        mm.download_model("bogus:1b", lambda _p: None)

    assert "known model" in str(ei.value)
    assert streamer.calls == 1  # no restart / resume loop


# --------------------------------------------------------------------------
# 6. verify_model_integrity
# --------------------------------------------------------------------------


def test_verify_model_integrity(app_cfg):
    mm_ok, _ = make_mm(
        FakePullStreamer([]), app_cfg, ollama=FakeOllama(installed={MODEL}, show_ok=True)
    )
    assert mm_ok.verify_model_integrity(MODEL) is True

    mm_absent, _ = make_mm(FakePullStreamer([]), app_cfg, ollama=FakeOllama(installed=set()))
    assert mm_absent.verify_model_integrity(MODEL) is False

    mm_noshow, _ = make_mm(
        FakePullStreamer([]), app_cfg, ollama=FakeOllama(installed={MODEL}, show_ok=False)
    )
    assert mm_noshow.verify_model_integrity(MODEL) is False


# --------------------------------------------------------------------------
# 7. switch_active_model
# --------------------------------------------------------------------------


def test_switch_active_model_writes_app_config(app_cfg):
    from src.models.app_config import AppConfig

    mm, _ = make_mm(
        FakePullStreamer([]), app_cfg, ollama=FakeOllama(installed={MODEL}, show_ok=True)
    )
    mm.switch_active_model(MODEL)
    assert AppConfig.load(str(app_cfg)).active_model == MODEL


def test_switch_active_model_rejects_uninstalled(app_cfg):
    mm, _ = make_mm(FakePullStreamer([]), app_cfg, ollama=FakeOllama(installed=set()))
    with pytest.raises(ModelDownloadError):
        mm.switch_active_model("gemma2:9b")


# --------------------------------------------------------------------------
# 8. get_model_catalog passthrough
# --------------------------------------------------------------------------


def test_get_model_catalog_passthrough(app_cfg):
    mm, _ = make_mm(FakePullStreamer([]), app_cfg)
    assert [e.name for e in mm.get_model_catalog()] == [e.name for e in load_model_catalog()]


# --------------------------------------------------------------------------
# 9. Progress math across multiple layers
# --------------------------------------------------------------------------


def test_progress_percent_sums_across_layers(app_cfg):
    streamer = FakePullStreamer(
        [
            [
                dl(0, 100, digest="l1"),
                dl(0, 300, digest="l2"),
                dl(50, 100, digest="l1"),  # 50 / 400
                dl(100, 100, digest="l1"),
                dl(300, 300, digest="l2"),  # 400 / 400
                {"status": "success"},
            ]
        ]
    )
    mm, _ = make_mm(streamer, app_cfg)
    events: list[DownloadProgress] = []
    mm.download_model(MODEL, events.append)

    downloads = [e for e in events if e.phase == "downloading"]
    assert downloads[2].percent == 12.5
    assert downloads[-1].percent == 100.0
