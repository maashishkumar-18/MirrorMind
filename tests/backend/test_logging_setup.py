"""Rotating backend log file (Phase 3 Step 3.4a)."""

from __future__ import annotations

import logging

import pytest

from src.backend import paths
from src.backend.logging_setup import _MARKER, configure_logging


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGPIPE_DATA_DIR", str(tmp_path))
    yield
    # leave the root logger clean for the next test
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _MARKER, False)]:
        root.removeHandler(handler)
        handler.close()


def test_configure_logging_creates_the_file_on_first_emit(tmp_path):
    configure_logging()
    logging.getLogger("backend.test").info("hello from a unit test")

    log_path = tmp_path / "logs" / "backend.log"
    assert log_path.exists()
    assert "hello from a unit test" in log_path.read_text(encoding="utf-8")


def test_configure_logging_is_idempotent_and_does_not_stack_handlers():
    configure_logging()
    configure_logging()
    configure_logging()

    root = logging.getLogger()
    marked = [h for h in root.handlers if getattr(h, _MARKER, False)]
    assert len(marked) == 2  # one stderr, one rotating file


def test_configure_logging_repoints_after_a_data_dir_change(tmp_path, monkeypatch):
    configure_logging()
    logging.getLogger("backend.test").info("first dir")

    second = tmp_path / "second"
    monkeypatch.setenv("RAGPIPE_DATA_DIR", str(second))
    configure_logging()
    logging.getLogger("backend.test").info("second dir")

    assert (second / "logs" / "backend.log").read_text(encoding="utf-8").count("second dir") == 1


def test_rotation_wraps_at_the_size_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr("src.backend.logging_setup._MAX_BYTES", 2_000)
    monkeypatch.setattr("src.backend.logging_setup._BACKUP_COUNT", 2)
    configure_logging()

    log = logging.getLogger("backend.test")
    for i in range(400):
        log.info("padding line number %d with some extra text to grow the file", i)

    files = sorted((tmp_path / "logs").glob("backend.log*"))
    assert (tmp_path / "logs" / "backend.log") in files
    assert any(p.suffix == ".1" for p in files)  # at least one rotation happened


def test_bad_data_dir_does_not_raise(tmp_path, monkeypatch):
    # point the log dir at a path that cannot be created (a file where a dir is expected)
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    monkeypatch.setattr(paths, "log_dir", lambda: blocker / "logs")
    configure_logging()  # must not raise
    logging.getLogger("backend.test").info("still alive")
