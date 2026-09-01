"""
Pure Pydantic-side tests for the IPC envelope (Phase 0 Step 0.1.5). No
subprocess/Node dependency — covers the Python contract in isolation so a
local run without Node still exercises it. See
test_ipc_envelope_roundtrip.py for the real cross-language proof.
"""

import pytest
from pydantic import ValidationError

from src.common.ipc.envelope import CURRENT_IPC_VERSION, IPCEnvelope, IPCMessageType
from src.common.ipc.middleware import IPCVersionMismatchError, check_version

pytestmark = pytest.mark.unit

VALID_ENVELOPE = {
    "version": CURRENT_IPC_VERSION,
    "message_type": "request",
    "request_id": "req-1",
    "timestamp": "2026-09-01T00:00:00Z",
    "payload": {"foo": "bar"},
}


def test_accepts_a_well_formed_envelope():
    envelope = IPCEnvelope.model_validate(VALID_ENVELOPE)
    assert envelope.version == CURRENT_IPC_VERSION
    assert envelope.message_type == IPCMessageType.REQUEST
    assert envelope.payload == {"foo": "bar"}


def test_defaults_payload_to_empty_dict_when_omitted():
    raw = {k: v for k, v in VALID_ENVELOPE.items() if k != "payload"}
    envelope = IPCEnvelope.model_validate(raw)
    assert envelope.payload == {}


def test_defaults_version_to_current_when_omitted():
    raw = {k: v for k, v in VALID_ENVELOPE.items() if k != "version"}
    envelope = IPCEnvelope.model_validate(raw)
    assert envelope.version == CURRENT_IPC_VERSION


@pytest.mark.parametrize("message_type", ["request", "response", "event", "error"])
def test_accepts_every_message_type(message_type):
    envelope = IPCEnvelope.model_validate({**VALID_ENVELOPE, "message_type": message_type})
    assert envelope.message_type.value == message_type


def test_rejects_an_unknown_message_type():
    with pytest.raises(ValidationError):
        IPCEnvelope.model_validate({**VALID_ENVELOPE, "message_type": "not_a_type"})


def test_rejects_a_missing_message_type():
    raw = {k: v for k, v in VALID_ENVELOPE.items() if k != "message_type"}
    with pytest.raises(ValidationError):
        IPCEnvelope.model_validate(raw)


def test_rejects_a_missing_request_id():
    raw = {k: v for k, v in VALID_ENVELOPE.items() if k != "request_id"}
    with pytest.raises(ValidationError):
        IPCEnvelope.model_validate(raw)


def test_model_dump_json_mode_serializes_enum_to_plain_string():
    envelope = IPCEnvelope.model_validate(VALID_ENVELOPE)
    dumped = envelope.model_dump(mode="json")
    assert dumped["message_type"] == "request"
    assert isinstance(dumped["message_type"], str)


class TestCheckVersion:
    def test_returns_the_envelope_when_the_version_matches(self):
        envelope = check_version(VALID_ENVELOPE, expected=CURRENT_IPC_VERSION)
        assert envelope.version == CURRENT_IPC_VERSION

    def test_raises_version_mismatch_when_the_version_differs(self):
        with pytest.raises(IPCVersionMismatchError):
            check_version(VALID_ENVELOPE, expected=CURRENT_IPC_VERSION + 1)

    def test_raises_validation_error_not_version_mismatch_for_a_malformed_envelope(self):
        with pytest.raises(ValidationError):
            check_version({"not": "an envelope"})
