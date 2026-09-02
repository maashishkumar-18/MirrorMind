"""
Cross-language IPC envelope round-trip proof (Phase 0 Step 0.1.5).

This is a genuine pipeline, not two parallel same-shape assertions:
Pydantic validates a fixture envelope, re-serializes it, the exact
serialized JSON is piped into the zod validator (ipc/schema/validate_stdin.ts)
in a real Node subprocess, and zod's own re-serialization is compared back.
If either schema silently coerces, drops, or adds a field, this fails.

Skipped (not failed) when Node isn't available locally — the pure-Pydantic
contract is still covered by test_ipc_envelope.py without this dependency.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.common.ipc.envelope import IPCEnvelope

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
IPC_DIR = REPO_ROOT / "ipc"
FIXTURES_PATH = IPC_DIR / "fixtures" / "envelope_examples.json"
INVALID_FIXTURES_PATH = IPC_DIR / "fixtures" / "envelope_invalid.json"


def _load_examples():
    with open(FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_invalid_examples():
    with open(INVALID_FIXTURES_PATH, encoding="utf-8") as f:
        return json.load(f)


NODE_AVAILABLE = shutil.which("node") is not None


def _run_zod_validator(payload) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "tsx", "schema/validate_stdin.ts"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(IPC_DIR),  # so npx resolves tsx from ipc/node_modules/.bin, not the repo root
        shell=True,  # npx.cmd resolution on Windows requires the shell
        timeout=30,
    )


@pytest.mark.skipif(
    not NODE_AVAILABLE, reason="Node.js not available for cross-language IPC round-trip"
)
@pytest.mark.parametrize("fixture", _load_examples())
def test_pydantic_to_zod_roundtrip(fixture):
    envelope = IPCEnvelope.model_validate(fixture)
    dumped = envelope.model_dump(mode="json")

    result = _run_zod_validator(dumped)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dumped


@pytest.mark.skipif(
    not NODE_AVAILABLE, reason="Node.js not available for cross-language IPC round-trip"
)
@pytest.mark.parametrize("case", _load_invalid_examples(), ids=lambda c: c["description"])
def test_invalid_envelope_rejected_by_both_pydantic_and_zod(case):
    """
    Cross-language rejection symmetry: every malformed envelope in
    envelope_invalid.json must be rejected by BOTH schemas, not just
    validated identically when well-formed. Wires up the fixture that
    was previously committed but never actually consumed by any test —
    found during a Phase 0 audit — using the same real Node subprocess
    mechanism as the round-trip proof above, not a duplicated hardcoded
    case list.
    """
    envelope = case["envelope"]

    with pytest.raises(ValidationError):
        IPCEnvelope.model_validate(envelope)

    result = _run_zod_validator(envelope)
    assert (
        result.returncode != 0
    ), f"zod accepted an envelope Pydantic rejected: {case['description']}"
