"""
IPC version-check middleware. Fails loudly on a version mismatch rather
than letting a stale frontend/backend pair silently misbehave — the
frontend routes IPCVersionMismatchError to a "please restart" UI state
(Production Roadmap Phase 3 Step 3.1).
"""

from typing import Any

from src.common.ipc.envelope import CURRENT_IPC_VERSION, IPCEnvelope


class IPCVersionMismatchError(Exception):
    """Raised when an incoming envelope's version doesn't match what this
    process expects. Distinct from pydantic's ValidationError (malformed
    envelope) — this is a well-formed envelope at the wrong version."""


def check_version(raw: dict[str, Any], expected: int = CURRENT_IPC_VERSION) -> IPCEnvelope:
    """
    Validate `raw` as an IPCEnvelope and enforce it matches `expected`.

    Raises pydantic.ValidationError if `raw` doesn't parse as a valid
    envelope at all, or IPCVersionMismatchError if it parses but its
    `version` field doesn't match `expected`.
    """
    envelope = IPCEnvelope.model_validate(raw)
    if envelope.version != expected:
        raise IPCVersionMismatchError(
            f"IPC version mismatch: got {envelope.version}, expected {expected}"
        )
    return envelope
