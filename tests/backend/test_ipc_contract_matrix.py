"""IPC contract verification (Phase 4 Step 4.2).

Every method in ``METHOD_CONTRACTS`` is exercised through the real
``Dispatcher`` with both a valid payload (→ a ``response`` whose result
validates against the ``*Result`` model) and invalid payloads (extra field /
missing required / wrong type → a ``validation_error`` frame, never a crash).
Plus the cross-cutting envelope failures as separate cases: non-JSON,
malformed envelope, unknown method, and version N vs N+1 (→ ``version_mismatch``).

Canonical valid params come from ``ipc/fixtures/methods_examples.json`` — the
same fixtures the cross-language round-trip test uses — so this test and the zod
mirror can't drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.backend.wire import make_error
from src.common.ipc.envelope import CURRENT_IPC_VERSION
from src.common.ipc.methods import METHOD_CONTRACTS
from tests.backend.conftest import FakeModelManager, FakeSessionWorker, envelope

REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = json.loads(
    (REPO_ROOT / "ipc" / "fixtures" / "methods_examples.json").read_text(encoding="utf-8")
)

# First `params:<method>` example per method.
_PARAMS: dict[str, dict] = {}
for _entry in _EXAMPLES:
    _kind, _, _name = _entry["target"].partition(":")
    if _kind == "params" and _name not in _PARAMS:
        _PARAMS[_name] = _entry["payload"]

ALL_METHODS = sorted(METHOD_CONTRACTS)

# Methods whose *handler* has an external precondition (a writable path, an
# installed model, a staged snapshot) or drives process exit — their happy path
# is covered by test_handlers.py / test_main.py. 4.2 still checks their
# params/version contract in the invalid + version tests below.
_SKIP_VALID = {
    "app.shutdown",  # sets the shutdown flag
    "backup.restore",  # stages a file, exit 5
    "data.export",  # writes to a user-chosen path
    "data.wipe",  # destructive
    "model.activate",  # needs an installed model
    "diagnostics.report",  # writes to a user-chosen path
}


def _worker_for(method: str):
    return FakeSessionWorker() if METHOD_CONTRACTS[method].worker else None


def _one_shot(dispatcher_factory, method: str, *, params, version=CURRENT_IPC_VERSION, **ctx):
    """Run one envelope through a fresh dispatcher and drain the executor."""
    d, transport, _ = dispatcher_factory(worker=_worker_for(method), **ctx)
    d.handle_raw(envelope(method, params=params, version=version))
    d.close(wait=True)  # drain the ThreadPoolExecutor before asserting
    return d, transport


def test_every_method_has_a_params_fixture():
    assert [m for m in ALL_METHODS if m not in _PARAMS] == []


@pytest.mark.parametrize("method", [m for m in ALL_METHODS if m not in _SKIP_VALID])
def test_valid_payload_produces_a_validating_response(method, dispatcher_factory):
    models = FakeModelManager(installed={"llama3.1:8b", "qwen2.5:7b"})
    d, transport = _one_shot(dispatcher_factory, method, params=_PARAMS[method], models=models)

    assert not transport.by_type("error"), transport.by_type("error")
    responses = transport.by_type("response")
    assert len(responses) == 1, transport.sent
    METHOD_CONTRACTS[method].result.model_validate(responses[0]["payload"]["result"])
    assert not d.shutdown_requested.is_set()


@pytest.mark.parametrize("method", ALL_METHODS)
def test_invalid_params_produce_validation_error_not_crash(method, dispatcher_factory):
    fields = METHOD_CONTRACTS[method].params.model_fields
    example = _PARAMS[method]

    bad_payloads: list[dict] = [{**example, "__nope__": 1}]  # extra="forbid"
    for name, f in fields.items():
        if f.is_required() and name in example:
            bad_payloads.append({k: v for k, v in example.items() if k != name})  # missing
            wrong = 12345 if not isinstance(example[name], int | float) else "not-a-number"
            bad_payloads.append({**example, name: wrong})  # wrong type
            break

    for bad in bad_payloads:
        d, transport = _one_shot(dispatcher_factory, method, params=bad)
        errors = transport.by_type("error")
        assert errors, f"{method}: {bad} should have errored"
        assert (
            errors[0]["payload"]["code"] == "validation_error"
        ), f"{method}: {bad} → {errors[0]['payload']}"
        assert not d.shutdown_requested.is_set()


def test_unknown_method_is_rejected(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw(envelope("does.not.exist"))
    d.close(wait=True)
    assert transport.by_type("error")[0]["payload"]["code"] == "unknown_method"


def test_non_json_line_surfaces_as_none_then_validation_error(dispatcher_factory):
    # A non-JSON stdin line comes through as `None`; the dispatcher turns that
    # into a validation_error and the loop survives (never a crash).
    import io

    from src.backend.transport import StdioTransport

    t = StdioTransport(io.BytesIO(b'not json\n{"version":1}\n'), io.BytesIO())
    assert list(t.read_messages()) == [None, {"version": 1}]

    d, transport, _ = dispatcher_factory()
    d.handle_raw(None)
    d.handle_raw(envelope("app.status"))
    d.close(wait=True)
    assert transport.by_type("error")[0]["payload"]["code"] == "validation_error"
    assert transport.by_type("response")  # the next message still processed


def test_none_line_is_rejected(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw(None)
    d.close(wait=True)
    assert transport.by_type("error")[0]["payload"]["code"] == "validation_error"


def test_malformed_envelope_missing_method_is_validation_error(dispatcher_factory):
    d, transport, _ = dispatcher_factory()
    d.handle_raw(
        {
            "version": CURRENT_IPC_VERSION,
            "message_type": "request",
            "request_id": "r1",
            "timestamp": "2026-09-08T00:00:00Z",
            "payload": {"params": {}},  # no "method"
        }
    )
    d.close(wait=True)
    assert transport.by_type("error")[0]["payload"]["code"] in {
        "validation_error",
        "unknown_method",
    }


@pytest.mark.parametrize("method", ["app.status", "chat.send", "model.catalog", "reminders.list"])
def test_version_n_plus_1_produces_version_mismatch(method, dispatcher_factory):
    d, transport = _one_shot(
        dispatcher_factory, method, params=_PARAMS[method], version=CURRENT_IPC_VERSION + 1
    )
    assert transport.by_type("error")[0]["payload"]["code"] == "version_mismatch"
    assert not d.shutdown_requested.is_set()


def test_make_error_shape_is_stable():
    # the frame the frontend's client.ts keys "please restart" off
    frame = make_error("r1", "version_mismatch", "got 2, expected 1")
    assert frame["message_type"] == "error"
    assert frame["payload"] == {"code": "version_mismatch", "message": "got 2, expected 1"}
