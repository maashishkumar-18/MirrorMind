"""IPC handlers against injected fakes (Phase 3 Step 3.1a)."""

from __future__ import annotations

import pytest

from src.backend import handlers
from src.backend.wire import MethodError
from src.common.ipc.methods import (
    AppStatusParams,
    BackupListParams,
    BackupRestoreParams,
    HealthCheckParams,
    ModelActivateParams,
    ModelCatalogParams,
    ModelDownloadParams,
    ModelStatusParams,
)
from src.models.app_config import AppConfig
from tests.backend.conftest import FakeModelManager, FakeOllama


def test_app_status_first_launch(ctx_factory, tmp_path):
    cfg_path = str(tmp_path / "app_config.json")
    ctx = ctx_factory(app_config_path=cfg_path)
    res = handlers.HANDLERS["app.status"](AppStatusParams(), ctx, "r")
    assert res.model_setup_required is True
    assert res.active_model is None
    assert res.ready is True and res.degraded is False


def test_app_status_after_activation(ctx_factory, tmp_path):
    cfg_path = str(tmp_path / "app_config.json")
    cfg = AppConfig.load(cfg_path)
    cfg.set_active_model("llama3.1:8b")
    cfg.set_last_exported_at("2026-09-01T00:00:00Z")

    ctx = ctx_factory(app_config_path=cfg_path)
    res = handlers.HANDLERS["app.status"](AppStatusParams(), ctx, "r")
    assert res.model_setup_required is False
    assert res.active_model == "llama3.1:8b"
    assert res.last_exported_at == "2026-09-01T00:00:00Z"


def test_health_check_ok_on_fresh_db(ctx_factory):
    ctx = ctx_factory()
    res = handlers.HANDLERS["health.check"](HealthCheckParams(), ctx, "r")
    assert res.ok is True and res.details == ["ok"]


def test_model_catalog(ctx_factory):
    ctx = ctx_factory(models=FakeModelManager())
    res = handlers.HANDLERS["model.catalog"](ModelCatalogParams(), ctx, "r")
    assert {m.name for m in res.models} == {"llama3.1:8b", "qwen2.5:7b"}
    assert next(m for m in res.models if m.name == "llama3.1:8b").recommended is True


def test_model_status_reports_installed_and_statuses(ctx_factory):
    ollama = FakeOllama(running=True, installed={"llama3.1:8b": 42})
    ctx = ctx_factory(ollama=ollama, models=FakeModelManager(installed={"llama3.1:8b"}))
    res = handlers.HANDLERS["model.status"](ModelStatusParams(), ctx, "r")
    assert res.ollama_running is True
    assert res.installed[0].name == "llama3.1:8b"
    assert res.statuses["llama3.1:8b"] == "available"
    assert res.statuses["qwen2.5:7b"] == "not_installed"


def test_model_download_streams_progress_then_result(ctx_factory):
    events: list[tuple[str, dict, str]] = []
    ctx = ctx_factory(models=FakeModelManager())
    ctx.emit_event = lambda m, p, r: events.append((m, p, r))

    res = handlers.HANDLERS["model.download"](ModelDownloadParams(name="qwen2.5:7b"), ctx, "rid9")
    assert res.model_name == "qwen2.5:7b"
    assert [e[0] for e in events] == ["model.download.progress"] * 3
    assert all(e[2] == "rid9" for e in events)
    assert events[-1][1]["phase"] == "complete"


def test_model_download_failure_raises_method_error(ctx_factory):
    ctx = ctx_factory(models=FakeModelManager(download_error="Not enough disk space"))
    ctx.emit_event = lambda *a: None
    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["model.download"](ModelDownloadParams(name="qwen2.5:7b"), ctx, "r")
    assert ei.value.code == "model_download_failed"
    assert "disk space" in ei.value.message


def test_model_activate_not_installed(ctx_factory):
    ctx = ctx_factory(models=FakeModelManager(installed=set()))
    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["model.activate"](ModelActivateParams(name="qwen2.5:7b"), ctx, "r")
    assert ei.value.code == "model_not_installed"


def test_model_activate_success(ctx_factory, tmp_path):
    cfg_path = str(tmp_path / "app_config.json")
    mm = FakeModelManager(installed={"llama3.1:8b"})
    ctx = ctx_factory(models=mm, app_config_path=cfg_path)
    res = handlers.HANDLERS["model.activate"](ModelActivateParams(name="llama3.1:8b"), ctx, "r")
    assert res.active_model == "llama3.1:8b" and res.verified is True
    assert mm.activated == "llama3.1:8b"


def test_backup_list_and_stage_restore_roundtrip(ctx_factory):
    ctx = ctx_factory()
    snap = ctx.backups.create_backup()
    listed = handlers.HANDLERS["backup.list"](BackupListParams(), ctx, "r")
    assert [b.path for b in listed.backups] == [str(snap.path)]

    staged: list = []
    ctx.emit_event = lambda m, p, r: staged.append((m, p))
    ctx.request_restart = lambda path: staged.append(("restart", path))
    res = handlers.HANDLERS["backup.restore"](BackupRestoreParams(path=str(snap.path)), ctx, "r")
    assert res.ok and res.needs_restart
    assert res.validated_snapshot_path is not None
    assert staged[0][0] == "app.restore_staged"
    assert staged[1][0] == "restart"


def test_backup_restore_invalid_snapshot(ctx_factory, tmp_path):
    ctx = ctx_factory()
    bad = tmp_path / "nope.db"
    bad.write_bytes(b"not a database")
    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["backup.restore"](BackupRestoreParams(path=str(bad)), ctx, "r")
    assert ei.value.code == "restore_invalid"
