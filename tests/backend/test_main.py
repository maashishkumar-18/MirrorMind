"""Backend entrypoint composition (Phase 3 Step 3.1a).

Mostly in-process — ``main`` takes injectable streams — with one real
``python -m src.backend.main`` subprocess smoke.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import src.backend.main as main_mod
from db.health import IntegrityResult
from src.security.errors import PreviousDataUnrecoverableError

KEY = "a" * 64


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAGPIPE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAGPIPE_DB_KEY", KEY)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    monkeypatch.delenv("OLLAMA_DEFAULT_MODEL", raising=False)


def _run(*messages: dict) -> tuple[int, list[dict]]:
    payload = b"".join((json.dumps(m) + "\n").encode() for m in messages)
    out = io.BytesIO()
    rc = main_mod.main([], in_stream=io.BytesIO(payload), out_stream=out)
    envelopes = [json.loads(line) for line in out.getvalue().decode().splitlines()]
    return rc, envelopes


def _req(method: str, rid: str, params: dict | None = None, version: int = 1) -> dict:
    return {
        "version": version,
        "message_type": "request",
        "request_id": rid,
        "timestamp": "2026-09-08T00:00:00Z",
        "payload": {"method": method, "params": params or {}},
    }


def _payloads(envs, message_type, method=None):
    out = []
    for e in envs:
        if e["message_type"] != message_type:
            continue
        if method is not None and e["payload"].get("method") != method:
            continue
        out.append(e["payload"])
    return out


def test_happy_path_ready_status_shutdown():
    rc, envs = _run(
        _req("app.status", "r1"), _req("model.catalog", "r2"), _req("app.shutdown", "z")
    )
    assert rc == 0

    ready = _payloads(envs, "event", "app.ready")
    assert ready and ready[0]["params"]["model_setup_required"] is True

    status = _payloads(envs, "response", "app.status")
    assert status[0]["result"]["ipc_version"] == 1

    assert _payloads(envs, "response", "model.catalog")[0]["result"]["models"]
    assert _payloads(envs, "response", "app.shutdown")[0]["result"] == {"stopping": True}


def test_version_mismatch_does_not_crash():
    rc, envs = _run(_req("app.status", "r1", version=999), _req("app.shutdown", "z"))
    assert rc == 0
    errs = _payloads(envs, "error")
    assert errs[0]["code"] == "version_mismatch"


def test_eof_without_shutdown_still_exits_clean():
    rc, envs = _run(_req("app.status", "r1"))
    assert rc == 0
    assert _payloads(envs, "response", "app.status")


def test_integrity_failure_enters_degraded_mode(monkeypatch):
    monkeypatch.setattr(
        main_mod, "check_integrity", lambda _conn: IntegrityResult(ok=False, details=["bad page"])
    )
    rc, envs = _run(
        _req("app.status", "r1"),
        _req("health.check", "r2"),  # not on the degraded whitelist
        _req("app.shutdown", "z"),
    )
    assert rc == 0
    assert _payloads(envs, "event", "app.integrity_failed")[0]["params"]["details"] == ["bad page"]

    status = _payloads(envs, "response", "app.status")[0]
    assert status["result"]["degraded"] is True and status["result"]["ready"] is False

    blocked = _payloads(envs, "error")
    assert blocked and blocked[0]["code"] == "integrity_failed"


def test_previous_data_unrecoverable_exits_3(monkeypatch):
    def _raise(_path):
        raise PreviousDataUnrecoverableError()

    monkeypatch.setattr(main_mod, "resolve_db_key", _raise)
    rc, envs = _run(_req("app.status", "r1"))
    assert rc == 3
    ev = _payloads(envs, "event", "app.previous_data_unrecoverable")[0]
    assert ev["params"]["message"] == PreviousDataUnrecoverableError.MESSAGE


def test_second_instance_exits_quietly(monkeypatch):
    monkeypatch.setattr(main_mod.SingleInstanceGuard, "acquire", lambda self: False)
    rc, envs = _run(_req("app.status", "r1"))
    assert rc == 0
    assert _payloads(envs, "error")[0]["code"] == "already_running"


def test_backup_restore_stages_and_exits_5(tmp_path):
    # first run: create a backup we can then "restore"
    rc, _ = _run(_req("app.shutdown", "z"))
    assert rc == 0
    from src.features.backup_manager import BackupManager

    snap = BackupManager(str(tmp_path / "session.db"), key=KEY).create_backup()

    rc, envs = _run(_req("backup.restore", "r1", {"path": str(snap.path)}))
    assert rc == 5
    staged = _payloads(envs, "event", "app.restore_staged")
    assert staged and staged[0]["params"]["validated_snapshot_path"]
    resp = _payloads(envs, "response", "backup.restore")[0]["result"]
    assert resp["ok"] is True and resp["needs_restart"] is True


@pytest.mark.integration
def test_subprocess_smoke(tmp_path):
    env = os.environ.copy()
    env.update(
        RAGPIPE_DATA_DIR=str(tmp_path),
        RAGPIPE_DB_KEY=KEY,
        LANGFUSE_PUBLIC_KEY="",
        LANGFUSE_SECRET_KEY="",
        HF_HUB_OFFLINE="1",
    )
    stdin = (
        json.dumps(_req("app.status", "r1")) + "\n" + json.dumps(_req("app.shutdown", "z")) + "\n"
    )
    proc = subprocess.run(
        [sys.executable, "-m", "src.backend.main"],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parents[2]),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    assert any(e["payload"].get("method") == "app.ready" for e in lines)
    assert any(
        e["message_type"] == "response" and e["payload"].get("method") == "app.status"
        for e in lines
    )
