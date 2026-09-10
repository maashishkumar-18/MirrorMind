"""
Cross-language IPC **method contract** round-trip proof (Phase 3 Step 3.1-fe.2).

The sibling of ``test_ipc_envelope_roundtrip.py``: for every method's
``params``/``result`` and every lifecycle/streaming event, a fixture payload is
validated by the Pydantic model in ``src.common.ipc.methods``, re-serialised,
piped into the zod validator (``ipc/schema/validate_methods_stdin.ts``) in a real
Node subprocess, and zod's canonical output is compared back. If either schema
silently coerces, drops, adds, or retypes a field, this fails.

Imports only ``src.common.ipc.methods`` (pydantic-only) — no backend / tracing
modules, so the ``conftest.py`` empty-Langfuse-creds guard is untouched.

Skipped (not failed) when Node isn't available locally.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from src.common.ipc import methods as m

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
IPC_DIR = REPO_ROOT / "ipc"
FIXTURES_PATH = IPC_DIR / "fixtures" / "methods_examples.json"

NODE_AVAILABLE = shutil.which("node") is not None

EVENT_MODELS: dict[str, type[BaseModel]] = {
    "app.ready": m.AppReadyEvent,
    "app.integrity_failed": m.AppIntegrityFailedEvent,
    "app.previous_data_unrecoverable": m.AppPreviousDataUnrecoverableEvent,
    "app.restore_staged": m.AppRestoreStagedEvent,
    "app.reminders_pending": m.AppRemindersPendingEvent,
    "model.download.progress": m.ModelDownloadProgressEvent,
    "toast.register": m.ToastRegisterEvent,
    "toast.cancel": m.ToastCancelEvent,
    "toast.fire": m.ToastFireEvent,
    "toast.cancel_all": m.ToastCancelAllEvent,
}


def _load_fixtures() -> list[dict]:
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _model_for(target: str) -> type[BaseModel]:
    kind, _, name = target.partition(":")
    if kind == "event":
        return EVENT_MODELS[name]
    contract = m.METHOD_CONTRACTS[name]
    return contract.params if kind == "params" else contract.result


def _run_zod_validator(target: str, payload: object) -> subprocess.CompletedProcess:
    # One `npx tsx` cold-start per fixture — under memory pressure (torch loaded
    # by earlier test files in a full run) the spawn itself can transiently
    # fail. Retry a couple of times before treating it as a real mismatch.
    payload_json = json.dumps({"target": target, "payload": payload})
    last: subprocess.CompletedProcess | None = None
    for attempt in range(3):
        proc = subprocess.run(
            ["npx", "tsx", "schema/validate_methods_stdin.ts"],
            input=payload_json,
            capture_output=True,
            text=True,
            cwd=str(IPC_DIR),
            shell=True,  # npx.cmd resolution on Windows requires the shell
            timeout=60,
        )
        if proc.returncode == 0:
            return proc
        last = proc
        time.sleep(1.5 * (attempt + 1))
    assert last is not None
    return last


def test_every_contract_and_event_has_a_fixture() -> None:
    targets = {fx["target"] for fx in _load_fixtures()}
    for name in m.METHOD_CONTRACTS:
        assert f"params:{name}" in targets, f"missing params fixture for {name}"
        assert f"result:{name}" in targets, f"missing result fixture for {name}"
    for name in EVENT_MODELS:
        assert f"event:{name}" in targets, f"missing event fixture for {name}"


@pytest.mark.skipif(
    not NODE_AVAILABLE, reason="Node.js not available for cross-language round-trip"
)
@pytest.mark.parametrize(
    "fixture",
    _load_fixtures(),
    ids=lambda fx: f"{fx['target']}",
)
def test_pydantic_to_zod_roundtrip(fixture: dict) -> None:
    model = _model_for(fixture["target"])
    canonical = model.model_validate(fixture["payload"]).model_dump(mode="json")

    result = _run_zod_validator(fixture["target"], canonical)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == canonical


@pytest.mark.skipif(
    not NODE_AVAILABLE, reason="Node.js not available for cross-language round-trip"
)
def test_extra_field_rejected_by_both_pydantic_and_zod() -> None:
    """An unknown key must be rejected by BOTH schemas — `extra="forbid"` on
    the Python side, `.strict()` on the zod side."""
    target = "result:app.status"
    good = (
        _model_for(target)
        .model_validate(
            {
                "ipc_version": 1,
                "model_setup_required": True,
                "active_model": None,
                "last_exported_at": None,
                "degraded": False,
                "ready": True,
            }
        )
        .model_dump(mode="json")
    )
    tampered = {**good, "unexpected": 1}

    with pytest.raises(ValidationError):
        _model_for(target).model_validate(tampered)

    result = _run_zod_validator(target, tampered)
    assert result.returncode != 0, "zod accepted a field Pydantic rejected"
