"""
Versioned IPC envelope schema — the base message contract between the
Python backend and the Tauri/React frontend (Production Roadmap Phase 0
Step 0.1). Frozen before any frontend or backend IPC work begins in later
phases.

The corresponding zod schema lives at ipc/schema/envelope.ts and mirrors
this module field-for-field. The two are kept in sync by a real
cross-language round-trip test — see tests/common/test_ipc_envelope_roundtrip.py —
not by convention alone.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

CURRENT_IPC_VERSION = 1


class IPCMessageType(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"
    ERROR = "error"


class IPCEnvelope(BaseModel):
    """
    The envelope every IPC message is wrapped in, on both sides of the
    Tauri bridge. `version` is the explicit discriminator the version-check
    middleware inspects; `message_type` discriminates request/response/
    event/error framing within a given version.
    """

    # strict=True: Pydantic v2's default "lax" mode silently coerces a
    # numeric-looking string ("1") to int -- found during a Phase 0 audit
    # via the cross-language rejection test, which showed zod's plain
    # z.number() correctly rejects the same input Pydantic was quietly
    # accepting. version is the version-check middleware's discriminator;
    # this schema's whole design point is failing loudly on mismatch, so
    # the two schemas must agree on what counts as a valid version, not
    # just on what a syntactically-correct one round-trips as.
    version: int = Field(default=CURRENT_IPC_VERSION, strict=True)
    message_type: IPCMessageType
    request_id: str
    timestamp: str  # ISO 8601
    payload: dict[str, Any] = Field(default_factory=dict)
